"""Offline regression tests for optional spot and futures analysis inputs."""

import unittest
from unittest.mock import patch

import bot.market_context as market_context
import bot.market_data as market_data
from bot import futures_data


class MarketDataAnalysisTests(unittest.TestCase):
    def test_kline_parser_preserves_optional_taker_buy_volume(self):
        row = [1000, "10", "12", "9", "11", "20", 1999, "220", 10, "13", "143", "0"]
        result = market_data._parse_kline_row(row)
        self.assertEqual(result["taker_buy_volume"], 13.0)

    def test_orderbook_depth_imbalance(self):
        payload = {
            "bids": [["100", "3"], ["99", "2"]],
            "asks": [["101", "1"], ["102", "1"]],
        }
        with patch.object(market_data, "_request_json", return_value=payload):
            result = market_data.get_orderbook("BTCUSDT", limit=20)
        self.assertEqual(result["imbalance"], "bid_heavy")
        self.assertGreater(result["bid_depth_usdt"], result["ask_depth_usdt"])
        self.assertTrue(result["available"])

    def test_multitimeframe_fetch_isolates_failures(self):
        def fake_klines(symbol, interval, limit):
            if interval == "4h":
                raise RuntimeError("temporary failure")
            return [{"interval": interval, "limit": limit}]

        with patch.object(market_data, "get_klines", side_effect=fake_klines):
            result = market_data.get_klines_multi_timeframe("BTCUSDT")
        self.assertEqual(set(result), {"5m", "15m", "1h", "4h", "1d", "1w"})
        self.assertEqual(result["4h"], [])
        self.assertEqual(result["1w"][0]["limit"], 100)

    def test_volume_uses_binance_taker_buy_field(self):
        candles = [
            {"open": 100, "high": 102, "low": 99, "close": 101, "volume": 10, "taker_buy_volume": 7}
            for _ in range(20)
        ]
        candles[-1] = {
            "open": 100,
            "high": 110,
            "low": 99,
            "close": 109,
            "volume": 30,
            "taker_buy_volume": 20,
        }
        result = market_data.calculate_volume_analysis(
            candles, {"quote_volume_24h": 12345}
        )
        self.assertTrue(result["volume_spike"])
        self.assertTrue(result["breakout_volume_confirmed"])
        self.assertEqual(result["buying_volume"], 153.0)
        self.assertEqual(result["selling_volume"], 67.0)
        self.assertEqual(result["quote_volume_24h"], 12345.0)
        self.assertFalse(result["estimated"])

    def test_btc_dominance_falls_back_to_top_twenty_volume_proxy(self):
        tickers = [
            {"symbol": "BTCUSDT", "quoteVolume": "100"},
            {"symbol": "ETHUSDT", "quoteVolume": "300"},
        ]
        with patch.object(
            market_data.requests,
            "get",
            side_effect=market_data.requests.RequestException("offline"),
        ), patch.object(market_data, "get_ticker_24h_all", return_value=tickers):
            result = market_data.get_btc_dominance_context()
        self.assertAlmostEqual(result["btc_dominance_percent"], 25.0)
        self.assertTrue(result["estimated"])
        self.assertIn("volume proxy", result["source"])

    def test_market_context_reports_proxy_and_degrades_gracefully(self):
        candles = [
            {
                "open": 100 + index,
                "high": 102 + index,
                "low": 99 + index,
                "close": 101 + index,
                "volume": 10,
            }
            for index in range(30)
        ]
        with patch.object(
            market_context,
            "get_btc_dominance_context",
            return_value={"btc_dominance_percent": 55.0, "total_mcap_proxy": 1_000_000, "estimated": True},
        ), patch.object(
            market_context,
            "get_klines_multi_timeframe",
            return_value={"1h": candles, "4h": candles},
        ), patch.object(
            market_context,
            "get_orderbook",
            return_value={"imbalance": "bid_heavy", "bid_depth_usdt": 100, "ask_depth_usdt": 50},
        ), patch.object(
            market_context,
            "get_open_interest",
            return_value={"open_interest_usdt": 500_000, "estimated": True},
        ), patch.object(
            market_context.requests,
            "get",
            side_effect=market_context.requests.RequestException("offline"),
        ):
            result = market_context.get_market_context(
                {"price": 100_000, "change_24h": 2.0, "quote_volume_24h": 1_000_000},
                {"price": 5_000, "change_24h": 1.0},
                [{"coin": "ETH", "change": 1.0}, {"coin": "ALT", "change": 5.0}],
                coin_symbol="ALT",
            )
        self.assertEqual(result["btc_price"], 100_000)
        self.assertIsNotNone(result["eth_btc_ratio"])
        self.assertEqual(result["btc_dominance"]["btc_dominance_percent"], 55.0)
        self.assertTrue(result["fear_greed"]["estimated"])
        self.assertTrue(result["on_chain_proxy"]["estimated"])
        self.assertIn("ALT", result["fundamental_notes"])


class FuturesDataTests(unittest.TestCase):
    def test_funding_and_long_short_schema(self):
        with patch.object(
            futures_data, "_get_json", return_value={"lastFundingRate": "0.001"}
        ):
            funding = futures_data.get_funding_rate("BTC")
        self.assertEqual(funding["bias"], "long_pays_short")
        self.assertAlmostEqual(funding["annualized"], 109.5)

        with patch.object(
            futures_data,
            "_get_json",
            return_value=[{"longShortRatio": "2"}],
        ):
            ratio = futures_data.get_long_short_ratio("BTCUSDT")
        self.assertAlmostEqual(ratio["long_pct"], 200 / 3)
        self.assertAlmostEqual(ratio["short_pct"], 100 / 3)

    def test_heatmap_is_explicitly_estimated(self):
        candles = [
            {
                "open": 100 + index,
                "high": 102 + index,
                "low": 99 + index,
                "close": 101 + index,
                "volume": 10,
            }
            for index in range(30)
        ]
        result = futures_data.get_liquidation_heatmap_levels(
            "BTCUSDT",
            candles,
            open_interest_data={"open_interest_usdt": 100000},
            long_short_data={"long_pct": 60, "short_pct": 40},
        )
        self.assertTrue(result["estimated"])
        self.assertIsNotNone(result["long_liquidation_zone"])
        self.assertIsNotNone(result["short_liquidation_zone"])
        self.assertEqual(len(result["clusters"]), 2)


if __name__ == "__main__":
    unittest.main()
