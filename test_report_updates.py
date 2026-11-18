"""Offline acceptance tests for the update report and the user's exceptions."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from bot import main_signal, monitor, pacer, poster, schedule, signal_generator as sg
from bot.content_quality import SignalContentError, clip_sentence
from test_pacer import FakeClock, FakeGitHub
from test_slot_cadence import TempStateMixin

UTC = dt.timezone.utc
PROSE = {
    "why_now": "The hourly close reclaimed resistance while measured participation expanded.",
    "rapid_reason": "Price is above VWAP with both measured trend readings bullish.",
}


def research(direction="LONG"):
    bullish = direction == "LONG"
    trend = "bullish" if bullish else "bearish"
    return {
        "timeframe_trends": {"1h": trend, "4h": trend},
        "indicators_1h": {"atr": 2.0, "rsi": 58 if bullish else 42,
                          "vwap": 99 if bullish else 101},
    }


def draft(**overrides):
    return {**PROSE, "direction": "LONG", "tp1": 104.0, "tp2": 108.0, "sl": 98.0,
            "hook_line": "ETH holds its reclaim as measured momentum improves", **overrides}


def normalize(payload=None, *, snapshot=None, **options):
    return sg._normalize_signal(
        draft() if payload is None else payload, coin="ETH", live_price=100.0,
        change=6.0, trending_bonus="BTC", model="offline",
        research=research() if snapshot is None else snapshot, **options,
    )


class DraftQualityTests(unittest.TestCase):
    def test_placeholder_short_missing_and_instruction_prose_are_rejected(self):
        for field in ("why_now", "rapid_reason"):
            for value in (None, {}, 123, "", "Trigger.", "1H trigger.", "Only five words are here",
                          "One complete sentence of at least 6 words explaining the measured reason this setup exists now."):
                with self.subTest(field=field, value=value), self.assertRaises(SignalContentError):
                    normalize(draft(**{field: value}))
        with self.assertRaises(SignalContentError):
            normalize(draft(hook_line="ETH breakout now"))

    def test_placeholder_completion_is_regenerated_instead_of_published(self):
        def response(value):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)))])

        client = mock.Mock()
        client.chat.completions.create.side_effect = [
            response(draft(why_now="Trigger.", rapid_reason="1H trigger.")), response(draft()),
        ]
        with (
            mock.patch.dict(os.environ, {"GROQ_API_KEY": "offline"}),
            mock.patch.object(sg, "Groq", return_value=client),
            mock.patch.object(sg, "get_live_price", return_value=100),
            mock.patch.object(sg, "_build_institutional_research", return_value=research()),
            mock.patch.object(sg, "_groq_model_candidates", return_value=["offline"]),
        ):
            result = sg.generate_signal_with_groq("ETH", 100, 6, "BTC")
        self.assertEqual(result["why_now"], PROSE["why_now"])
        self.assertEqual(client.chat.completions.create.call_count, 2)
        self.assertIn("REGENERATION REQUIRED", client.chat.completions.create.call_args.kwargs["messages"][1]["content"])

    def test_persistent_placeholders_fail_closed_with_bounded_regeneration(self):
        client = mock.Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content=json.dumps(draft(why_now="Trigger."))))
        ])
        with (
            mock.patch.dict(os.environ, {"GROQ_API_KEY": "offline"}),
            mock.patch.object(sg, "Groq", return_value=client),
            mock.patch.object(sg, "get_live_price", return_value=100),
            mock.patch.object(sg, "_build_institutional_research", return_value=research()),
            mock.patch.object(sg, "_groq_model_candidates", return_value=["offline"]),
            self.assertRaises(sg.SignalQualityError),
        ):
            sg.generate_signal_with_groq("ETH", 100, 6, "BTC")
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_weak_rr_is_repaired_and_risk_text_is_not_trusted(self):
        signal = normalize(draft(tp1=100.2, tp2=101, sl=98,
                                 risk_management={"risk_reward": 99, "position_sizing": "all in"}))
        self.assertGreaterEqual(abs(signal["tp1"] - 100) / abs(100 - signal["sl"]), 1)
        self.assertGreaterEqual(abs(signal["tp2"] - 100) / abs(100 - signal["sl"]), 2)
        self.assertGreaterEqual(abs(signal["tp1"] - 100) / 2, 0.6)
        self.assertNotIn("all in", str(signal["risk_management"]))

    def test_tp1_atr_distance_is_enforced_even_when_rr_is_good(self):
        weak_distance = draft(tp1=100.5, tp2=101, sl=99.5)
        with mock.patch.object(sg, "_derive_levels", return_value=(100.5, 101, 99.5)):
            with self.assertRaisesRegex(sg.SignalQualityError, "ATR"):
                normalize(weak_distance)

    def test_failed_derived_rr_skips_instead_of_stretching_targets(self):
        with mock.patch.object(sg, "_derive_levels", return_value=(101, 102, 95)):
            with self.assertRaises(sg.SignalQualityError):
                normalize(draft(tp1=100.2, tp2=101, sl=98))
        with self.assertRaises(sg.SignalQualityError):
            normalize(trading_config={"min_rr_tp1": 3.0, "min_rr_tp2": 4.0})

    def test_missing_atr_or_conflicting_direction_cannot_publish(self):
        snapshot = research()
        snapshot["indicators_1h"]["atr"] = None
        with self.assertRaisesRegex(sg.SignalQualityError, "ATR"):
            normalize(snapshot=snapshot)
        with self.assertRaisesRegex(sg.SignalQualityError, "conflicts"):
            normalize(draft(direction="SHORT", tp1=96, tp2=92, sl=102))
        with self.assertRaises(sg.SignalQualityError):
            normalize(snapshot={})

    def test_rule_consensus_uses_trends_rsi_and_vwap_with_a_4h_veto(self):
        for side in ("LONG", "SHORT"):
            self.assertEqual(sg.rule_based_direction(100, research(side))[0], side)
        snapshot = research()
        snapshot["timeframe_trends"]["4h"] = "bearish"
        self.assertIsNone(sg.rule_based_direction(100, snapshot)[0])
        snapshot = research()
        snapshot["timeframe_trends"] = {"1h": "neutral", "4h": "neutral"}
        self.assertIsNone(sg.rule_based_direction(100, snapshot)[0])

    def test_rounding_is_rechecked_before_a_signal_is_accepted(self):
        # Below the market-sane precision, distinct model levels become equal.
        with mock.patch.object(sg, "_derive_levels", return_value=(100, 100, 100)):
            with self.assertRaises(sg.SignalQualityError):
                normalize(draft(tp1=100.00001, tp2=100.00002, sl=99.99999))


class EditorialReportTests(unittest.TestCase):
    def test_hooks_never_split_a_word_or_price_and_stay_within_70(self):
        text = "ETH shows measured buying pressure near $9.847631 while traders await confirmation."
        for limit in range(1, 71):
            with self.subTest(limit=limit):
                result = sg._sanitize_hook_line(text, max_chars=limit)
                self.assertLessEqual(len(result), limit)
                self.assertTrue(text.startswith(result))
                if result:
                    self.assertFalse(re.search(r"\$?\d[\d.]*$", result))
                    self.assertIn(text[len(result):len(result) + 1], (" ", ""))
        self.assertEqual(sg._sanitize_hook_line("x" * 100), "")
        self.assertLessEqual(len(sg._sanitize_hook_line(text, max_chars=90)), 70)
        clipped = clip_sentence("A sentence with a price of $1234.56789 and more words after it", 34)
        self.assertNotIn("$1234", clipped)

    def test_public_rr_is_computed_once_without_confidence_or_validation(self):
        signal = normalize(draft(confidence=1, validation_score=1,
                                 risk_management={"risk_reward": 99, "guidance": "Use 99R"}))
        for formatter in (poster.format_explained_post, poster.format_rapid_alert_post):
            text = formatter(signal)
            self.assertIn("RR 1:2.00", text)
            self.assertIn("RR 1:2.00 to TP1", text)
            self.assertNotIn("99", text)
            self.assertNotIn("Conf", text)
            self.assertNotIn("validation", text)
            poster.assert_post_quality(text, signal)

    def test_missing_feeds_are_omitted_and_available_parts_survive(self):
        signal = normalize()
        signal.update({"futures_data": {"open_interest": None, "funding_rate": None},
                       "macro_factors": {"cpi_context": "unavailable"},
                       "market_context": {"bitcoin_trend": "unavailable"}})
        for formatter in (poster.format_explained_post, poster.format_rapid_alert_post):
            text = formatter(signal)
            self.assertNotIn("unavailable", text.lower())
            self.assertNotIn("n/a", text.lower())
            self.assertNotIn("OI ", text)
            self.assertNotIn("funding", text)
        self.assertEqual(poster._fmt_futures_line(None), "")
        self.assertEqual(poster._fmt_macro_line({"cpi_context": "unavailable"}, "unverified"), "")
        self.assertEqual(poster._fmt_liquidity_line({"imbalance": "unavailable"}, None), "")
        self.assertEqual(poster._fmt_trend_stack({"1h": "unavailable"}), "")
        available = poster._fmt_futures_line({"open_interest": None, "funding_rate": {"funding_rate_pct": 0.01}})
        self.assertIn("funding 0.01%", available)
        self.assertNotIn("OI", available)

    def test_only_relevant_cashtags_with_no_duplicates_or_reward_tags(self):
        signal = normalize()
        signal["trending_bonus"] = "RARE"
        signal["why_now"] = "The measured $ETH setup agrees with $BTC while $RARE moves elsewhere."
        for formatter in (poster.format_explained_post, poster.format_rapid_alert_post):
            text = formatter(signal)
            tags = re.findall(r"\$([A-Za-z][A-Za-z0-9]*)\b", text)
            self.assertLessEqual(len(tags), 2)
            self.assertEqual(len(tags), len(set(tags)))
            self.assertLessEqual(set(tags), {"ETH", "BTC"})
            for absent in ("$RARE", "#Write2Earn", "#BinanceSquareFamily"):
                self.assertNotIn(absent, text)
        with mock.patch.object(poster, "_content_config", return_value={"max_cashtags": 1}):
            text = poster.format_explained_post(signal)
            self.assertLessEqual(len(re.findall(r"\$[A-Za-z]+", text)), 1)

    def test_small_token_prices_are_not_rounded_into_partial_or_zero_prices(self):
        text_signal = sg._normalize_signal(
            draft(tp1=0.00000038, tp2=0.00000040, sl=0.00000034,
                  hook_line="BTTC holds its reclaim as measured momentum improves"),
            coin="BTTC", live_price=0.00000036, change=6, trending_bonus="BTC", model="offline",
            research={"timeframe_trends": {"1h": "bullish", "4h": "bullish"},
                      "indicators_1h": {"atr": 0.00000002, "rsi": 58, "vwap": 0.00000035}},
        )
        text = poster.format_explained_post(text_signal)
        for value in ("Entry CMP $0.00000036", "TP1 $0.00000038", "TP2 $0.0000004", "SL $0.00000034"):
            self.assertIn(value, text)

    def test_humor_is_explicitly_fictional(self):
        text = poster.format_meme_community_post({"title": "Trader thoughts", "content": "Me: coffee. Also me: chart.",
                                                 "lesson": "Use a plan", "question": "Who relates?"})
        self.assertIn("Fictional trading humor", text)

    def test_stock_gold_and_configured_exclusions_apply_to_every_trending_call(self):
        config = main_signal.load_config()
        excluded = config["trending"]["exclude_coins"]
        tickers = [{"symbol": coin + "USDT", "priceChangePercent": "10", "quoteVolume": "10000000", "lastPrice": "100"}
                   for coin in [*excluded, "ETH", "BTC"]]
        with mock.patch.object(sg, "get_ticker_24h_all", return_value=tickers):
            self.assertEqual({row["coin"] for row in sg.get_trending_coins()}, {"ETH", "BTC"})
            self.assertNotIn("ETH", {row["coin"] for row in sg.get_trending_coins(exclude_coins=["ETHUSDT"])})
        with mock.patch.object(sg, "get_ticker_24h_all", return_value=tickers[:len(excluded)]):
            self.assertEqual(sg.get_trending_coins(), [])


class EngineReportTests(unittest.TestCase, TempStateMixin):
    def setUp(self):
        self.make_temp_state()

    def test_next_candidate_is_tried_without_state_for_a_rejected_setup(self):
        candidates = [{"coin": "BAD", "change": 9, "volume": 2e7, "price": 100},
                      {"coin": "ETH", "change": 6, "volume": 1e7, "price": 100}]
        good = normalize()
        good["entry_price"] = 100.2
        with (
            mock.patch.object(main_signal, "get_trending_coins", return_value=candidates),
            mock.patch.object(main_signal, "get_live_price", return_value=100),
            mock.patch.object(main_signal, "generate_signal_with_groq", side_effect=[sg.SignalQualityError("RR failed"), good]) as generate,
            mock.patch.object(main_signal, "generate_single_chart", return_value="chart.png") as chart,
            mock.patch.object(main_signal, "validate_chart", return_value=True),
            mock.patch.object(main_signal, "save_active", return_value={"trade_id": "good"}) as save,
            mock.patch.object(main_signal, "record_posted", return_value="stamp") as posted,
        ):
            _, payload, _, effects = main_signal.build_signal_post("explained_setup", main_signal.load_config())
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(generate.call_args.kwargs["trending_bonus"], "BTC")
        self.assertEqual(payload["entry_price"], 100.2)
        self.assertEqual(effects["coin"], "ETH")
        chart.assert_called_once()
        save.assert_called_once_with(payload)
        posted.assert_called_once_with("ETH")

    def test_all_quality_failures_create_no_chart_trade_or_cooldown(self):
        candidates = [{"coin": coin, "change": 9, "volume": 1e7, "price": 100} for coin in ("BAD", "WORSE")]
        with (
            mock.patch.object(main_signal, "get_trending_coins", return_value=candidates),
            mock.patch.object(main_signal, "get_live_price", return_value=100),
            mock.patch.object(main_signal, "generate_signal_with_groq", side_effect=sg.SignalQualityError("RR failed")) as generate,
            mock.patch.object(main_signal, "generate_single_chart") as chart,
            mock.patch.object(main_signal, "save_active") as save,
            mock.patch.object(main_signal, "record_posted") as posted,
            self.assertRaises(main_signal.NoPostableSignal),
        ):
            main_signal.build_signal_post("explained_setup", main_signal.load_config())
        self.assertEqual(generate.call_count, 2)
        for operation in (chart, save, posted):
            operation.assert_not_called()

    def test_empty_strict_pool_never_relaxes_momentum(self):
        with mock.patch.object(main_signal, "get_trending_coins", return_value=[]) as trending:
            with self.assertRaises(main_signal.NoPostableSignal):
                main_signal.select_trending_coin(main_signal.load_config())
        self.assertEqual(trending.call_count, 1)
        self.assertEqual(trending.call_args.kwargs["min_change"], 5)

    def test_quality_skip_advances_without_republishing_a_stale_artifact(self):
        output = self.data_dir / "github_output"
        Path(main_signal.LATEST_POST_FILE).write_text("old post must not be republished")
        with (
            mock.patch.dict(os.environ, {"SLOT": "explained_setup", "PREVIEW_ONLY": "0", "GITHUB_OUTPUT": str(output)}),
            mock.patch.object(main_signal, "build_signal_post", side_effect=main_signal.NoPostableSignal("No quality setup")),
        ):
            main_signal.run()
        self.assertEqual(output.read_text(), "ready=false\n")
        state = self.state()
        self.assertIsNone(state["in_flight"])
        self.assertNotIn(1, state["pending"])
        self.assertEqual(state["published"][-1]["mode"], "skipped")
        self.assertIsNone(state["published"][-1]["published_utc"])
        self.assertEqual(schedule.status()["published_today"], 0)
        self.assertEqual(schedule.status()["skipped_today"], 1)
        self.assertFalse(schedule.local_due_status()["due"])
        self.assertFalse((self.data_dir / "active_trades.json").exists())
        self.assertFalse((self.data_dir / "posted_coins.json").exists())

    def test_preview_quality_skip_preserves_the_queue(self):
        with (
            mock.patch.dict(os.environ, {"SLOT": "explained_setup", "PREVIEW_ONLY": "1"}),
            mock.patch.object(main_signal, "build_signal_post", side_effect=main_signal.NoPostableSignal("No quality setup")),
        ):
            main_signal.run()
        self.assertIn(1, self.state()["pending"])
        self.assertEqual(self.state()["published"], [])

    def test_weekly_recap_and_ready_output_are_wired_into_workflow(self):
        workflow = Path(".github/workflows/signal.yml").read_text()
        self.assertIn('cron: "17 18 * * 0"', workflow)
        self.assertIn("&& 'scorecard'", workflow)
        self.assertIn("id: generate", workflow)
        self.assertEqual(workflow.count("steps.generate.outputs.ready == 'true' && inputs.preview != true"), 3)


class CadenceReportTests(unittest.TestCase):
    def test_stable_jitter_has_one_release_time_and_respects_bounds(self):
        last = dt.datetime(2026, 11, 18, tzinfo=UTC)
        state = {"pending": [1, 2], "rotations": 1, "published": [{"published_utc": last.isoformat()}]}
        now = last + dt.timedelta(minutes=230)
        verdict = schedule.due_status(state, now=now, min_gap=230, jitter_min=3, jitter_max=40)
        jitter = verdict["jitter_minutes"]
        self.assertGreaterEqual(jitter, 3)
        self.assertLessEqual(jitter, 40)
        self.assertFalse(verdict["due"])
        release = last + dt.timedelta(minutes=230 + jitter)
        self.assertFalse(schedule.due_status(state, now=release-dt.timedelta(seconds=1), min_gap=230, jitter_min=3, jitter_max=40)["due"])
        self.assertTrue(schedule.due_status(state, now=release, min_gap=230, jitter_min=3, jitter_max=40)["due"])
        allowed, _ = pacer.decide(now=release, state=state, signal_active=False, dispatches_this_hour=0,
                                  max_dispatches=3, min_gap=230, jitter_min=3, jitter_max=40)
        self.assertTrue(allowed)

    def test_jitter_varies_between_opportunities_and_midnight_does_not_reset_gap(self):
        jitter_values = set()
        for day in range(1, 20):
            last = dt.datetime(2026, 11, day, 23, 55, tzinfo=UTC)
            state = {"published": [{"published_utc": last.isoformat()}]}
            verdict = schedule.due_status(state, now=last+dt.timedelta(minutes=10), min_gap=230,
                                          jitter_min=3, jitter_max=40)
            self.assertFalse(verdict["due"])
            jitter_values.add(verdict["jitter_minutes"])
        self.assertGreater(len(jitter_values), 5)

    def test_first_post_waits_for_the_randomized_window(self):
        now = dt.datetime(2026, 11, 18, 8, tzinfo=UTC)
        first = schedule.due_status(None, now=now, min_gap=230, jitter_min=3, jitter_max=40)
        self.assertFalse(first["due"])
        release = now+dt.timedelta(minutes=first["jitter_minutes"])
        self.assertTrue(schedule.due_status(None, now=release, min_gap=230, jitter_min=3, jitter_max=40)["due"])

    def test_first_release_does_not_move_forward_on_hourly_backup_retries(self):
        start = dt.datetime(2026, 11, 18, 8, tzinfo=UTC)
        state = {"rotation_started_utc": start.isoformat(), "pending": [0]}
        first = schedule.due_status(state, now=start, min_gap=230, jitter_min=3, jitter_max=40)
        later = schedule.due_status(state, now=start+dt.timedelta(hours=1), min_gap=230, jitter_min=3, jitter_max=40)
        self.assertFalse(first["due"])
        self.assertTrue(later["due"])
        self.assertEqual(first["jitter_minutes"], later["jitter_minutes"])

    def test_real_config_pacer_simulation_is_not_hourly_anymore(self):
        clock = FakeClock(dt.datetime(2026, 11, 18, tzinfo=UTC))
        github = FakeGitHub(clock)
        with mock.patch.object(pacer, "_log"):
            for _ in range(5):
                pacer.run(client=github, ref="test", my_run_id=500, now=clock.now, sleep=clock.sleep)
        stamps = [dt.datetime.fromisoformat(value) for value in github.published]
        first_day = [value for value in stamps if value.date() == dt.date(2026, 11, 18)]
        self.assertGreaterEqual(len(first_day), 5)
        self.assertLessEqual(len(first_day), 7)
        for before, after in zip(stamps, stamps[1:]):
            self.assertGreaterEqual((after-before).total_seconds()/60, 233)
        self.assertGreater(len({value.minute for value in stamps}), 1)


class PreservedTargetBehaviorTests(unittest.TestCase):
    def test_multiple_tp_hits_still_all_post_without_new_limits(self):
        now = dt.datetime.now(UTC)
        trades = [{"trade_id": str(i), "coin": coin, "direction": "LONG", "entry_price": 100,
                   "tp1": 110, "tp2": 120, "sl": 95, "created_at": now.isoformat(), "status": "ACTIVE"}
                  for i, coin in enumerate(("ETH", "BTC", "SOL", "ADA"))]
        publish = mock.Mock()

        def target(callback, trade, event):
            callback(f"{trade['coin']} {event.kind}", ["unchanged-position-card.png"])
            return "unchanged-position-card.png"

        with (
            mock.patch.object(monitor, "load_active", return_value=trades),
            mock.patch.object(monitor, "save_active"), mock.patch.object(monitor, "save_closed"),
            mock.patch.object(monitor, "get_current_price", return_value=121),
            mock.patch.object(monitor, "_scan_trade_history", return_value=monitor.ScanResult(
                terminal_event=monitor.TradeEvent("TP2", 120, now))),
            mock.patch.object(monitor, "_publish_target_hit", side_effect=target),
        ):
            monitor.check_trades_and_maybe_post(publish)
        self.assertEqual(publish.call_count, 4)

    def test_target_hit_text_keeps_the_existing_template_exactly(self):
        signal = {"coin": "ETH", "entry_price": 100, "tp2": 120, "direction": "LONG"}
        expected = (
            "🎯 $ETH TP1 HIT — +10.00% MOVE DELIVERED! 🚀\n\n"
            "• Entry: $100\n• Target Hit: $110\n• Bias: LONG\n\n"
            "Next target: TP2 at $120 🎯\n💡 Pro Tip: Move Stop Loss to Entry ($100) now for a risk-free ride."
            "\n\n$ETH $BTC #Write2Earn #TargetHit\n"
        )
        with mock.patch.object(poster._RNG, "choice", side_effect=lambda options: options[0]):
            self.assertEqual(poster.format_success_post(signal, "TP1", 110), expected)


if __name__ == "__main__":
    unittest.main()
