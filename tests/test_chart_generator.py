import os
import shutil
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd
import numpy as np

from bot.chart_generator import (
    VALID_VISUAL_TYPES,
    _find_support_demand_zone,
    _find_swing_trendline,
    _format_chart_price,
    fetch_klines,
    generate_btc_market_chart,
    generate_single_chart,
    generate_trending_chart,
    select_chart_visual_type,
)
from bot.visual_strategy import validate_visual_asset


def _make_dummy_df(n=60, base=100.0, trend="up"):
    dates = pd.date_range("2026-11-12", periods=n, freq="1h")
    prices = [base]
    for i in range(1, n):
        step = (0.5 if trend == "up" else -0.5) + np.sin(i / 3.0) * 0.4
        prices.append(max(1.0, prices[-1] + step))

    rows = []
    for i, p in enumerate(prices):
        rows.append({
            "open_time": dates[i],
            "open": p - 0.2,
            "high": p + 0.6,
            "low": p - 0.6,
            "close": p + 0.2,
            "volume": 1000.0,
        })
    df = pd.DataFrame(rows)
    df.set_index("open_time", inplace=True)
    return df


def _make_dummy_klines(n=80, base=100.0):
    start = 1700000000000
    rows = []
    curr = base
    for i in range(n):
        curr += 0.2 + (i % 3 - 1) * 0.1
        rows.append({
            "open_time": start + i * 3600000,
            "open": curr - 0.2,
            "high": curr + 0.5,
            "low": curr - 0.5,
            "close": curr + 0.2,
            "volume": 1500.0,
        })
    return rows


class ChartGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_price_formatting(self):
        self.assertEqual(_format_chart_price(76007.0), "76,007.00")
        self.assertEqual(_format_chart_price(1.0557), "1.0557")
        self.assertEqual(_format_chart_price(0.1351), "0.1351")

    def test_visual_type_selection(self):
        df = _make_dummy_df(60, 100.0, "up")

        # Explicit override
        sig_explicit = {"visual_type": "breakout_expansion"}
        self.assertEqual(select_chart_visual_type(sig_explicit, df), "breakout_expansion")

        # Binary scenario
        sig_binary = {"angle_family": "binary_scenario", "why_now": "Scenario breakout"}
        self.assertEqual(select_chart_visual_type(sig_binary, df), "scenario_projection")

        # Support narrative
        sig_support = {"angle_family": "event_risk", "why_now": "Testing strong support floor"}
        self.assertEqual(select_chart_visual_type(sig_support, df), "support_demand_zone")

        # Structure break with trendline support
        sig_struct = {"angle_family": "structure_break", "why_now": "Ascending trendline retest"}
        chosen = select_chart_visual_type(sig_struct, df)
        self.assertIn(chosen, ("trendline_retest", "breakout_expansion"))

    def test_swing_trendline_detection(self):
        df_up = _make_dummy_df(60, 100.0, "up")
        pts = _find_swing_trendline(df_up, "LONG")
        if pts is not None:
            p1, p2 = pts
            self.assertLess(p1[0], p2[0])

        df_down = _make_dummy_df(60, 100.0, "down")
        pts_down = _find_swing_trendline(df_down, "SHORT")
        if pts_down is not None:
            p1, p2 = pts_down
            self.assertLess(p1[0], p2[0])

    def test_support_demand_zone_detection(self):
        df = _make_dummy_df(60, 100.0, "up")
        entry = float(df["close"].iloc[-1])
        zone = _find_support_demand_zone(df, entry, "LONG")
        if zone is not None:
            start_idx, btm, top = zone
            self.assertLess(btm, top)
            self.assertLess(top, entry)

    def test_generate_trending_chart_all_formats_long(self):
        dummy_klines = _make_dummy_klines(80, 100.0)
        with patch("bot.chart_generator.get_klines", return_value=dummy_klines):
            for v_type in VALID_VISUAL_TYPES:
                save_path = os.path.join(self.tmpdir, f"chart_long_{v_type}.png")
                signal = {
                    "entry_price": 105.0,
                    "tp1": 109.0,
                    "tp2": 112.0,
                    "sl": 101.0,
                    "direction": "LONG",
                    "change": 2.5,
                    "timeframe": "1h",
                    "visual_type": v_type,
                }
                out = generate_trending_chart("BTC", signal, "1h", save_path, visual_type=v_type)
                self.assertTrue(os.path.isfile(out))
                self.assertGreater(os.path.getsize(out), 5000)

                valid, issues = validate_visual_asset(out)
                self.assertTrue(valid, f"Issues for {v_type}: {issues}")

    def test_generate_trending_chart_short(self):
        dummy_klines = _make_dummy_klines(80, 100.0)
        with patch("bot.chart_generator.get_klines", return_value=dummy_klines):
            save_path = os.path.join(self.tmpdir, "chart_short.png")
            signal = {
                "entry_price": 100.0,
                "tp1": 95.0,
                "tp2": 92.0,
                "sl": 104.0,
                "direction": "SHORT",
                "change": -3.2,
                "timeframe": "1h",
                "visual_type": "setup_box_arrow",
            }
            out = generate_trending_chart("ETH", signal, "1h", save_path)
            self.assertTrue(os.path.isfile(out))
            valid, issues = validate_visual_asset(out)
            self.assertTrue(valid, f"Issues for SHORT: {issues}")

    def test_generate_btc_market_chart(self):
        dummy_klines = _make_dummy_klines(80, 75000.0)
        with patch("bot.chart_generator.get_klines", return_value=dummy_klines):
            save_path = os.path.join(self.tmpdir, "BTC_1H.png")
            out = generate_btc_market_chart(save_path)
            self.assertTrue(os.path.isfile(out))
            self.assertGreater(os.path.getsize(out), 5000)
            valid, issues = validate_visual_asset(out)
            self.assertTrue(valid, f"BTC chart QC issues: {issues}")

    def test_generate_single_chart_integration(self):
        dummy_klines = _make_dummy_klines(80, 10.0)
        with patch("bot.chart_generator.get_klines", return_value=dummy_klines):
            signal = {
                "entry_price": 10.0,
                "tp1": 10.8,
                "tp2": 11.2,
                "sl": 9.4,
                "direction": "LONG",
                "change": 4.1,
                "timeframe": "1h",
            }
            out = generate_single_chart("SOL", signal, out_dir=self.tmpdir)
            self.assertTrue(os.path.isfile(out))
            self.assertTrue(out.endswith("SOL_1H.png"))

    def test_fetch_klines_validations(self):
        # Insufficient data
        with patch("bot.chart_generator.get_klines", return_value=[{"open": 1}] * 20):
            with self.assertRaises(RuntimeError):
                fetch_klines("BTCUSDT", "1h")

        # Invalid OHLC (high < low)
        invalid_rows = [
            {"open_time": 1000 + i * 3600000, "open": 10, "high": 5, "low": 8, "close": 9, "volume": 10}
            for i in range(70)
        ]
        with patch("bot.chart_generator.get_klines", return_value=invalid_rows):
            with self.assertRaises(RuntimeError):
                fetch_klines("BTCUSDT", "1h")


if __name__ == "__main__":
    unittest.main()
