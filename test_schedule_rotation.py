"""Tests for the continuous posting queue in :mod:`bot.schedule`.

Manual runs advance the same plan as cron, delayed runs preserve the mix across
midnight, and a run that never reached Square hands its slot back. Explicit
overrides remain auditable without consuming another queued slot.
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

EXPECTED_COUNTS = {"rapid_alert": 11, "explained_setup": 11, "market_overview": 1, "meme_community": 1}
TOTAL_SLOTS = 24
MEME_SLOT = 12


class QueueFixture(unittest.TestCase):
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


class QueueTestCase(QueueFixture):
    # -- the reported bug ------------------------------------------------
    def test_corrupt_state_file_restarts_the_day_instead_of_blocking_forever(self) -> None:
        schedule.select_slot()  # create the state file
        schedule.STATE_FILE.write_text('{"pending": [0, 1"', encoding="utf-8")  # half-written JSON

        slot = schedule.select_slot()
        self.assertEqual(slot["slot"], 0, "a broken state file must not stop an autonomous bot")
        self.assertEqual(len(self.read_state()["pending"]), TOTAL_SLOTS)

    def test_repeated_manual_runs_walk_the_queue(self) -> None:
        """Three runs minutes apart must not all produce the same post type."""

        slots = [self.publish()["slot"] for _ in range(3)]
        self.assertEqual(slots, [0, 1, 2], slots)
        types = [schedule.load_plan()[slot]["type"] for slot in slots]
        self.assertEqual(len(set(types)), 3, types)

    def test_a_full_rotation_yields_the_benchmark_mix(self) -> None:
        self.assertEqual(schedule.status()["published_today"], 0)

        self.publish_all(TOTAL_SLOTS)
        published = self.read_state()["published"]
        counts: dict[str, int] = {}
        for entry in published:
            counts[entry["type"]] = counts.get(entry["type"], 0) + 1

        self.assertEqual(len(published), TOTAL_SLOTS)
        self.assertEqual(counts, EXPECTED_COUNTS)
        self.assertEqual(self.read_state()["pending"], [], "a full rotation drains the queue")

    def test_delayed_outlook_does_not_repeat_after_midnight(self) -> None:
        """Reproduce runs 41515783317 / 41571777989 from 2026-11-02/03.

        GitHub delivered the 20:00 cron more than four hours late on Nov 2,
        then the 02:00 cron late on Nov 3. The old midnight reset put slot 0
        back at the head, producing two consecutive BTC outlooks.
        """
        legacy_state = {
            "version": 1,
            "day_utc": "2026-11-02",
            "pending": [0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11],
            "in_flight": None,
            "published": [{
                "slot": 2,
                "type": "explained_setup",
                "forced": False,
                "published_utc": "2026-11-02T13:00:29+00:00",
                "run_id": "41449121687",
                "mode": "image",
            }],
            "rotations": 0,
            "reset_reason": "new UTC day",
            "updated_utc": "2026-11-02T13:00:29+00:00",
            "last_error": None,
        }
        schedule.STATE_FILE.write_text(json.dumps(legacy_state), encoding="utf-8")

        with mock.patch.object(
            schedule, "_utc_now",
            return_value=datetime.datetime(2026, 11, 2, 20, 4, 55, tzinfo=datetime.timezone.utc),
        ):
            first = self.publish()
        with mock.patch.object(
            schedule, "_utc_now",
            return_value=datetime.datetime(2026, 11, 3, 3, 23, 7, tzinfo=datetime.timezone.utc),
        ):
            second = self.publish()

        self.assertEqual((first["slot"], first["type"]), (0, "market_overview"))
        self.assertEqual((second["slot"], second["type"]), (1, "rapid_alert"))
        state = self.read_state()
        self.assertEqual(state["day_utc"], "2026-11-03")
        self.assertEqual(state["rotations"], 0, "midnight is not a new rotation")
        self.assertEqual(len(state["published"]), 3, "the earlier setup plus both delayed runs remain auditable")
        self.assertNotIn(0, state["pending"])
        self.assertEqual(state["previous_day"]["by_type"], {"explained_setup": 1, "market_overview": 1})

    def test_a_full_rotation_of_delayed_runs_keeps_the_mix_across_calendar_days(self) -> None:
        now = datetime.datetime(2026, 11, 2, 20, 4, tzinfo=datetime.timezone.utc)
        with mock.patch.object(schedule, "_utc_now", side_effect=lambda: now):
            for _ in range(TOTAL_SLOTS):
                self.publish()
                now += datetime.timedelta(hours=7)

        state = self.read_state()
        self.assertEqual(state["pending"], [])
        self.assertEqual(state["rotations"], 0)
        counts: dict[str, int] = {}
        for entry in state["published"]:
            counts[entry["type"]] = counts.get(entry["type"], 0) + 1
        self.assertEqual(counts, EXPECTED_COUNTS)
        self.assertGreater(state["day_utc"], "2026-11-02")

        with mock.patch.object(schedule, "_utc_now", return_value=now):
            next_slot = schedule.select_slot()
        self.assertEqual(next_slot["slot"], 0)
        self.assertEqual(next_slot["rotation"], 2)
        self.assertEqual(next_slot["published_before"], 0)

    def test_in_flight_slot_survives_midnight_until_publish_or_timeout(self) -> None:
        with mock.patch.object(
            schedule, "_utc_now",
            return_value=datetime.datetime(2026, 11, 2, 23, 59, tzinfo=datetime.timezone.utc),
        ):
            slot = schedule.select_slot()
            schedule.begin_run(slot)
        with mock.patch.object(
            schedule, "_utc_now",
            return_value=datetime.datetime(2026, 11, 3, 0, 1, tzinfo=datetime.timezone.utc),
        ):
            state = schedule.load_state()
            self.assertEqual(state["in_flight"]["slot"], slot["slot"])
            self.assertNotIn(slot["slot"], state["pending"])
            self.assertEqual(state["day_utc"], "2026-11-03")

            record = schedule.publish_succeeded(mode="image", attempts=1)
            following = schedule.select_slot()

        self.assertEqual(record["day_utc"], "2026-11-03")
        self.assertEqual(record["rotation"], 1)
        self.assertEqual(following["slot"], 1)
        self.assertEqual(following["rotation"], 1)

    # -- explicit requests ----------------------------------------------
    def test_forced_type_consumes_its_own_pending_slot(self) -> None:
        slot = self.publish("meme_community")
        self.assertEqual((slot["slot"], slot["type"]), (MEME_SLOT, "meme_community"))
        self.assertNotIn(MEME_SLOT, self.read_state()["pending"])

        # The auto queue keeps its place: the meme slot must not be published twice.
        following = self.publish()
        self.assertEqual(following["slot"], 0, following)

    def test_request_for_an_already_published_slot_is_forced_and_non_consuming(self) -> None:
        self.publish("meme_community")
        state_before = self.read_state()

        slot = schedule.select_slot("meme_community")
        self.assertTrue(slot["forced"], slot)
        self.assertEqual(slot["slot"], MEME_SLOT)

        schedule.begin_run(slot)
        schedule.publish_succeeded()
        state_after = self.read_state()
        self.assertEqual(state_after["pending"], state_before["pending"])
        self.assertIn(0, state_after["pending"], "a forced run must not steal the auto queue's turn")
        self.assertEqual(len(state_after["published"]), 2, "both meme runs are on the record")
        self.assertEqual(schedule.status()["published_this_rotation"], 1, "an explicit extra does not consume a queued slot")
        self.assertEqual(schedule.status()["published_today"], 2)

    def test_unknown_request_is_rejected_with_a_helpful_message(self) -> None:
        with self.assertRaises(schedule.ScheduleError) as ctx:
            schedule.select_slot("sentiment")
        self.assertIn("rapid_alert", str(ctx.exception))

    def test_slot_number_request(self) -> None:
        slot = schedule.select_slot("slot 10")
        self.assertEqual(slot["slot"], 10)
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

        # The retried slot wins over the rest of the rotation's plan.
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
    def test_legacy_state_migration_persists_without_losing_pending_slots(self) -> None:
        self.publish_all(2)
        state = self.read_state()
        state.pop("rotation_started_utc")
        for entry in state["published"]:
            entry.pop("rotation")
        schedule.STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

        migrated = schedule.load_state()
        persisted = self.read_state()
        self.assertEqual(migrated["pending"], list(range(2, TOTAL_SLOTS)))
        self.assertEqual(persisted["pending"], list(range(2, TOTAL_SLOTS)))
        self.assertTrue(persisted["rotation_started_utc"])
        self.assertEqual([entry["rotation"] for entry in persisted["published"]], [1, 1])

    def test_day_rollover_preserves_the_queue_and_archives_the_day(self) -> None:
        self.publish_all(3)
        state = self.read_state()
        yesterday = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        state["day_utc"] = yesterday
        for entry in state["published"]:
            entry["published_utc"] = f"{yesterday}T12:00:00+00:00"
            entry["day_utc"] = yesterday
        schedule.STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

        fresh = schedule.load_state()
        self.assertEqual(fresh["pending"], list(range(3, TOTAL_SLOTS)))
        self.assertEqual(len(fresh["published"]), 3)
        self.assertEqual(fresh["previous_day"]["published"], 3)
        self.assertEqual(fresh["rotations"], 0)
        self.assertEqual(schedule.status()["published_this_rotation"], 3)
        self.assertEqual(schedule.status()["published_today"], 0)

    def test_queue_wraps_only_after_all_slots_are_resolved(self) -> None:
        self.publish_all(TOTAL_SLOTS)
        slot = schedule.select_slot()

        self.assertTrue(slot["wrapped"], slot)
        self.assertFalse(slot["forced"], slot)
        self.assertEqual(slot["slot"], 0, "a wrap restarts the rotation from slot 0")
        self.assertEqual(slot["rotation"], 2)
        self.assertEqual(slot["published_before"], 0)
        self.assertEqual(self.read_state()["rotations"], 1)

        self.publish()
        status = schedule.status()
        self.assertEqual(status["published_this_rotation"], 1)
        self.assertEqual(status["published_today"], TOTAL_SLOTS + 1, "daily reporting still includes both rotations")
        self.assertEqual(len(self.read_state()["published"]), TOTAL_SLOTS + 1, "a wrap keeps the audit trail")
        self.assertEqual(status["previous_rotation"]["by_type"], EXPECTED_COUNTS)

    def test_reset_starts_a_new_rotation_without_erasing_history(self) -> None:
        self.publish_all(4)
        slot = schedule.select_slot(reset=True)
        self.assertEqual(slot["slot"], 0)
        self.assertEqual(slot["published_before"], 0)
        self.assertEqual(slot["rotation"], 2)
        self.assertEqual(len(self.read_state()["published"]), 4)
        self.assertEqual(schedule.status()["previous_rotation"]["published"], 4)

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


OLD_12_SLOT_TYPES = [
    "market_overview", "rapid_alert", "explained_setup", "rapid_alert", "explained_setup", "rapid_alert",
    "meme_community", "explained_setup", "rapid_alert", "explained_setup", "rapid_alert", "explained_setup",
]


class PlanChangeTestCase(QueueFixture):
    """Switching bot/config.json from the 12-slot to the 24-slot plan."""

    def write_old_rotation(self, *, published: int, signature: str | None, in_flight: dict | None = None) -> None:
        records = [
            {
                "slot": slot,
                "type": OLD_12_SLOT_TYPES[slot],
                "forced": False,
                "published_utc": f"2026-11-14T{slot:02d}:10:00+00:00",
                "day_utc": "2026-11-14",
                "rotation": 4,
                "mode": "image",
            }
            for slot in range(published)
        ]
        state = {
            "version": 1,
            "day_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
            "rotation_started_utc": "2026-11-14T00:00:00+00:00",
            "pending": list(range(published, 12)),
            "in_flight": in_flight,
            "published": records,
            "rotations": 3,
            "reset_reason": "completed rotation",
            "last_error": None,
        }
        if signature is not None:
            state["plan_signature"] = signature
        schedule.STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

    def test_live_state_from_the_old_plan_starts_a_clean_24_slot_rotation(self) -> None:
        """The real queue on 2026-11-14: slots 0-6 done (meme at 6), 7-11 pending."""

        self.write_old_rotation(published=7, signature=None)
        slot = schedule.select_slot()

        self.assertEqual((slot["slot"], slot["type"]), (0, "market_overview"))
        self.assertEqual(slot["rotation"], 5)
        self.assertEqual(slot["published_before"], 0)
        state = self.read_state()
        self.assertEqual(state["pending"], list(range(TOTAL_SLOTS)))
        self.assertEqual(state["reset_reason"], "plan changed in bot/config.json")
        self.assertEqual(state["plan_signature"], schedule.plan_signature(schedule.load_plan()))
        self.assertEqual(len(state["published"]), 7, "the old rotation stays in the audit ledger")
        self.assertEqual(state["previous_rotation"]["unpublished"], [7, 8, 9, 10, 11])

    def test_explicit_signature_change_starts_a_new_rotation(self) -> None:
        self.write_old_rotation(published=3, signature="0:market_overview,1:rapid_alert")
        state = schedule.load_state()
        self.assertEqual(state["pending"], list(range(TOTAL_SLOTS)))
        self.assertEqual(state["rotations"], 4)

    def test_unsigned_state_consistent_with_the_plan_is_kept(self) -> None:
        """Slots 0-2 have identical types in both plans: nothing to restart."""

        self.write_old_rotation(published=3, signature=None)
        state = schedule.load_state()
        self.assertEqual(state["pending"], list(range(3, 12)))
        self.assertEqual(state["plan_signature"], schedule.plan_signature(schedule.load_plan()))
        self.assertEqual(state["rotations"], 3)

    def test_plan_change_waits_for_an_in_flight_run(self) -> None:
        in_flight = {
            "slot": 7,
            "type": "explained_setup",
            "forced": False,
            "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "rotation": 4,
        }
        self.write_old_rotation(published=7, signature=None, in_flight=in_flight)
        state = schedule.load_state()
        self.assertEqual(state["in_flight"]["slot"], 7, "a publishing run is never pulled from under itself")

        schedule.publish_succeeded(mode="image", attempts=1)
        after = schedule.load_state()
        self.assertEqual(after["pending"], list(range(TOTAL_SLOTS)))
        self.assertIsNone(after["in_flight"])


class DueGateTestCase(QueueFixture):
    """One automatic post per UTC hour; never two inside the minimum gap."""

    @staticmethod
    def at(hour: int, minute: int) -> datetime.datetime:
        return datetime.datetime(2026, 11, 15, hour, minute, tzinfo=datetime.timezone.utc)

    def state_with(self, *stamps: str, forced: bool = False, in_flight: dict | None = None) -> dict:
        return {
            "published": [{"slot": 1, "type": "rapid_alert", "forced": forced, "published_utc": s} for s in stamps],
            "in_flight": in_flight,
        }

    def test_empty_history_is_due(self) -> None:
        self.assertTrue(schedule.due_status(None, now=self.at(10, 0))["due"])

    def test_same_utc_hour_is_not_due(self) -> None:
        verdict = schedule.due_status(self.state_with("2026-11-15T10:01:30+00:00"), now=self.at(10, 45))
        self.assertFalse(verdict["due"])
        self.assertIn("already has its post", verdict["reason"])

    def test_next_hour_is_due(self) -> None:
        verdict = schedule.due_status(self.state_with("2026-11-15T10:01:30+00:00"), now=self.at(11, 0), min_gap=20)
        self.assertTrue(verdict["due"], verdict)

    def test_min_gap_defers_a_post_after_a_late_one(self) -> None:
        state = self.state_with("2026-11-15T10:58:00+00:00")
        self.assertFalse(schedule.due_status(state, now=self.at(11, 0), min_gap=20)["due"])
        self.assertTrue(schedule.due_status(state, now=self.at(11, 20), min_gap=20)["due"])

    def test_manual_extras_do_not_cost_the_hour_its_post(self) -> None:
        state = self.state_with("2026-11-15T11:05:00+00:00", forced=True)
        self.assertTrue(schedule.due_status(state, now=self.at(11, 30))["due"])

    def test_uncertain_publishes_count_as_the_hours_post(self) -> None:
        state = {"published": [{"slot": 2, "forced": False, "published_utc": None,
                                "resolved_utc": "2026-11-15T11:02:00+00:00", "mode": "uncertain"}]}
        self.assertFalse(schedule.due_status(state, now=self.at(11, 30))["due"])

    def test_live_in_flight_run_blocks_and_stale_one_does_not(self) -> None:
        live = {"slot": 3, "forced": False, "started_utc": "2026-11-15T11:00:00+00:00"}
        self.assertFalse(schedule.due_status(self.state_with(in_flight=live), now=self.at(11, 10))["due"])
        self.assertTrue(
            schedule.due_status(self.state_with(in_flight=live), now=self.at(13, 0), in_flight_timeout=45)["due"]
        )

    def test_cli_writes_the_github_output(self) -> None:
        self.publish()
        output = self.tmp / "github_output"
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": ""}):
            with mock.patch("sys.stdout"):
                self.assertEqual(schedule.main(["--due"]), 0)
        self.assertEqual(output.read_text(encoding="utf-8"), "due=false\n", "a post was just published")


class PlanLoadingTestCase(unittest.TestCase):
    def test_plan_matches_the_benchmark_mix(self) -> None:
        plan = schedule.load_plan()
        self.assertEqual(len(plan), TOTAL_SLOTS)
        self.assertEqual([entry["slot"] for entry in plan], list(range(TOTAL_SLOTS)))

        counts: dict[str, int] = {}
        for entry in plan:
            counts[entry["type"]] = counts.get(entry["type"], 0) + 1
        self.assertEqual(counts, EXPECTED_COUNTS)

    def test_hourly_layout_never_repeats_a_type_back_to_back(self) -> None:
        plan = schedule.load_plan()
        types = [entry["type"] for entry in plan]
        self.assertEqual(types[0], "market_overview")
        self.assertEqual(types[MEME_SLOT], "meme_community")
        for index, current in enumerate(types):
            following = types[(index + 1) % len(types)]  # includes the wrap 23 -> 0
            self.assertNotEqual(current, following, f"slots {index} and {(index + 1) % len(types)}")
        self.assertEqual([entry["hour_utc"] for entry in plan], list(range(TOTAL_SLOTS)))

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
            self.assertEqual(status["total_slots"], TOTAL_SLOTS)
            self.assertEqual(status["published_today"], 0)
            self.assertEqual(status["next_slot"]["slot"], 0)

            summary = schedule.render_summary(status)
            self.assertIn("Posting schedule", summary)
            self.assertIn("market_overview", summary)


if __name__ == "__main__":
    unittest.main()
