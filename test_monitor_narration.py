"""Offline tests for the monitor's verified outcome-narration layer."""

from __future__ import annotations

import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import monitor


class NearInvalidationTests(unittest.TestCase):
    def test_long_trade_near_stop_detected(self):
        trade = {"direction": "LONG", "entry_price": 100.0, "sl": 95.0}
        self.assertTrue(monitor._near_invalidation(trade, 95.5))   # 10% of the way to entry-risk
        self.assertTrue(monitor._near_invalidation(trade, 96.0))
        self.assertFalse(monitor._near_invalidation(trade, 97.5))  # still comfortably away
        self.assertFalse(monitor._near_invalidation(trade, 94.9))  # already through the stop

    def test_short_trade_near_stop_detected(self):
        trade = {"direction": "SHORT", "entry_price": 100.0, "sl": 105.0}
        self.assertTrue(monitor._near_invalidation(trade, 104.5))
        self.assertFalse(monitor._near_invalidation(trade, 102.0))
        self.assertFalse(monitor._near_invalidation(trade, 105.1))

    def test_degenerate_risk_never_triggers(self):
        trade = {"direction": "LONG", "entry_price": 100.0, "sl": 100.0}
        self.assertFalse(monitor._near_invalidation(trade, 99.9))


class NarrationBudgetTests(unittest.TestCase):
    def test_daily_budget_resets_per_utc_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_file = Path(tmp) / "monitor_posts.json"
            with mock.patch.object(monitor, "MONITOR_POSTS_FILE", fake_file):
                day1 = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=datetime.timezone.utc)
                budget = monitor._load_monitor_budget(day1)
                self.assertEqual(budget["sl_posts"], 0)
                budget["sl_posts"] = 2
                monitor._save_monitor_budget(budget)

                reloaded = monitor._load_monitor_budget(day1)
                self.assertEqual(reloaded["sl_posts"], 2)

                day2 = datetime.datetime(2026, 11, 11, 0, 1, tzinfo=datetime.timezone.utc)
                self.assertEqual(monitor._load_monitor_budget(day2)["sl_posts"], 0)

    def test_caps_come_from_config_then_env(self):
        with mock.patch.dict(os.environ, {"MONITOR_SL_POST_MAX_PER_DAY": "7"}):
            self.assertEqual(monitor._daily_cap("sl"), 7)
        with mock.patch.dict(os.environ, {"MONITOR_SL_POST_MAX_PER_DAY": "999"}):
            self.assertEqual(monitor._daily_cap("sl"), 24)  # bounded

    def test_enable_flags_default_from_config(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(monitor._sl_posts_enabled())      # config default: on
            self.assertTrue(monitor._pressure_posts_enabled())
        with mock.patch.dict(os.environ, {"MONITOR_SL_POSTS_ENABLED": "0"}):
            self.assertFalse(monitor._sl_posts_enabled())

    def test_sl_and_pressure_posts_render_through_poster(self):
        trade = {
            "coin": "STX",
            "direction": "LONG",
            "entry_price": 329.1,
            "tp1": 364.0,
            "tp2": 399.0,
            "sl": 305.0,
            "created_at": "2026-11-10T22:58:46+00:00",
        }
        event = monitor.TradeEvent(
            kind="SL",
            price=305.0,
            happened_at=datetime.datetime(2026, 11, 11, 9, 0, tzinfo=datetime.timezone.utc),
        )
        published: list[tuple[str, list[str]]] = []

        def fake_publish(text: str, images: list[str]) -> None:
            published.append((text, images))

        with mock.patch.object(monitor, "_create_target_card", return_value="/tmp/fake_card.png"), mock.patch.object(
            Path, "is_file", return_value=True
        ):
            monitor._publish_stop(fake_publish, trade, event)
        self.assertIn("STOPPED", published[0][0])
        self.assertEqual(published[0][1], ["/tmp/fake_card.png"])

        monitor._publish_pressure(fake_publish, trade, 307.5)
        self.assertIn("under pressure", published[1][0].lower())
        self.assertEqual(published[1][1], [])  # journal updates are text-only


if __name__ == "__main__":
    unittest.main()
