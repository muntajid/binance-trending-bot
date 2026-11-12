"""Offline tests for TP-only monitor publishing and silent stop-loss handling."""

from __future__ import annotations

import datetime
import unittest
from unittest import mock

from bot import monitor


class MonitorPostingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime.now(datetime.timezone.utc)
        self.trade = {
            "trade_id": "test-trade-1",
            "coin": "TEST",
            "direction": "LONG",
            "entry_price": 100.0,
            "tp1": 110.0,
            "tp2": 120.0,
            "sl": 95.0,
            "created_at": self.now.isoformat(),
            "last_checked_at": (self.now - datetime.timedelta(minutes=1)).isoformat(),
            "status": "ACTIVE",
        }

    def _run_monitor(self, result, current_price, publish):
        archived = []
        active_snapshots = []
        with (
            mock.patch.object(monitor, "load_active", return_value=[dict(self.trade)]),
            mock.patch.object(monitor, "save_active", side_effect=active_snapshots.append),
            mock.patch.object(monitor, "save_closed", side_effect=lambda trade: archived.append(dict(trade))),
            mock.patch.object(monitor, "get_current_price", return_value=current_price),
            mock.patch.object(monitor, "_scan_trade_history", return_value=result),
        ):
            monitor.check_trades_and_maybe_post(publish)
        return archived, active_snapshots

    def test_stop_loss_is_archived_without_any_post(self):
        event = monitor.TradeEvent("SL", 95.0, self.now)
        publish = mock.Mock()

        archived, active_snapshots = self._run_monitor(
            monitor.ScanResult(terminal_event=event), current_price=94.0, publish=publish
        )

        publish.assert_not_called()
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["status"], "SL_HIT")
        self.assertEqual(archived[0]["closed_price"], 95.0)
        self.assertEqual(active_snapshots[-1], [])

    def test_tp1_hit_still_posts_a_target_update(self):
        event = monitor.TradeEvent("TP1", 110.0, self.now)
        publish = mock.Mock()

        def publish_target(publish_func, trade, hit):
            publish_func(f"{trade['coin']} {hit.kind} hit", ["tp-card.png"])
            return "tp-card.png"

        archived = []
        active_snapshots = []
        with (
            mock.patch.object(monitor, "load_active", return_value=[dict(self.trade)]),
            mock.patch.object(monitor, "save_active", side_effect=active_snapshots.append),
            mock.patch.object(monitor, "save_closed", side_effect=lambda trade: archived.append(dict(trade))),
            mock.patch.object(monitor, "get_current_price", return_value=105.0),
            mock.patch.object(monitor, "_scan_trade_history", return_value=monitor.ScanResult(new_tp1_event=event)),
            mock.patch.object(monitor, "_publish_target_hit", side_effect=publish_target),
        ):
            monitor.check_trades_and_maybe_post(publish)

        publish.assert_called_once_with("TEST TP1 hit", ["tp-card.png"])
        self.assertEqual(archived, [])
        self.assertTrue(active_snapshots[-1][0]["tp1_hit"])

    def test_tp2_hit_still_posts_and_closes_the_trade(self):
        event = monitor.TradeEvent("TP2", 120.0, self.now)
        publish = mock.Mock()
        archived = []
        active_snapshots = []

        def publish_target(publish_func, trade, hit):
            publish_func(f"{trade['coin']} {hit.kind} hit", ["tp2-card.png"])
            return "tp2-card.png"

        with (
            mock.patch.object(monitor, "load_active", return_value=[dict(self.trade)]),
            mock.patch.object(monitor, "save_active", side_effect=active_snapshots.append),
            mock.patch.object(monitor, "save_closed", side_effect=lambda trade: archived.append(dict(trade))),
            mock.patch.object(monitor, "get_current_price", return_value=121.0),
            mock.patch.object(monitor, "_scan_trade_history", return_value=monitor.ScanResult(terminal_event=event)),
            mock.patch.object(monitor, "_publish_target_hit", side_effect=publish_target),
        ):
            monitor.check_trades_and_maybe_post(publish)

        publish.assert_called_once_with("TEST TP2 hit", ["tp2-card.png"])
        self.assertEqual(archived[0]["status"], "TP2_HIT")
        self.assertEqual(active_snapshots[-1], [])

    def test_near_stop_without_a_target_hit_does_not_post(self):
        publish = mock.Mock()

        archived, active_snapshots = self._run_monitor(
            monitor.ScanResult(), current_price=96.0, publish=publish
        )

        publish.assert_not_called()
        self.assertEqual(archived, [])
        self.assertEqual(len(active_snapshots[-1]), 1)
        self.assertNotIn("pressure_posted", active_snapshots[-1][0])


if __name__ == "__main__":
    unittest.main()
