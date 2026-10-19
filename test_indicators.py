"""Deterministic unit tests for the dependency-free OHLCV indicator layer."""

import math
import unittest

from bot.indicators import (
    calculate_all_indicators,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_stoch_rsi,
    calculate_vwap,
    detect_candlestick_patterns,
    detect_market_structure,
    find_support_resistance,
)


class IndicatorTests(unittest.TestCase):
    def setUp(self):
        self.uptrend = []
        for index in range(60):
            opening = 100.0 + index * 0.5
            self.uptrend.append(
                {
                    "open_time": index,
                    "open": opening,
                    "high": opening + 1.0,
                    "low": opening - 0.5,
                    "close": opening + 0.5,
                    "volume": 100.0 + index,
                }
            )

    def test_empty_and_nan_inputs_are_neutral(self):
        self.assertEqual(calculate_ema([], 20), [])
        self.assertEqual(calculate_rsi([], 14), 50.0)
        self.assertEqual(calculate_macd([])["histogram"], 0.0)
        self.assertEqual(calculate_vwap([]), 0.0)
        self.assertEqual(calculate_atr([]), 0.0)
        self.assertEqual(detect_candlestick_patterns([]), [])
        self.assertEqual(find_support_resistance([]), {"support": None, "resistance": None, "levels": []})
        self.assertTrue(math.isfinite(calculate_rsi([1, float("nan"), 2])))
        self.assertTrue(all(math.isfinite(value) for value in calculate_ema([1, float("nan"), 2], 2)))

    def test_momentum_and_volatility_indicators_are_bounded(self):
        prices = [float(value) for value in range(1, 50)]
        self.assertGreater(calculate_ema(prices, 5)[-1], calculate_ema(prices, 20)[-1])
        self.assertEqual(calculate_rsi(prices), 100.0)
        self.assertTrue(0.0 <= calculate_rsi(list(reversed(prices))) <= 100.0)
        self.assertGreater(calculate_macd(prices)["macd"], 0.0)
        bands = calculate_bollinger_bands(prices)
        self.assertLessEqual(bands["lower"], bands["middle"])
        self.assertLessEqual(bands["middle"], bands["upper"])
        self.assertTrue(0.0 <= calculate_stoch_rsi([20, 40, 60, 80])["k"] <= 100.0)

    def test_candle_derived_metrics_and_structure(self):
        self.assertGreater(calculate_vwap(self.uptrend), 0.0)
        self.assertGreater(calculate_atr(self.uptrend), 0.0)
        levels = find_support_resistance(self.uptrend)
        self.assertIsNotNone(levels["support"])
        self.assertIsNotNone(levels["resistance"])
        self.assertIn("bullish", detect_market_structure(self.uptrend))
        result = calculate_all_indicators(self.uptrend)
        self.assertEqual(result["close"], self.uptrend[-1]["close"])
        self.assertGreater(result["ema_20"], result["ema_50"])
        self.assertEqual(len(result["candlestick_patterns"]), len(set(result["candlestick_patterns"])))

    def test_bearish_engulfing_hammer_shooting_star_and_doji(self):
        bearish_engulfing = [
            {"open": 9.0, "high": 10.5, "low": 8.5, "close": 10.0, "volume": 10},
            {"open": 10.2, "high": 10.4, "low": 8.6, "close": 8.8, "volume": 12},
        ]
        self.assertIn("bearish_engulfing", detect_candlestick_patterns(bearish_engulfing))
        hammer = [{"open": 10.0, "high": 10.3, "low": 8.0, "close": 10.2, "volume": 10}]
        self.assertIn("hammer", detect_candlestick_patterns(hammer))
        shooting_star = [{"open": 10.4, "high": 12.0, "low": 10.1, "close": 10.2, "volume": 10}]
        self.assertIn("shooting_star", detect_candlestick_patterns(shooting_star))
        doji = [{"open": 10.0, "high": 10.5, "low": 9.5, "close": 10.01, "volume": 10}]
        self.assertIn("doji", detect_candlestick_patterns(doji))

    def test_bullish_engulfing_and_morning_star(self):
        engulfing = [
            {"open": 10.0, "high": 10.5, "low": 8.5, "close": 9.0, "volume": 10},
            {"open": 8.8, "high": 10.4, "low": 8.6, "close": 10.2, "volume": 12},
        ]
        self.assertIn("bullish_engulfing", detect_candlestick_patterns(engulfing))

        morning_star = [
            {"open": 10.0, "high": 10.2, "low": 7.8, "close": 8.0, "volume": 10},
            {"open": 8.0, "high": 8.3, "low": 7.9, "close": 8.2, "volume": 8},
            {"open": 8.1, "high": 9.5, "low": 8.0, "close": 9.3, "volume": 14},
        ]
        self.assertIn("morning_star", detect_candlestick_patterns(morning_star))


if __name__ == "__main__":
    unittest.main()
