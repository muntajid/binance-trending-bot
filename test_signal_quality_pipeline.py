"""Offline regression tests for enriched signal normalization and Square templates."""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot import signal_generator
from bot.poster import (
    assert_clean_post_text,
    format_explained_post,
    format_rapid_alert_post,
)


REQUIRED_SIGNAL_FIELDS = (
    "direction",
    "tp1",
    "tp2",
    "sl",
    "confidence",
    "rapid_reason",
    "why_now",
    "invalidation",
    "smc_logic",
    "smc_logic_short",
    "technical_analysis",
    "indicators_summary",
    "volume_analysis",
    "futures_data",
    "liquidity_analysis",
    "liquidation_heatmap",
    "mtf_analysis",
    "market_context",
    "sentiment_onchain",
    "volatility_assessment",
    "risk_management",
    "validation_score",
    "macro_factors",
    "fundamental_note",
)


class SignalNormalizationTests(unittest.TestCase):
    def _research(self):
        return {
            "indicators_1h": {
                "rsi": 57.4,
                "ema_20": 101,
                "ema_50": 99,
                "ema_200": 93,
                "atr": 2.2,
                "bollinger_bands": {"bandwidth": 4.1},
            },
            "indicators_4h": {"trendlines": {"trend": "bullish"}},
            "timeframe_trends": {"1h": "bullish", "4h": "bullish"},
            "volume_analysis": {
                "buying_volume": 1500,
                "selling_volume": 800,
                "volume_spike": True,
                "average_volume": 100,
                "latest_volume": 180,
                "estimated": False,
            },
            "futures": {
                "open_interest": {"open_interest_usdt": 2_500_000},
                "funding_rate": {"funding_rate_pct": 0.01},
                "long_short_ratio": {"long_short_ratio": 1.2},
                "liquidation_levels": {
                    "long_liquidation_zone": {"mid": 95},
                    "short_liquidation_zone": {"mid": 108},
                    "estimated": True,
                },
            },
            "orderbook": {"imbalance": "bid_heavy", "bid_depth_usdt": 120_000},
            "market_context": {
                "bitcoin_trend": "bullish",
                "btc_dominance": {"btc_dominance_percent": 54.3, "estimated": True},
                "fear_greed": {"value": 62, "classification": "Greed"},
                "macro_factors": {"cpi_context": "unavailable"},
                "fundamental_notes": "Token event information is unverified.",
            },
        }

    def test_all_required_fields_have_safe_defaults_and_verified_entry(self):
        result = signal_generator._normalize_signal(
            {"direction": "LONG", "tp1": 110, "tp2": 115, "sl": 95},
            coin="ETH",
            live_price=100,
            change=3.2,
            trending_bonus="BTC",
            model="offline-test",
            research=self._research(),
        )
        self.assertEqual(result["entry_price"], 100)
        self.assertEqual(result["entry"], "CMP")
        self.assertEqual(result["confidence"], 85)
        self.assertTrue(all(field in result for field in REQUIRED_SIGNAL_FIELDS))
        self.assertEqual(result["volume_analysis"]["volume_spike"], True)
        self.assertEqual(result["futures_data"]["open_interest"]["open_interest_usdt"], 2_500_000)
        self.assertEqual(result["market_context"]["btc_dominance"]["btc_dominance_percent"], 54.3)
        self.assertIn("risk_reward_tp1", result["risk_management"])
        self.assertEqual(result["validation_score"], 85)

    def test_unusable_model_levels_are_repaired_from_research(self):
        # Unordered LONG levels are replaced by deterministic ATR/SR levels.
        repaired = signal_generator._normalize_signal(
            {"direction": "LONG", "tp1": 99, "tp2": 110, "sl": 95},
            coin="ETH",
            live_price=100,
            change=1,
            trending_bonus="BTC",
            model="offline-test",
            research=self._research(),
        )
        self.assertTrue(repaired["sl"] < 100 < repaired["tp1"] < repaired["tp2"])

        # Unordered SHORT levels are repaired too.
        repaired_short = signal_generator._normalize_signal(
            {"direction": "SHORT", "tp1": 101, "tp2": 90, "sl": 105},
            coin="ETH",
            live_price=100,
            change=-1,
            trending_bonus="BTC",
            model="offline-test",
            research=self._research(),
        )
        self.assertTrue(
            repaired_short["tp2"] < repaired_short["tp1"] < 100 < repaired_short["sl"]
        )

        # Null levels (the CI failure mode) no longer raise; they are derived.
        derived = signal_generator._normalize_signal(
            {"direction": "LONG", "tp1": None, "tp2": None, "sl": None},
            coin="ETH",
            live_price=100,
            change=1,
            trending_bonus="BTC",
            model="offline-test",
            research=self._research(),
        )
        self.assertTrue(derived["sl"] < 100 < derived["tp1"] < derived["tp2"])

        # String levels with currency noise are coerced, not rejected.
        coerced = signal_generator._normalize_signal(
            {"direction": "LONG", "tp1": "$110.0", "tp2": "115", "sl": "95"},
            coin="ETH",
            live_price=100,
            change=1,
            trending_bonus="BTC",
            model="offline-test",
        )
        self.assertEqual((coerced["tp1"], coerced["tp2"], coerced["sl"]), (110.0, 115.0, 95.0))

        # Valid model levels are retained untouched.
        retained = signal_generator._normalize_signal(
            {"direction": "LONG", "tp1": 110, "tp2": 115, "sl": 95},
            coin="ETH",
            live_price=100,
            change=1,
            trending_bonus="BTC",
            model="offline-test",
        )
        self.assertEqual((retained["tp1"], retained["tp2"], retained["sl"]), (110, 115, 95))

    def test_derived_levels_stay_ordered_without_research(self):
        for direction, live_price in (("LONG", 0.0589), ("SHORT", 0.0589), ("LONG", 118_000.0)):
            tp1, tp2, sl = signal_generator._derive_levels(direction, live_price, None)
            if direction == "LONG":
                self.assertTrue(sl < live_price < tp1 < tp2)
            else:
                self.assertTrue(tp2 < tp1 < live_price < sl)

    def test_groq_chat_kwargs_give_reasoning_models_headroom(self):
        kwargs = signal_generator._groq_chat_kwargs("openai/gpt-oss-20b", 16384)
        self.assertEqual(kwargs["max_tokens"], 16384)
        self.assertEqual(kwargs["extra_body"], {"reasoning_effort": "low"})
        plain = signal_generator._groq_chat_kwargs("qwen/qwen3.6-27b", 4096)
        self.assertNotIn("extra_body", plain)

    def test_prompt_requires_full_schema_and_repairs_invalid_model_levels(self):
        invalid = {"direction": "LONG", "tp1": 99, "tp2": 115, "sl": 95}

        def response(payload):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            )

        client = Mock()
        client.chat.completions.create.side_effect = [response(invalid)]
        with patch.dict(os.environ, {"GROQ_API_KEY": "offline-test"}), patch.object(
            signal_generator, "Groq", return_value=client
        ), patch.object(
            signal_generator, "get_live_price", return_value=100.0
        ), patch.object(
            signal_generator, "_build_institutional_research", return_value=self._research()
        ), patch.object(
            signal_generator,
            "_groq_model_candidates",
            return_value=["test/model-a"],
        ):
            result = signal_generator.generate_signal_with_groq(
                "ETH", 100.0, 3.2, "BTC"
            )

        # One completion is enough: unusable levels are repaired, not retried.
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertEqual(result["entry_price"], 100.0)
        self.assertTrue(result["sl"] < 100.0 < result["tp1"] < result["tp2"])
        self.assertTrue(all(field in result for field in REQUIRED_SIGNAL_FIELDS))
        messages = client.chat.completions.create.call_args_list[0].kwargs["messages"]
        user_prompt = next(message["content"] for message in messages if message["role"] == "user")
        self.assertIn("Verified Binance live price (Entry CMP): 100.0", user_prompt)
        for field in REQUIRED_SIGNAL_FIELDS:
            self.assertIn(f'"{field}"', user_prompt)
        self.assertIn("CPI", user_prompt)
        # Nested research blocks are auto-attached, so the prompt must not ask
        # the model to emit them (keeps the JSON document small).
        self.assertIn("Do NOT output these keys", user_prompt)
        self.assertIn("liquidation heatmap", user_prompt.lower())


class MarketOverviewPromptTests(unittest.TestCase):
    def test_market_overview_receives_btc_eth_indicators_dominance_and_sentiment(self):
        btc = {"price": 100_000.0, "change_24h": 1.5, "quote_volume_24h": 9_000_000_000}
        eth = {"price": 3_000.0, "change_24h": 2.1, "quote_volume_24h": 4_000_000_000}
        indicators = [
            {"rsi": 52.1, "ema_20": 99_000, "ema_50": 98_000, "ema_200": 95_000},
            {"rsi": 57.3, "ema_20": 100_000, "ema_50": 97_000, "ema_200": 92_000},
            {"rsi": 54.2, "ema_20": 2_950, "ema_50": 2_900, "ema_200": 2_800},
            {"rsi": 59.4, "ema_20": 2_900, "ema_50": 2_850, "ema_200": 2_700},
        ]
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "headline": "BTC holds structure",
                                "btc_support": 98_000,
                                "btc_resistance": 102_000,
                            }
                        )
                    )
                )
            ]
        )
        market_context = {
            "btc_dominance": {"btc_dominance_percent": 53.7, "estimated": True},
            "fear_greed": {"value": 61, "classification": "Greed", "estimated": False},
            "macro_factors": {"cpi_context": "unavailable"},
        }
        with patch.dict(os.environ, {"GROQ_API_KEY": "offline-test"}), patch.object(
            signal_generator, "Groq", return_value=client
        ), patch.object(
            signal_generator, "get_24h_data", side_effect=[btc, eth]
        ), patch.object(
            signal_generator,
            "get_klines_multi_timeframe",
            return_value={frame: [] for frame in ("5m", "15m", "1h", "4h", "1d", "1w")},
        ), patch.object(
            signal_generator, "calculate_all_indicators", side_effect=indicators
        ), patch.object(
            signal_generator, "get_market_context", return_value=market_context
        ), patch.object(
            signal_generator, "_groq_model_candidates", return_value=["test/overview"]
        ):
            result = signal_generator.generate_market_overview_with_groq(
                [{"coin": "ETH", "change": 4.0}]
            )

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        user_prompt = next(message["content"] for message in messages if message["role"] == "user")
        self.assertIn('"btc_indicators_1h"', user_prompt)
        self.assertIn('"eth_indicators_4h"', user_prompt)
        self.assertIn('"btc_dominance"', user_prompt)
        self.assertIn('"fear_greed"', user_prompt)
        self.assertIn("52.1", user_prompt)
        self.assertIn("2900", user_prompt)
        self.assertEqual(result["btc_dominance"], market_context["btc_dominance"])
        self.assertEqual(result["fear_greed"], market_context["fear_greed"])
        self.assertEqual(result["btc_indicators_1h"]["rsi"], 52.1)


class SignalPostTests(unittest.TestCase):
    def _signal(self):
        research = SignalNormalizationTests()._research()
        return {
            "coin": "ETH",
            "trending_bonus": "BTC",
            "direction": "LONG",
            "entry_price": 100,
            "tp1": 110,
            "tp2": 118,
            "sl": 95,
            "confidence": 84,
            "change": 5.4,
            "rapid_reason": "1H breakout with confirmed volume; watch the prior resistance retest.",
            "why_now": "A measured 1H close cleared resistance while participation expanded.",
            "invalidation": "A 1H close below 95 invalidates the setup.",
            "smc_logic": "Price reclaimed a prior range high. Wait for continuation confirmation.",
            "technical_analysis": "Bullish 1H structure with a retest of prior resistance and measured 4H support.",
            "indicators_summary": "EMA20 above EMA50 and EMA200; RSI 57; MACD positive; price above VWAP; BB expanding; Stoch RSI mid-range.",
            "volume_analysis": research["volume_analysis"],
            "futures_data": research["futures"],
            "liquidity_analysis": research["orderbook"],
            "liquidation_heatmap": research["futures"]["liquidation_levels"],
            "mtf_analysis": {"5m": "bullish", "15m": "bullish", "1h": "bullish", "4h": "bullish", "1d": "neutral", "1w": "neutral"},
            "market_context": research["market_context"],
            "sentiment_onchain": {"fear_greed": "62 Greed", "social_sentiment": "estimated neutral", "on_chain_proxy": "estimated unavailable"},
            "volatility_assessment": {"atr_1h": 2.2, "atr_percent": 2.2, "bollinger_bandwidth": 4.1, "estimated": False},
            "risk_management": {"risk_reward_tp1": 2.0, "position_sizing": "Use a predefined account-risk limit", "invalidation_plan": "Close below 95"},
            "validation_score": 82,
            "macro_factors": {"fed_rate_context": "unavailable", "cpi_context": "unavailable", "dxy_proxy": "unavailable"},
            "fundamental_note": "No confirmed token catalyst; unlock/listing information remains unverified.",
            "analysis_summary": "Price, spot volume, market structure, and derivatives were cross-checked; estimated fields are labelled.",
            "institutional_analysis": research,
        }

    def test_rapid_post_has_required_evidence_and_stays_120_to_300_chars(self):
        post = format_rapid_alert_post(self._signal())
        self.assertGreaterEqual(len(post), 120)
        self.assertLessEqual(len(post), 300)
        self.assertIn("Entry CMP", post)
        self.assertIn("TP1", post)
        self.assertIn("TP2", post)
        self.assertIn("SL", post)
        self.assertIn("RR", post)
        self.assertIn("RSI", post)
        self.assertIn("VOL", post)
        self.assertIn("OI", post)
        self.assertIn("MTF", post)
        self.assertIn("Liq", post)
        self.assertIn("Bonus", post)
        self.assertIn("CTA:", post)
        self.assertIn("#", post)
        self.assertIn("not financial advice", post.lower())
        self.assert_clean(post)

    def test_explained_post_has_all_sections_and_stays_in_target_range(self):
        post = format_explained_post(self._signal())
        self.assertGreaterEqual(len(post), 900)
        self.assertLessEqual(len(post), 1900)
        self.assertTrue(post.startswith("👀 $ETH LONG | ENTRY CMP"))
        for section in (
            "TECHNICAL ANALYSIS",
            "INDICATORS",
            "VOLUME & FUTURES",
            "LIQUIDITY & LIQUIDATION HEATMAP",
            "MULTI-TIMEFRAME",
            "MARKET / SENTIMENT / ON-CHAIN",
            "MACRO & FUNDAMENTAL",
            "VOLATILITY & VALIDATION",
            "RISK MANAGEMENT",
            "CTA:",
            "Tags:",
            "not financial advice",
        ):
            self.assertIn(section.lower(), post.lower())
        self.assert_clean(post)

    def assert_clean(self, post):
        self.assertEqual(assert_clean_post_text(post), post)
        for marker in ("\u00f0\u0178", "\u00e2\u0153", "\u00e2\u0161", "\ufffd"):
            self.assertNotIn(marker, post)
        post.encode("utf-8", errors="strict")


if __name__ == "__main__":
    unittest.main()
