"""Tests for the hourly pacer in :mod:`bot.pacer` (no network, fake clock)."""

from __future__ import annotations

import datetime
import unittest
import urllib.error
from typing import Any
from unittest import mock

from bot import pacer

UTC = datetime.timezone.utc


def at(hour: int, minute: int = 0, second: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 11, 15, hour, minute, second, tzinfo=UTC)


class FakeClock:
    def __init__(self, start: datetime.datetime) -> None:
        self.current = start

    def now(self) -> datetime.datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += datetime.timedelta(seconds=seconds)


class FakeGitHub:
    """Simulates signal runs that publish ~2 minutes after being dispatched."""

    def __init__(self, clock: FakeClock, *, fail_first: int = 0) -> None:
        self.clock = clock
        self.published: list[str] = []
        self.dispatches: list[tuple[str, datetime.datetime]] = []
        self.pacer_runs: list[dict[str, Any]] = []
        self.fail_first = fail_first
        self.running_until: datetime.datetime | None = None
        self.pending_publish: datetime.datetime | None = None

    def _settle(self) -> None:
        if self.running_until and self.clock.now() >= self.running_until:
            if self.pending_publish is not None:
                self.published.append(self.pending_publish.isoformat())
            self.running_until = None
            self.pending_publish = None

    def workflow_runs(self, workflow: str, per_page: int = 15) -> list[dict[str, Any]]:
        self._settle()
        if workflow == pacer.PACER_WORKFLOW:
            return self.pacer_runs
        return [{"id": 1, "status": "in_progress"}] if self.running_until else [{"id": 1, "status": "completed"}]

    def read_state(self, ref: str) -> dict[str, Any]:
        self._settle()
        return {"published": [{"slot": 0, "forced": False, "published_utc": s} for s in self.published]}

    def dispatch(self, workflow: str, ref: str, inputs: dict[str, str] | None = None) -> None:
        self.dispatches.append((workflow, self.clock.now()))
        if workflow != pacer.SIGNAL_WORKFLOW:
            return
        self.running_until = self.clock.now() + datetime.timedelta(minutes=2)
        if self.fail_first > 0:
            self.fail_first -= 1  # the run fails: no publish, slot back in the queue
        else:
            self.pending_publish = self.running_until


CONFIG = {
    "schedule": {
        "min_gap_minutes": 20,
        "pacer": {"check_every_minutes": 5, "max_dispatches_per_hour": 3, "handoff_after_minutes": 330},
    }
}


class PureHelpersTestCase(unittest.TestCase):
    def test_next_tick_is_clock_aligned(self) -> None:
        self.assertEqual(pacer.next_tick(at(10, 0, 0), 5), at(10, 0, 20))
        self.assertEqual(pacer.next_tick(at(10, 0, 20), 5), at(10, 5, 20))
        self.assertEqual(pacer.next_tick(at(10, 3, 7), 5), at(10, 5, 20))
        self.assertEqual(pacer.next_tick(at(23, 58), 5), at(23, 0) + datetime.timedelta(hours=1, seconds=20))

    def test_settings_are_clamped_below_the_job_limit(self) -> None:
        settings = pacer.pacer_settings({"schedule": {"pacer": {"handoff_after_minutes": 500, "check_every_minutes": "x"}}})
        self.assertEqual(settings["handoff_after_minutes"], 345)
        self.assertEqual(settings["check_every_minutes"], 5)

    def test_decide_respects_active_runs_and_the_dispatch_cap(self) -> None:
        state = {"published": [{"forced": False, "published_utc": "2026-11-15T09:01:00+00:00"}]}
        common = {"now": at(10, 0, 20), "state": state, "max_dispatches": 3, "min_gap": 20}
        self.assertEqual(pacer.decide(signal_active=False, dispatches_this_hour=0, **common)[0], True)
        self.assertEqual(pacer.decide(signal_active=True, dispatches_this_hour=0, **common)[0], False)
        go, reason = pacer.decide(signal_active=False, dispatches_this_hour=3, **common)
        self.assertFalse(go)
        self.assertIn("next hour", reason)

    def test_only_the_oldest_active_pacer_leads(self) -> None:
        runs = [
            {"id": 100, "status": "in_progress"},
            {"id": 200, "status": "in_progress"},
            {"id": 50, "status": "completed"},
        ]
        self.assertEqual(pacer.leadership(100, runs), "lead")
        self.assertEqual(pacer.leadership(200, runs), "yield")
        self.assertEqual(pacer.leadership(300, [{"id": 250, "status": "queued"}]), "lead")


class LoopTestCase(unittest.TestCase):
    def run_pacer(self, github: FakeGitHub, clock: FakeClock) -> dict[str, Any]:
        with mock.patch.object(pacer.schedule, "load_config", return_value=CONFIG), mock.patch.object(
            pacer, "_log"
        ):
            return pacer.run(client=github, ref="main", my_run_id=500, now=clock.now, sleep=clock.sleep)

    def test_one_post_per_hour_then_handoff(self) -> None:
        clock = FakeClock(at(10, 0, 20))
        github = FakeGitHub(clock)
        report = self.run_pacer(github, clock)

        hours = [datetime.datetime.fromisoformat(stamp).strftime("%H") for stamp in github.published]
        self.assertEqual(hours, ["10", "11", "12", "13", "14", "15"], "5.5h of pacing -> one post per hour")
        self.assertEqual(len(set(hours)), len(hours), "never two posts in the same hour")
        self.assertTrue(report["handoff"])
        self.assertEqual(github.dispatches[-1][0], pacer.PACER_WORKFLOW)
        self.assertLessEqual(clock.now(), at(10, 0, 20) + datetime.timedelta(minutes=331))

    def test_failed_run_is_retried_within_the_hour(self) -> None:
        clock = FakeClock(at(10, 0, 20))
        github = FakeGitHub(clock, fail_first=1)
        self.run_pacer(github, clock)

        signal_dispatches = [when for name, when in github.dispatches if name == pacer.SIGNAL_WORKFLOW]
        self.assertEqual(signal_dispatches[0], at(10, 0, 20))
        self.assertEqual(signal_dispatches[1], at(10, 5, 20), "retried on the next tick")
        self.assertTrue(github.published[0].startswith("2026-11-15T10:07"))

    def test_persistent_failures_are_capped_per_hour(self) -> None:
        clock = FakeClock(at(10, 0, 20))
        github = FakeGitHub(clock, fail_first=10_000)
        self.run_pacer(github, clock)

        per_hour: dict[str, int] = {}
        for name, when in github.dispatches:
            if name == pacer.SIGNAL_WORKFLOW:
                per_hour[when.strftime("%H")] = per_hour.get(when.strftime("%H"), 0) + 1
        self.assertTrue(per_hour)
        self.assertTrue(all(count <= 3 for count in per_hour.values()), per_hour)

    def test_api_errors_do_not_end_the_chain(self) -> None:
        clock = FakeClock(at(10, 0, 20))
        github = FakeGitHub(clock)
        original = github.workflow_runs
        calls = {"n": 0}

        def flaky(workflow: str, per_page: int = 15):
            calls["n"] += 1
            if workflow == pacer.SIGNAL_WORKFLOW and calls["n"] in {2, 3}:
                raise urllib.error.URLError("temporary failure")
            return original(workflow, per_page)

        github.workflow_runs = flaky  # type: ignore[method-assign]
        report = self.run_pacer(github, clock)
        self.assertTrue(report["handoff"])
        self.assertGreaterEqual(len(github.published), 5)

    def test_a_duplicate_pacer_exits_without_dispatching(self) -> None:
        clock = FakeClock(at(10, 0, 20))
        github = FakeGitHub(clock)
        github.pacer_runs = [{"id": 100, "status": "in_progress"}, {"id": 500, "status": "in_progress"}]
        report = self.run_pacer(github, clock)

        self.assertFalse(report["led"])
        self.assertEqual(github.dispatches, [])
        self.assertLessEqual(clock.now() - at(10, 0, 20), datetime.timedelta(seconds=pacer.LEADER_WAIT_SECONDS))

    def test_successor_waits_for_a_handing_off_predecessor(self) -> None:
        clock = FakeClock(at(10, 0, 20))
        github = FakeGitHub(clock)
        predecessor = {"id": 100, "status": "in_progress"}
        github.pacer_runs = [predecessor, {"id": 500, "status": "in_progress"}]
        original_sleep = clock.sleep

        def sleep(seconds: float) -> None:
            predecessor["status"] = "completed"  # finishes during the first wait
            original_sleep(seconds)

        clock.sleep = sleep  # type: ignore[method-assign]
        report = self.run_pacer(github, clock)
        self.assertTrue(report["led"])
        self.assertTrue(github.published)


if __name__ == "__main__":
    unittest.main()
