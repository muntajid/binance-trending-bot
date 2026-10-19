"""Tests for the shared trade-state helpers (cooldown window and stale setups)."""

from __future__ import annotations

import datetime
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import main_signal, schedule, trade_state


def iso(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).isoformat()


class CooldownTestCase(unittest.TestCase):
    def test_in_cooldown_respects_the_window(self) -> None:
        now = datetime.datetime(2026, 10, 19, 12, 0, tzinfo=datetime.timezone.utc)
        entries = [{"coin": "korub", "time": iso(now - datetime.timedelta(hours=5))}]

        self.assertTrue(trade_state.in_cooldown(entries, "KORUB", hours=24, now=now))
        self.assertFalse(trade_state.in_cooldown(entries, "KORUB", hours=4, now=now))
        self.assertFalse(trade_state.in_cooldown(entries, "AMP", hours=24, now=now))
        self.assertFalse(
            trade_state.in_cooldown([{"coin": "AMP", "time": "garbage"}], "AMP", hours=24, now=now),
            "an unreadable timestamp must not block a coin forever",
        )

    def test_cooldown_can_be_disabled(self) -> None:
        now = datetime.datetime(2026, 10, 19, tzinfo=datetime.timezone.utc)
        entries = [{"coin": "AMP", "time": iso(now)}]
        self.assertFalse(trade_state.in_cooldown(entries, "AMP", hours=0, now=now))


class StaleTradeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.datetime(2026, 10, 19, 12, 0, tzinfo=datetime.timezone.utc)

    def test_only_active_records_expire(self) -> None:
        trades = [
            {"coin": "UTK", "status": "ACTIVE", "created_at": "2026-08-13T09:47:04"},
            {
                "coin": "KORUB",
                "status": "ACTIVE",
                "created_at": iso(self.now - datetime.timedelta(hours=2)),
                "last_checked_at": iso(self.now - datetime.timedelta(minutes=10)),
            },
            {
                "coin": "AMP",
                "status": "TP2_HIT",
                "created_at": "2026-08-01T09:47:04",
                "last_checked_at": "2026-08-01T09:47:04",
            },
        ]

        live, expired = trade_state.split_expired(trades, ttl_hours=72, now=self.now)
        self.assertEqual([trade["coin"] for trade in live], ["KORUB", "AMP"])
        self.assertEqual([trade["coin"] for trade in expired], ["UTK"])

    def test_expiry_can_be_disabled(self) -> None:
        trades = [{"coin": "UTK", "status": "ACTIVE", "created_at": "2026-01-01T00:00:00"}]
        live, expired = trade_state.split_expired(trades, ttl_hours=0, now=self.now)
        self.assertEqual(len(live), 1)
        self.assertEqual(expired, [])

    def test_ttl_prefers_env_then_config_then_default(self) -> None:
        with mock.patch.dict(os.environ, {"ACTIVE_TRADE_TTL_HOURS": "12"}):
            self.assertEqual(trade_state.active_trade_ttl_hours(), 12.0)
        with mock.patch.dict(os.environ, {"ACTIVE_TRADE_TTL_HOURS": ""}):
            self.assertEqual(trade_state.active_trade_ttl_hours({"trading": {"active_trade_ttl_hours": 30}}), 30.0)
            self.assertEqual(trade_state.active_trade_ttl_hours({}), 72.0)
        with mock.patch.dict(os.environ, {"ACTIVE_TRADE_TTL_HOURS": "not-a-number"}):
            self.assertEqual(trade_state.active_trade_ttl_hours({}), 72.0)
        with mock.patch.dict(os.environ, {"ACTIVE_TRADE_TTL_HOURS": "0"}):
            self.assertEqual(trade_state.active_trade_ttl_hours(), 0.0)


class EngineFileTestCase(unittest.TestCase):
    """The engine must read the same state files the queue writes."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="engine-state-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.active = self.tmp / "active_trades.json"
        self.posted = self.tmp / "posted_coins.json"
        patches = [
            mock.patch.object(main_signal, "ACTIVE_FILE", str(self.active)),
            mock.patch.object(main_signal, "POSTED_FILE", str(self.posted)),
            mock.patch.object(schedule, "ACTIVE_TRADES_FILE", self.active),
            mock.patch.object(schedule, "POSTED_COINS_FILE", self.posted),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, path: Path, payload) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_active_coins_ignore_stale_setups(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        self.write(
            self.active,
            [
                {"coin": "UTK", "status": "ACTIVE", "created_at": iso(now - datetime.timedelta(days=60))},
                {"coin": "KORUB", "status": "ACTIVE", "created_at": iso(now - datetime.timedelta(hours=1))},
                {"coin": "AMP", "status": "SL_HIT", "created_at": iso(now)},
            ],
        )

        self.assertEqual(main_signal.get_active_coins(), {"KORUB"})
        self.assertEqual(
            main_signal.get_active_coins(include_stale=True),
            {"UTK", "KORUB"},
            "include_stale still ignores trades that already closed",
        )

    def test_can_post_uses_the_configured_window(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        self.write(self.posted, [{"coin": "AMP", "time": iso(now - datetime.timedelta(hours=10))}])

        self.assertFalse(main_signal.can_post("AMP", 24))
        self.assertTrue(main_signal.can_post("AMP", 4))
        with mock.patch.object(main_signal, "load_config", return_value={"duplicate_filter_hours": 4}):
            self.assertTrue(main_signal.can_post("AMP"), "config window is honoured when none is passed")

    def test_record_posted_returns_the_timestamp_used_for_rollback(self) -> None:
        self.write(self.posted, [])
        stamp = main_signal.record_posted("amp")
        entries = json.loads(self.posted.read_text(encoding="utf-8"))
        self.assertEqual(entries, [{"coin": "AMP", "time": stamp}])

    def test_save_active_rejects_an_overlapping_setup(self) -> None:
        self.write(self.active, [{"coin": "AMP", "status": "ACTIVE", "trade_id": "x"}])
        with self.assertRaises(RuntimeError):
            main_signal.save_active({"coin": "AMP"})


if __name__ == "__main__":
    unittest.main()
