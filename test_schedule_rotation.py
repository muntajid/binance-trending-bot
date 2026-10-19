"""Tests for the day-scoped posting queue in :mod:`bot.schedule`.

These lock in the behaviour that the clock-based slot lookup got wrong: manual
runs must advance the day's plan instead of republishing whichever slot the
current UTC hour mapped to, and a run that never reached Square must hand its
slot back instead of losing that post for the day.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import schedule

EXPECTED_COUNTS = {"rapid_alert": 5, "explained_setup": 5, "market_overview": 1, "meme_community": 1}


class QueueTestCase(unittest.TestCase):
    """Every test runs against a throwaway copy of the repo's data/ directory."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="schedule-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

        data_dir = self.tmp / "data"
        data_dir.mkdir()
        bot_dir = self.tmp / "bot"
        bot_dir.mkdir()
        shutil.copy(Path("bot/config.json"), bot_dir / "config.json")

        self.data_dir = data_dir
        patches = [
            mock.patch.object(schedule, "ROOT_DIR", self.tmp),
            mock.patch.object(schedule, "CONFIG_FILE", bot_dir / "config.json"),
            mock.patch.object(schedule, "STATE_FILE", data_dir / "schedule_state.json"),
            mock.patch.object(schedule, "PENDING_RUN_FILE", data_dir / "pending_run.json"),
            mock.patch.object(schedule, "PUBLISH_RECEIPT_FILE", data_dir / "publish_receipt.json"),
            mock.patch.object(schedule, "LEGACY_SLOT_STATE_FILE", data_dir / "slot_state.json"),
            mock.patch.object(schedule, "ACTIVE_TRADES_FILE", data_dir / "active_trades.json"),
            mock.patch.object(schedule, "POSTED_COINS_FILE", data_dir / "posted_coins.json"),
            mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "test-run"}, clear=False),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    # -- helpers ---------------------------------------------------------
    def read_state(self) -> dict:
        return json.loads(schedule.STATE_FILE.read_text(encoding="utf-8"))

    def publish(self, request: str | None = None) -> dict:
        """Run one complete generate -> publish cycle and return the slot."""

        slot = schedule.select_slot(request)
        schedule.begin_run(slot)
        record = schedule.publish_succeeded(mode="image", attempts=1)
        self.assertIsNotNone(record, "an in-flight slot must exist before finalising")
        return slot

    def publish_all(self, runs: int) -> list[dict]:
        return [self.publish() for _ in range(runs)]

    # -- the reported bug ------------------------------------------------
    def test_corrupt_state_file_restarts_the_day_instead_of_blocking_forever(self) -> None:
        schedule.select_slot()  # create the state file
        schedule.STATE_FILE.write_text('{"pending": [0, 1"', encoding="utf-8")  # half-written JSON

        slot = schedule.select_slot()
        self.assertEqual(slot["slot"], 0, "a broken state file must not stop an autonomous bot")
        self.assertEqual(len(self.read_state()["pending"]), 12)

    def test_repeated_manual_runs_walk_the_queue(self) -> None:
        """Three runs minutes apart must not all produce the same post type."""

        slots = [self.publish()["slot"] for _ in range(3)]
        self.assertEqual(slots, [0, 1, 2], slots)
        types = [schedule.load_plan()[slot]["type"] for slot in slots]
        self.assertEqual(len(set(types)), 3, types)

    def test_a_full_day_yields_the_benchmark_mix(self) -> None:
        self.assertEqual(schedule.status()["published_today"], 0)

        self.publish_all(12)
        published = self.read_state()["published"]
        counts: dict[str, int] = {}
        for entry in published:
            counts[entry["type"]] = counts.get(entry["type"], 0) + 1

        self.assertEqual(len(published), 12)
        self.assertEqual(counts, EXPECTED_COUNTS)
        self.assertEqual(self.read_state()["pending"], [], "a full day drains the queue")

    # -- explicit requests ----------------------------------------------
    def test_forced_type_consumes_its_own_pending_slot(self) -> None:
        slot = self.publish("meme_community")
        self.assertEqual((slot["slot"], slot["type"]), (6, "meme_community"))
        self.assertNotIn(6, self.read_state()["pending"])

        # The auto queue keeps its place: slot 6 must not be published twice.
        following = self.publish()
        self.assertEqual(following["slot"], 0, following)

    def test_request_for_an_already_published_slot_is_forced_and_non_consuming(self) -> None:
        self.publish("meme_community")
        state_before = self.read_state()

        slot = schedule.select_slot("meme_community")
        self.assertTrue(slot["forced"], slot)
        self.assertEqual(slot["slot"], 6)

        schedule.begin_run(slot)
        schedule.publish_succeeded()
        state_after = self.read_state()
        self.assertEqual(state_after["pending"], state_before["pending"])
        self.assertIn(0, state_after["pending"], "a forced run must not steal the auto queue's turn")
        self.assertEqual(len(state_after["published"]), 2, "both meme runs are on the record")

    def test_unknown_request_is_rejected_with_a_helpful_message(self) -> None:
        with self.assertRaises(schedule.ScheduleError) as ctx:
            schedule.select_slot("sentiment")
        self.assertIn("rapid_alert", str(ctx.exception))

    def test_slot_number_request(self) -> None:
        slot = schedule.select_slot("slot 9")
        self.assertEqual(slot["slot"], 9)
        self.assertEqual(slot["type"], "explained_setup")

        with self.assertRaises(schedule.ScheduleError):
            schedule.select_slot("42")

    # -- failure handling -------------------------------------------------
    def test_publish_failure_returns_the_slot_to_the_head_of_the_queue(self) -> None:
        first = self.publish()
        slot = schedule.select_slot()
        schedule.begin_run(slot)

        schedule.abort_run("market data unavailable")

        state = self.read_state()
        self.assertEqual(state["pending"][0], slot["slot"])
        self.assertIn("market data unavailable", state["last_error"]["reason"])
        self.assertIsNone(state["in_flight"])

        # The retried slot wins over the rest of the day's plan.
        retried = schedule.select_slot()
        self.assertEqual(retried["slot"], slot["slot"])
        self.assertNotEqual(retried["slot"], first["slot"])

    def test_abort_rolls_back_only_this_run_open_trade_and_cooldown(self) -> None:
        (self.data_dir / "active_trades.json").write_text(
            json.dumps(
                [
                    {"coin": "KEEP", "trade_id": "keep-1", "status": "ACTIVE"},
                ]
            ),
            encoding="utf-8",
        )
        (self.data_dir / "posted_coins.json").write_text(
            json.dumps([{"coin": "KEEP", "time": "2026-01-01T00:00:00+00:00"}]),
            encoding="utf-8",
        )

        slot = schedule.select_slot()
        schedule.begin_run(slot)

        trades = json.loads((self.data_dir / "active_trades.json").read_text(encoding="utf-8"))
        trades.append({"coin": "TEMP", "trade_id": "temp-1", "status": "ACTIVE"})
        (self.data_dir / "active_trades.json").write_text(json.dumps(trades), encoding="utf-8")

        posted = json.loads((self.data_dir / "posted_coins.json").read_text(encoding="utf-8"))
        posted.append({"coin": "TEMP", "time": "2026-01-02T00:00:00+00:00"})
        (self.data_dir / "posted_coins.json").write_text(json.dumps(posted), encoding="utf-8")

        schedule.record_run_effects(coin="TEMP", trade_id="temp-1", posted_time="2026-01-02T00:00:00+00:00")
        schedule.abort_run("chart generation failed")

        self.assertEqual(
            [t["coin"] for t in json.loads((self.data_dir / "active_trades.json").read_text(encoding="utf-8"))],
            ["KEEP"],
        )
        self.assertEqual(
            [t["coin"] for t in json.loads((self.data_dir / "posted_coins.json").read_text(encoding="utf-8"))],
            ["KEEP"],
        )
        self.assertFalse(schedule.PENDING_RUN_FILE.exists())

    def test_uncertain_publish_consumes_the_slot_without_a_retry(self) -> None:
        """A create-post request that may have landed must not be re-run."""

        slot = schedule.select_slot()
        schedule.begin_run(slot)
        record = schedule.publish_uncertain("request already sent")

        self.assertEqual(record["slot"], slot["slot"])
        self.assertEqual(record["mode"], "uncertain")
        state = self.read_state()
        self.assertNotIn(slot["slot"], state["pending"])
        self.assertEqual(state["published"][-1]["mode"], "uncertain")
        self.assertIn("verify the Square profile", state["last_error"]["action"])

    def test_stale_in_flight_run_is_healed(self) -> None:
        slot = schedule.select_slot()
        schedule.begin_run(slot)

        state = self.read_state()
        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
        state["in_flight"]["started_utc"] = old.isoformat()
        schedule.STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

        fresh = schedule.load_state()
        self.assertIsNone(fresh["in_flight"])
        self.assertEqual(fresh["pending"][0], slot["slot"])

    def test_in_flight_run_is_recorded_in_state(self) -> None:
        """The concurrency group serialises runs, and the in-flight slot is taken
        out of the pending list the moment it is claimed."""

        slot = schedule.select_slot()
        schedule.begin_run(slot)

        state = self.read_state()
        self.assertIsNotNone(state["in_flight"])
        self.assertEqual(state["published"], [])
        self.assertNotIn(slot["slot"], state["pending"])

    # -- rotation bookkeeping --------------------------------------------
    def test_day_rollover_rebuilds_the_queue_and_keeps_a_summary(self) -> None:
        self.publish_all(3)
        state = self.read_state()
        yesterday = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        state["day_utc"] = yesterday
        schedule.STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

        fresh = schedule.load_state()
        self.assertEqual(len(fresh["pending"]), 12)
        self.assertEqual(fresh["published"], [])
        self.assertEqual(fresh["previous_day"]["published"], 3)
        self.assertEqual(fresh["reset_reason"], "new UTC day")

    def test_queue_wraps_after_the_day_is_published(self) -> None:
        self.publish_all(12)
        slot = schedule.select_slot()

        self.assertTrue(slot["wrapped"], slot)
        self.assertFalse(slot["forced"], slot)
        self.assertEqual(slot["slot"], 0, "a wrap restarts the rotation from slot 0")
        self.assertEqual(slot["rotation"], 2)
        self.assertEqual(self.read_state()["rotations"], 1)

    def test_reset_flag_refills_a_partially_published_day(self) -> None:
        self.publish_all(4)
        slot = schedule.select_slot(reset=True)
        self.assertEqual(slot["slot"], 0)
        self.assertEqual(slot["published_before"], 0)

    def test_receipt_records_the_last_publish(self) -> None:
        slot = self.publish()
        receipt = json.loads(schedule.PUBLISH_RECEIPT_FILE.read_text(encoding="utf-8"))
        self.assertEqual(receipt["slot"], slot["slot"])
        self.assertEqual(receipt["mode"], "image")
        self.assertEqual(receipt["run_id"], "test-run")
        self.assertEqual(receipt["pending_after"], self.read_state()["pending"])

    def test_legacy_slot_state_file_stays_in_sync(self) -> None:
        self.publish()
        legacy = json.loads(schedule.LEGACY_SLOT_STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(legacy["last_slot"], 0)
        self.assertEqual(legacy["last_type"], "market_overview")
        self.assertIn(1, legacy["pending_slots"])


class PlanLoadingTestCase(unittest.TestCase):
    def test_plan_matches_the_benchmark_mix(self) -> None:
        plan = schedule.load_plan()
        self.assertEqual(len(plan), 12)
        self.assertEqual([entry["slot"] for entry in plan], list(range(12)))

        counts: dict[str, int] = {}
        for entry in plan:
            counts[entry["type"]] = counts.get(entry["type"], 0) + 1
        self.assertEqual(counts, EXPECTED_COUNTS)

    def test_config_is_the_single_source_of_truth(self) -> None:
        with self.assertRaises(schedule.ScheduleError):
            schedule.load_plan({"schedule": {"slots": []}})
        with self.assertRaises(schedule.ScheduleError):
            schedule.load_plan({"schedule": {"slots": [{"slot": 0, "type": "hot_take"}]}})
        with self.assertRaises(schedule.ScheduleError):
            schedule.load_plan(
                {"schedule": {"slots": [{"slot": 1, "type": "rapid_alert"}, {"slot": 1, "type": "rapid_alert"}]}}
            )

    def test_status_and_summary_render(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="schedule-status-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        data_dir = tmp / "data"
        data_dir.mkdir()
        bot_dir = tmp / "bot"
        bot_dir.mkdir()
        shutil.copy(Path("bot/config.json"), bot_dir / "config.json")
        with mock.patch.object(schedule, "CONFIG_FILE", bot_dir / "config.json"), mock.patch.object(
            schedule, "STATE_FILE", data_dir / "schedule_state.json"
        ), mock.patch.object(schedule, "LEGACY_SLOT_STATE_FILE", data_dir / "slot_state.json"):
            status = schedule.status()
            self.assertEqual(status["total_slots"], 12)
            self.assertEqual(status["published_today"], 0)
            self.assertEqual(status["next_slot"]["slot"], 0)

            summary = schedule.render_summary(status)
            self.assertIn("Posting schedule", summary)
            self.assertIn("market_overview", summary)


if __name__ == "__main__":
    unittest.main()
