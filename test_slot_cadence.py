"""Integration tests for the engine run cycle and the publisher's queue hooks.

These cover the behaviour the old clock-based slot lookup could not express:
which slot a run takes, what happens to the queue when generation or publishing
fails, and that a near-duplicate caption is regenerated instead of posted.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from bot import main_signal, schedule, square_publisher


class TempStateMixin:
    """Point every state file the engine and the queue touch at a temp dir."""

    def make_temp_state(self) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="slot-run-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "data").mkdir()
        self.data_dir = tmp / "data"
        patches = [
            mock.patch.object(schedule, "ROOT_DIR", tmp),
            mock.patch.object(schedule, "CONFIG_FILE", Path("bot/config.json")),
            mock.patch.object(schedule, "STATE_FILE", self.data_dir / "schedule_state.json"),
            mock.patch.object(schedule, "PENDING_RUN_FILE", self.data_dir / "pending_run.json"),
            mock.patch.object(schedule, "PUBLISH_RECEIPT_FILE", self.data_dir / "publish_receipt.json"),
            mock.patch.object(schedule, "LEGACY_SLOT_STATE_FILE", self.data_dir / "slot_state.json"),
            mock.patch.object(schedule, "ACTIVE_TRADES_FILE", self.data_dir / "active_trades.json"),
            mock.patch.object(schedule, "POSTED_COINS_FILE", self.data_dir / "posted_coins.json"),
            mock.patch.object(main_signal, "ACTIVE_FILE", str(self.data_dir / "active_trades.json")),
            mock.patch.object(main_signal, "POSTED_FILE", str(self.data_dir / "posted_coins.json")),
            mock.patch.object(main_signal, "LATEST_POST_FILE", str(self.data_dir / "latest_post.txt")),
            mock.patch.object(main_signal, "LATEST_SIGNAL_FILE", str(self.data_dir / "latest_signal.json")),
            mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}, clear=False),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return tmp

    def state(self) -> dict[str, Any]:
        return json.loads(schedule.STATE_FILE.read_text(encoding="utf-8"))


class EngineRunTestCase(unittest.TestCase, TempStateMixin):
    def setUp(self) -> None:
        self.make_temp_state()

    def test_meme_slot_writes_the_post_and_keeps_the_slot_in_flight(self) -> None:
        builder = mock.Mock(return_value=("🎭 meme text", {"type": "meme_community", "title": "t"}, "charts/MEME_CARD.png"))
        with mock.patch.dict(os.environ, {"SLOT": "meme_community"}), mock.patch.object(
            main_signal, "_with_duplicate_guard", builder
        ), mock.patch.object(main_signal, "validate_chart", return_value=True):
            main_signal.run()

        state = self.state()
        self.assertEqual(state["in_flight"]["type"], "meme_community")
        self.assertEqual(state["pending"][0], 0, "the auto queue waits for this run to publish")
        self.assertEqual(self.data_dir.joinpath("latest_post.txt").read_text(encoding="utf-8"), "🎭 meme text")

    def test_generation_failure_hands_the_slot_back(self) -> None:
        boom = RuntimeError("Groq returned an unreadable document")
        with mock.patch.dict(os.environ, {"SLOT": "auto"}), mock.patch.object(
            main_signal, "_with_duplicate_guard", side_effect=boom
        ):
            with self.assertRaises(RuntimeError):
                main_signal.run()

        state = self.state()
        self.assertIsNone(state["in_flight"])
        self.assertEqual(state["pending"][0], 0)
        self.assertIn("unreadable document", state["last_error"]["reason"])

    def test_preview_run_rolls_back_state_and_effects(self) -> None:
        effects = {"coin": "AMP", "trade_id": "abc", "posted_time": "now"}
        with mock.patch.dict(os.environ, {"SLOT": "rapid_alert", "PREVIEW_ONLY": "1"}), mock.patch.object(
            main_signal, "build_signal_post", return_value=("⚡ rapid", {"coin": "AMP"}, "charts/AMP.png", effects)
        ), mock.patch.object(main_signal, "record_posted", return_value="now"), mock.patch.object(
            main_signal, "save_active", return_value={"trade_id": "abc"}
        ):
            main_signal.run()

        state = self.state()
        self.assertEqual(state["in_flight"], None)
        self.assertEqual(state["pending"][0], 1, "a preview must not consume a slot")
        self.assertFalse((self.data_dir / "pending_run.json").exists())

    def test_signal_run_records_its_effects_for_the_publisher(self) -> None:
        effects = {"coin": "AMP", "trade_id": "abc-1", "posted_time": "2026-10-19T12:00:00+00:00"}
        with mock.patch.dict(os.environ, {"SLOT": "rapid_alert"}), mock.patch.object(
            main_signal, "build_signal_post", return_value=("⚡ rapid", {"coin": "AMP"}, "charts/AMP.png", effects)
        ):
            main_signal.run()

        pending = json.loads(schedule.PENDING_RUN_FILE.read_text(encoding="utf-8"))
        self.assertEqual(pending["coin"], "AMP")
        self.assertEqual(pending["trade_id"], "abc-1")
        self.assertEqual(pending["posted_time"], "2026-10-19T12:00:00+00:00")

    def test_invalid_slot_request_fails_before_any_generation(self) -> None:
        with mock.patch.dict(os.environ, {"SLOT": "hot-take"}), mock.patch.object(
            main_signal, "_with_duplicate_guard"
        ) as guard:
            with self.assertRaises(SystemExit) as ctx:
                main_signal.run()

        self.assertIn("Unknown slot request", str(ctx.exception))
        guard.assert_not_called()


class DuplicateGuardTestCase(unittest.TestCase):
    def test_similar_caption_is_regenerated_then_accepted(self) -> None:
        calls = {"n": 0}

        def builder(config):  # noqa: ANN001 - mirrors the engine builders
            calls["n"] += 1
            return f"post #{calls['n']}", {"n": calls["n"]}, "chart.png"

        verdicts = iter([False, True])
        with mock.patch.object(main_signal, "check_and_record_similarity", side_effect=lambda *a, **k: next(verdicts)):
            post, payload, _ = main_signal._with_duplicate_guard(builder, {"caption_regeneration_attempts": 3})

        self.assertEqual(post, "post #2")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(payload["n"], 2)

    def test_last_variant_is_accepted_instead_of_losing_the_slot(self) -> None:
        builder = mock.Mock(return_value=("same text", {}, "chart.png"))
        with mock.patch.object(main_signal, "check_and_record_similarity", return_value=False):
            post, _, _ = main_signal._with_duplicate_guard(builder, {"caption_regeneration_attempts": 2})

        self.assertEqual(post, "same text")
        self.assertEqual(builder.call_count, 2)

    def test_unique_caption_short_circuits(self) -> None:
        builder = mock.Mock(return_value=("fresh", {}, "chart.png"))
        with mock.patch.object(main_signal, "check_and_record_similarity", return_value=True) as checker:
            post, _, _ = main_signal._with_duplicate_guard(builder, {})

        self.assertEqual(post, "fresh")
        self.assertEqual(builder.call_count, 1)
        self.assertEqual(checker.call_args.kwargs["max_similarity"], 0.82)


class TrendingSelectionTestCase(unittest.TestCase, TempStateMixin):
    def setUp(self) -> None:
        self.make_temp_state()

    def test_thresholds_relax_until_a_coin_is_usable(self) -> None:
        """A quiet market still yields a verified setup instead of a dead slot.

        AMP is already open, so the 3% pool is unusable even though it is not
        empty, and selection has to drop to the next threshold.
        """

        seen: list[float] = []
        self.data_dir.joinpath("active_trades.json").write_text(
            json.dumps(
                [
                    {
                        "coin": "AMP",
                        "status": "ACTIVE",
                        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    }
                ]
            ),
            encoding="utf-8",
        )

        def fake_get_trending(*, top_n: int, min_change: float, min_volume: float):
            seen.append(min_change)
            if min_change > 3.0:
                raise RuntimeError("No valid trending coins found from verified API.")
            if min_change >= 1.5:
                # Usable pool, but every candidate is already an open setup.
                return [{"coin": "AMP", "change": min_change, "volume": 1, "price": 1}]
            return [{"coin": "TIA", "change": min_change, "volume": 1, "price": 1}]

        with mock.patch.object(main_signal, "get_trending_coins", side_effect=fake_get_trending):
            coin, _pool, threshold = main_signal.select_trending_coin(
                {
                    "trending": {
                        "top_n": 30,
                        "min_change_percent": 5,
                        "min_volume_usdt": 5_000_000,
                        "min_change_fallbacks": [3, 1.5, 0.5],
                    },
                    "duplicate_filter_hours": 24,
                }
            )

        self.assertEqual(coin["coin"], "TIA")
        self.assertEqual(threshold, 0.5)
        self.assertEqual(seen, [5.0, 3.0, 1.5, 0.5])

    def test_every_threshold_blocked_reports_why(self) -> None:
        with mock.patch.object(
            main_signal, "get_trending_coins", side_effect=RuntimeError("No valid trending coins found")
        ):
            with self.assertRaises(RuntimeError) as ctx:
                main_signal.select_trending_coin({"trending": {"min_change_percent": 5, "min_change_fallbacks": [3]}})

        message = str(ctx.exception)
        self.assertIn("No postable trending coin found", message)
        self.assertIn(">=5.0%", message)
        self.assertIn(">=3.0%", message)


class PublisherQueueHookTestCase(unittest.TestCase, TempStateMixin):
    def setUp(self) -> None:
        self.make_temp_state()

    def start_run(self) -> dict[str, Any]:
        slot = schedule.select_slot("meme_community")
        schedule.begin_run(slot)
        return slot

    def publish(self, argv: list[str]):
        return square_publisher.main(argv + ["--text", "hello world", "--images", ""])

    def test_successful_publish_marks_the_slot_published(self) -> None:
        slot = self.start_run()
        outcome = square_publisher.PublishOutcome("image", True, attempts=2, degraded=False, detail="ok")

        with mock.patch.object(square_publisher, "publish_to_square", return_value=outcome):
            self.assertEqual(self.publish([]), 0)

        state = self.state()
        self.assertIsNone(state["in_flight"])
        self.assertEqual(state["published"][-1]["slot"], slot["slot"])
        self.assertEqual(state["published"][-1]["mode"], "image")
        self.assertFalse(schedule.PENDING_RUN_FILE.exists())

    def test_retryable_publish_failure_requeues_the_slot(self) -> None:
        slot = self.start_run()
        error = square_publisher.PublishError("media poll timed out")

        with mock.patch.object(square_publisher, "publish_to_square", side_effect=error):
            self.assertEqual(self.publish([]), 1)

        state = self.state()
        self.assertEqual(state["pending"][0], slot["slot"])
        self.assertIsNone(state["in_flight"])

    def test_duplicate_risk_consumes_the_slot_instead_of_retrying(self) -> None:
        slot = self.start_run()
        error = square_publisher.PublishError("request already sent", duplicate_risk=True)

        with mock.patch.object(square_publisher, "publish_to_square", side_effect=error):
            self.assertEqual(self.publish([]), 1)

        state = self.state()
        self.assertNotIn(slot["slot"], state["pending"])
        self.assertEqual(state["published"][-1]["mode"], "uncertain")

    def test_no_state_flag_leaves_the_queue_alone(self) -> None:
        slot = self.start_run()
        outcome = square_publisher.PublishOutcome("text", True, 1, False, "ok")

        with mock.patch.object(square_publisher, "publish_to_square", return_value=outcome):
            self.assertEqual(self.publish(["--no-state"]), 0)

        state = self.state()
        self.assertEqual(state["in_flight"]["slot"], slot["slot"])
        self.assertEqual(state["published"], [])


if __name__ == "__main__":
    unittest.main()
