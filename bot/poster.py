"""Evidence-based Binance Square post templates built from top 100 creator benchmark data.

Supports 4 core publication modes:
1. Rapid Alert Mode (verified levels plus a concise 15-point evidence summary)
2. Explained Master Setup Mode (15 numbered findings, Why Now, and Invalidation)
3. Macro & Bitcoin Market Overview Mode (BTC structure, flows, and market phases)
4. Viral Meme & Community Culture Mode (trader psychology and community culture)

Plus event-driven verifiable Target Hit updates with 0% forced A/B bait.
"""

from __future__ import annotations

import difflib
import json
import math
import os
import random
import re
from pathlib import Path
from typing import Any

from bot.market_data import get_24h_data

_RNG = random.SystemRandom()
ROOT_DIR = Path(__file__).resolve().parents[1]
TEMPLATE_STATE_FILE = ROOT_DIR / "data" / "post_template_state.json"
CAPTIONS_HISTORY_FILE = ROOT_DIR / "data" / "posted_captions.json"
MAX_SQUARE_POST_CHARACTERS = 2100

# Markers for mojibake detection
_MOJIBAKE_MARKERS = (
    "\u00f0\u0178",
    "\u00e2\u0153",
    "\u00e2\u0161",
    "\u00ef\u00b8",
    "\u00c2",
    "\ufffd",
)


def assert_clean_post_text(text: str) -> str:
    """Reject empty, oversized, or visibly mojibaked Square post text."""

    value = str(text)
    if not value.strip():
        raise ValueError("Binance Square post text cannot be empty")

    for marker in _MOJIBAKE_MARKERS:
        if marker in value:
            raise ValueError(
                "Mojibake detected in Binance Square post text; publication blocked"
            )

    value.encode("utf-8", errors="strict").decode("utf-8", errors="strict")

    if len(value) > MAX_SQUARE_POST_CHARACTERS:
        raise ValueError(
            f"Binance Square post is {len(value)} characters; maximum is {MAX_SQUARE_POST_CHARACTERS}"
        )

    return value


def _finalize_post(text: str) -> str:
    return assert_clean_post_text(text.strip() + "\n")


def _direction(signal: dict[str, Any]) -> str:
    value = str(signal.get("direction", "LONG")).upper().strip()
    if value not in {"LONG", "SHORT"}:
        raise ValueError(f"Unsupported direction: {value}")
    return value


def _move_percent(entry: float, target: float, direction: str) -> float:
    if entry <= 0 or target <= 0:
        raise ValueError("Entry and target prices must be greater than zero")

    if direction == "LONG":
        return ((target - entry) / entry) * 100.0
    return ((entry - target) / entry) * 100.0


def _format_price(value: float) -> str:
    value = float(value)
    if value <= 0:
        raise ValueError("Price must be greater than zero")

    if value >= 1000:
        decimals = 2
    elif value >= 1:
        decimals = 4
    else:
        decimals = 6

    result = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return result if result else "0"


def _risk_reward(entry: float, target: float, stop: float) -> float:
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0:
        raise ValueError("Entry and stop cannot be equal")
    return reward / risk


# ---------------------------------------------------------------------------
# Near-Duplicate Guard (Protects -12.7% duplicate penalty)
# ---------------------------------------------------------------------------

def _normalize_text_for_similarity(text: str) -> str:
    """Normalize text by removing digits, prices, emojis, and hashtags."""
    cleaned = re.sub(r"\$[\d,.]+|\b\d+\.?\d*\b", " ", text)
    cleaned = re.sub(r"[#$]\w+", " ", cleaned)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    return " ".join(cleaned.lower().split())


def check_and_record_similarity(caption: str, max_similarity: float = 0.82) -> bool:
    """Return True if caption is sufficiently unique, and record it in history."""
    normalized_new = _normalize_text_for_similarity(caption)
    if not normalized_new:
        return True

    history: list[str] = []
    if CAPTIONS_HISTORY_FILE.exists():
        try:
            loaded = json.loads(CAPTIONS_HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = [str(item) for item in loaded if item]
        except Exception:
            history = []

    for previous in history[-30:]:
        sim = difflib.SequenceMatcher(None, normalized_new, previous).ratio()
        if sim > max_similarity:
            print(f"[Duplicate Guard] Caption too similar ({sim:.2f} > {max_similarity}); rotating variant")
            return False

    history.append(normalized_new)
    CAPTIONS_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    CAPTIONS_HISTORY_FILE.write_text(
        json.dumps(history[-50:], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


# ---------------------------------------------------------------------------
# Dynamic Hashtag & CTA Distributions (Based on 136k Post Benchmark)
# ---------------------------------------------------------------------------

DYNAMIC_HASHTAGS = (
    "#Write2Earn",
    "#BinanceSquareFamily",
    "#CryptoTrading",
    "#BinanceSquare",
    "#Altcoins",
)


def _get_dynamic_hashtag() -> str:
    """Return exactly 1-2 relevant dynamic hashtags per benchmark."""
    return _RNG.choice(DYNAMIC_HASHTAGS)


def _get_cta_prompt(coin: str) -> str:
    """
    Evidence-based CTA distribution:
    - 65% No CTA (Clean authoritative analyst tone)
    - 20% Natural coin-specific discussion question
    - 10% Value-based follow prompt
    - 5% Explicit comment prompt
    - 0% Forced A/B
    """
    roll = _RNG.random()

    if roll < 0.65:
        return ""  # 65% No CTA

    if roll < 0.85:
        # 20% Natural coin question
        questions = (
            f"Where do you see ${coin} by end of week?",
            f"Are you trading this ${coin} setup or watching the reaction?",
            f"What is your target for ${coin} on this structure?",
        )
        return f"\n{_RNG.choice(questions)}"

    if roll < 0.95:
        # 10% Value-based follow prompt
        return f"\nFollow for real-time TP updates & verified setups."

    # 5% Explicit comment prompt
    return f"\nDrop your price prediction for ${coin} below."


def _clip(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _first_value(*values: Any) -> Any:
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, (dict, list, tuple)) and not value:
            continue
        return value
    return None


def _nested_value(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "confirmed", "spike", "bullish"}:
            return True
        if text in {"false", "no", "normal", "none", "unavailable", "unknown"}:
            return False
    return None


def _compact_number(value: Any, digits: int = 2) -> str:
    number = _as_number(value)
    if number is None:
        return "n/a"
    text = f"{number:,.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _compact_money(value: Any) -> str:
    number = _as_number(value)
    if number is None:
        return "n/a"
    absolute = abs(number)
    if absolute >= 1_000_000_000:
        return f"${number / 1_000_000_000:.1f}B"
    if absolute >= 1_000_000:
        return f"${number / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"${number / 1_000:.1f}K"
    return f"${number:.0f}"


def _describe_data(value: Any, limit: int = 180) -> str:
    """Render nested signal evidence as compact readable prose, not raw JSON."""
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in list(value.items())[:12]:
            label = str(key).replace("_", " ")
            if isinstance(item, bool):
                rendered = "yes" if item else "no"
            elif isinstance(item, (dict, list, tuple)):
                rendered = _describe_data(item, 65)
            elif isinstance(item, float):
                rendered = _compact_number(item, 3)
            elif item is None:
                rendered = "unavailable"
            else:
                rendered = str(item)
            parts.append(f"{label}: {rendered}")
        text = "; ".join(parts) if parts else "unavailable"
    elif isinstance(value, (list, tuple)):
        text = ", ".join(_describe_data(item, 35) for item in list(value)[:5])
    elif value is None:
        text = "unavailable"
    else:
        text = str(value)
    return _clip(text, limit)


def _trend_short(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("trend", value.get("bias", "unavailable"))
    text = str(value or "unavailable").strip().lower()
    if any(word in text for word in ("bull", "up", "positive", "higher")):
        return "up"
    if any(word in text for word in ("bear", "down", "negative", "lower")):
        return "down"
    if any(word in text for word in ("sideways", "range", "neutral")):
        return "flat"
    return "n/a"


def _institutional_scan_block(
    signal: dict[str, Any],
    *,
    max_chars: int = 950,
    include_macro: bool = True,
) -> str:
    """Render the measured 15-point scan without implying certainty."""
    analysis = signal.get("institutional_analysis")
    if not isinstance(analysis, dict):
        summary = str(signal.get("analysis_summary", "")).strip()
        return _clip(summary or "Institutional market-data scan unavailable.", max_chars)

    points = analysis.get("analysis_points", [])
    if not isinstance(points, list):
        points = []
    points = [point for point in points if isinstance(point, dict)][:15]
    header = "🏛️ 15-POINT INSTITUTIONAL SCAN"
    if not points:
        return _clip(
            str(signal.get("analysis_summary", "Institutional scan data unavailable.")),
            max_chars,
        )

    context_line = ""
    if include_macro:
        context = analysis.get("market_context", {})
        context = context if isinstance(context, dict) else {}
        dominance = context.get("btc_dominance", analysis.get("btc_dominance", {}))
        dominance = dominance if isinstance(dominance, dict) else {}
        fear_greed = context.get("fear_greed", {})
        fear_greed = fear_greed if isinstance(fear_greed, dict) else {}
        correlation = context.get("altcoin_btc_correlation", {})
        correlation = correlation if isinstance(correlation, dict) else {}
        dom_value = dominance.get("btc_dominance_percent")
        dom_text = f"{float(dom_value):.1f}%" if isinstance(dom_value, (int, float)) else "n/a"
        fg_value = fear_greed.get("value", "n/a")
        fg_label = str(fear_greed.get("classification", "unknown"))
        context_line = _clip(
            f"Macro proxy: BTC {context.get('bitcoin_trend', 'n/a')}; dominance {dom_text}; "
            f"F&G {fg_value} {fg_label}; {correlation.get('breadth', 'alt breadth n/a')}.",
            130,
        )

    fixed_chars = len(header) + 1 + (len(context_line) + 1 if context_line else 0)
    line_budget = max(24, (max_chars - fixed_chars - max(0, len(points) - 1)) // max(len(points), 1))
    lines: list[str] = []
    for index, point in enumerate(points, start=1):
        number = point.get("number", index)
        try:
            number = int(number)
        except (TypeError, ValueError, OverflowError):
            number = index
        name = _clip(point.get("name", "Evidence"), 15)
        observation = _clip(point.get("observation", "unavailable"), max(8, line_budget - len(f"{number}. {name}: ")))
        lines.append(f"{number}. {name}: {observation}")

    block = "\n".join([header, *lines, *([context_line] if context_line else [])])
    if len(block) > max_chars:
        # Preserve every numbered finding; trim only their observations to fit.
        compact: list[str] = []
        target = max(8, (max_chars - len(header) - 2 - (len(context_line) + 1 if context_line else 0) - len(lines)) // max(len(lines), 1))
        for line in lines:
            prefix, _, observation = line.partition(": ")
            compact.append(f"{prefix}: {_clip(observation, target)}")
        block = "\n".join([header, *compact, *([context_line] if context_line else [])])
    if len(block) > max_chars:
        return block[: max(0, max_chars - 1)].rstrip() + "…"
    return block


# ---------------------------------------------------------------------------
# MODE 1: Rapid Alert Post (institutional scan summary)
# ---------------------------------------------------------------------------

def format_rapid_alert_post(signal: dict[str, Any]) -> str:
    """Render a 120-300 character alert with price, risk, and live research cues."""

    coin = _clip(signal["coin"], 12).upper().strip()
    entry = float(signal["entry_price"])
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])
    stop = float(signal["sl"])
    direction = _direction(signal)
    hashtag = _get_dynamic_hashtag()
    cta = _get_cta_prompt(coin).strip()

    entry_s = _format_price(entry)
    tp1_s = _format_price(tp1)
    tp2_s = _format_price(tp2)
    sl_s = _format_price(stop)
    tp1_pct = _move_percent(entry, tp1, direction)
    tp2_pct = _move_percent(entry, tp2, direction)
    stop_pct = abs(_move_percent(entry, stop, direction))
    rr = _risk_reward(entry, tp1, stop)

    research = _as_mapping(signal.get("institutional_analysis"))
    indicators = _as_mapping(research.get("indicators_1h"))
    volume = _as_mapping(research.get("volume_analysis")) or _as_mapping(signal.get("volume_analysis"))
    futures = _as_mapping(research.get("futures")) or _as_mapping(signal.get("futures_data"))
    heatmap = _as_mapping(_nested_value(research, "futures", "liquidation_levels")) or _as_mapping(
        signal.get("liquidation_heatmap")
    )
    mtf = _as_mapping(research.get("timeframe_trends")) or _as_mapping(
        signal.get("mtf_analysis")
    )

    rsi = _first_value(indicators.get("rsi"), _nested_value(research, "indicators_1h", "rsi"))
    rsi_text = _compact_number(rsi, 1)
    spike = _boolish(volume.get("volume_spike"))
    latest_volume = _as_number(volume.get("latest_volume"))
    average_volume = _as_number(volume.get("average_volume"))
    if spike is True and average_volume and average_volume > 0 and latest_volume is not None:
        volume_text = f"spike {latest_volume / average_volume:.1f}x"
    elif spike is True:
        volume_text = "spike"
    elif spike is False and latest_volume is not None:
        volume_text = "normal"
    else:
        volume_text = "n/a"
    if _boolish(volume.get("estimated")) is True:
        volume_text = f"est {volume_text}"

    oi = _first_value(
        _nested_value(futures, "open_interest", "open_interest_usdt"),
        _nested_value(futures, "open_interest", "notional_usdt"),
        _nested_value(futures, "open_interest", "value"),
        futures.get("open_interest_usdt"),
        futures.get("open_interest"),
        _nested_value(research, "futures", "open_interest", "open_interest_usdt"),
    )
    oi_estimated = _boolish(
        _first_value(
            _nested_value(futures, "open_interest", "estimated"),
            futures.get("estimated"),
        )
    )
    if isinstance(oi, dict):
        oi = _first_value(oi.get("open_interest_usdt"), oi.get("notional_usdt"), oi.get("value"))
    oi_text = _compact_money(oi)
    if oi_estimated is True:
        oi_text = f"est {oi_text}"

    def zone_text(value: Any) -> str:
        if isinstance(value, dict):
            value = _first_value(value.get("mid"), value.get("lower"), value.get("upper"))
        number = _as_number(value)
        return _format_price(number) if number and number > 0 else "n/a"

    long_zone = zone_text(heatmap.get("long_liquidation_zone"))
    short_zone = zone_text(heatmap.get("short_liquidation_zone"))
    if long_zone == "n/a" and short_zone == "n/a":
        magnet = zone_text(heatmap.get("magnet_price"))
        zones_text = f"magnet {magnet}" if magnet != "n/a" else "zones n/a"
    else:
        zones_text = f"L {long_zone}/S {short_zone}"

    trend_1h = _trend_short(mtf.get("1h", mtf.get("1H", "unavailable")))
    trend_4h = _trend_short(mtf.get("4h", mtf.get("4H", "unavailable")))
    bonus = _clip(signal.get("trending_bonus", "BTC"), 12).upper() or "BTC"

    headline = (
        f"⚡ ${coin} {direction} | Entry CMP ${entry_s} | "
        f"TP1 ${tp1_s} (+{tp1_pct:.1f}%) / TP2 ${tp2_s} (+{tp2_pct:.1f}%) | "
        f"SL ${sl_s} (-{stop_pct:.1f}%) | RR 1:{rr:.1f}"
    )
    detail = (
        f"📊 RSI{rsi_text} | VOL {volume_text} | OI {oi_text} | "
        f"MTF {trend_1h}/{trend_4h} | Est Liq {zones_text}"
    )
    tag_line = f"💎 Bonus ${bonus} {hashtag}"
    cta_line = f"CTA: {_clip(cta, 36)}" if cta else "CTA: Verify levels before entry."
    disclaimer = "⚠️ Not financial advice; manage risk."

    max_body = 299  # _finalize_post appends one trailing newline.
    fixed = len(headline) + len(tag_line) + len(cta_line) + len(disclaimer) + 3
    detail = _clip(detail, max(20, max_body - fixed))
    body = f"{headline}\n{detail}\n{tag_line}\n{cta_line}\n{disclaimer}"
    if len(body) < 120:
        reason = _clip(signal.get("rapid_reason", "Use the stated invalidation and recheck live conditions."), 90)
        body = f"{headline}\n{detail}\n{reason}\n{tag_line}\n{cta_line}\n{disclaimer}"
    if len(body) > max_body:
        # Keep the entry/target/stop line at the very top; trim optional detail first.
        detail_budget = max(0, max_body - len(headline) - len(tag_line) - len(cta_line) - len(disclaimer) - 4)
        detail = _clip(detail, detail_budget)
        body = f"{headline}\n{detail}\n{tag_line}\n{cta_line}\n{disclaimer}"
        if len(body) > max_body:
            body = body[: max_body - 1].rstrip()
    return _finalize_post(body)


# ---------------------------------------------------------------------------
# MODE 2: Explained Master Setup Post (up to the Square character limit)
# ---------------------------------------------------------------------------

def format_explained_post(signal: dict[str, Any]) -> str:
    """Format a concise but complete 15-point setup explanation (900-1900 chars)."""

    coin = _clip(signal["coin"], 12).upper().strip()
    entry = float(signal["entry_price"])
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])
    stop = float(signal["sl"])
    change = float(signal.get("change", 0.0))
    direction = _direction(signal)
    confidence_value = _as_number(signal.get("confidence", 85))
    confidence = max(0, min(100, int(round(confidence_value or 85))))

    tp1_pct = _move_percent(entry, tp1, direction)
    tp2_pct = _move_percent(entry, tp2, direction)
    stop_pct = abs(_move_percent(entry, stop, direction))
    rr = _risk_reward(entry, tp1, stop)

    analysis = _as_mapping(signal.get("institutional_analysis"))
    volume = _first_value(analysis.get("volume_analysis"), signal.get("volume_analysis"))
    futures = _first_value(analysis.get("futures"), signal.get("futures_data"))
    liquidity = _first_value(analysis.get("orderbook"), signal.get("liquidity_analysis"))
    heatmap = _first_value(
        _nested_value(analysis, "futures", "liquidation_levels"), signal.get("liquidation_heatmap")
    )
    mtf = _first_value(analysis.get("timeframe_trends"), signal.get("mtf_analysis"))
    market_context = _first_value(analysis.get("market_context"), signal.get("market_context"))
    sentiment = _first_value(
        {
            "fear_greed": _nested_value(market_context, "fear_greed"),
            "social_sentiment": _nested_value(market_context, "social_sentiment"),
            "on_chain_proxy": _nested_value(market_context, "on_chain_proxy"),
        },
        signal.get("sentiment_onchain"),
    )
    macro = _first_value(_nested_value(market_context, "macro_factors"), signal.get("macro_factors"))
    volatility = signal.get("volatility_assessment")
    validation_value = _as_number(signal.get("validation_score", confidence))
    validation = max(0, min(100, int(round(validation_value if validation_value is not None else confidence))))

    why_now = _clip(
        signal.get(
            "why_now",
            f"{coin} has a {change:+.1f}% 24-hour move; wait for the measured trigger and volume confirmation.",
        ),
        110,
    )
    invalidation = _clip(
        signal.get(
            "invalidation",
            f"A decisive move beyond the stated ${_format_price(stop)} stop invalidates this thesis.",
        ),
        90,
    )
    technical = _clip(
        _describe_data(
            _first_value(signal.get("technical_analysis"), signal.get("smc_logic")), 110
        ),
        110,
    )
    indicators = _clip(
        _describe_data(
            _first_value(signal.get("indicators_summary"), _nested_value(analysis, "indicators_1h")),
            95,
        ),
        95,
    )
    volume_futures = _clip(
        f"Volume: {_describe_data(volume, 68)} | Futures: {_describe_data(futures, 68)}",
        145,
    )
    liquidity_heatmap = _clip(
        f"Order book: {_describe_data(liquidity, 58)} | Estimated liquidation zones: {_describe_data(heatmap, 58)}",
        125,
    )
    mtf_summary = _clip(_describe_data(mtf, 85), 85)
    market_sentiment = _clip(
        f"Market: {_describe_data(market_context, 62)} | Sentiment/on-chain: {_describe_data(sentiment, 62)}",
        130,
    )
    fundamental = _first_value(
        signal.get("fundamental_note"),
        signal.get("fundamental_risk"),
        _nested_value(market_context, "fundamental_notes"),
        "Token/event risk unavailable; do not assume a catalyst.",
    )
    macro_fundamental = _clip(
        f"Macro (Fed/CPI/DXY): {_describe_data(macro, 70)} | Fundamental: {_describe_data(fundamental, 55)}",
        135,
    )
    volatility_validation = _clip(
        f"ATR/volatility: {_describe_data(volatility, 73)} | Validation score: {validation}/100",
        95,
    )
    risk_management = _clip(
        _describe_data(
            _first_value(
                signal.get("risk_management"),
                {"risk": signal.get("risk", "Medium Risk"), "confidence": confidence},
            ),
            100,
        ),
        105,
    )

    hooks = (
        f"📊 ${coin} 15-Point Institutional Scan ({change:+.1f}% 24H) — {direction} Thesis",
        f"⚖️ ${coin} Evidence-Weighted {direction} Setup — Defined Risk, Clear Invalidation",
        f"🔍 ${coin} Multi-Timeframe Read ({change:+.1f}% 24H) — Levels & Liquidity in Focus",
        f"👀 ${coin} Market Structure Check ({change:+.1f}% 24H) — Setup Overview",
    )
    selected_hook = _RNG.choice(hooks)
    cta = _get_cta_prompt(coin).strip()
    hashtag = _get_dynamic_hashtag()
    cta_text = _clip(cta, 54) if cta else "Verify the setup against your own chart."

    header = (
        f"👀 ${coin} {direction} | ENTRY CMP ${_format_price(entry)} | "
        f"TP1 ${_format_price(tp1)} ({tp1_pct:+.1f}%) | "
        f"TP2 ${_format_price(tp2)} ({tp2_pct:+.1f}%) | "
        f"SL ${_format_price(stop)} (-{stop_pct:.1f}%) | RR 1:{rr:.2f} | Conf {confidence}%"
    )
    sections = (
        f"TECHNICAL ANALYSIS: {technical}",
        f"INDICATORS (EMA/RSI/MACD/VWAP/BB/Stoch RSI): {indicators}",
        f"VOLUME & FUTURES: {volume_futures}",
        f"LIQUIDITY & LIQUIDATION HEATMAP: {liquidity_heatmap}",
        f"MULTI-TIMEFRAME (5m-1W): {mtf_summary}",
        f"MARKET / SENTIMENT / ON-CHAIN: {market_sentiment}",
        f"MACRO & FUNDAMENTAL: {macro_fundamental}",
        f"VOLATILITY & VALIDATION: {volatility_validation}",
        f"RISK MANAGEMENT: {risk_management}",
    )
    footer = (
        f"CTA: {cta_text}\n"
        f"Tags: ${coin} $BTC {hashtag}\n"
        "⚠️ Educational market analysis, not financial advice. Recheck live data and manage risk."
    )
    body = "\n".join(
        [header, selected_hook, f"Why now: {why_now}", f"Invalidation: {invalidation}", *sections, footer]
    )

    # Short inputs still get useful execution/data-quality context, not generic filler.
    if len(body) < 900:
        body += (
            "\nData discipline: Entry CMP comes from the verified live-price path; candles and volume are observations, "
            "while futures, order-book and liquidation levels are snapshots or estimates where marked. Unavailable "
            "sentiment, on-chain, Fed, CPI or DXY feeds are unknown rather than directional. Reconfirm the trigger "
            "before acting; use the stated invalidation and size positions to a predefined account-risk limit."
        )
    if len(body) < 900:
        body += " Higher-timeframe conflict or a changing volatility regime is a reason to reassess, not to widen the stop."

    # Keep the target under 1,900 chars (well below the 2,100 hard cap) and preserve Entry at the top.
    if len(body) + 1 > 1900:
        body = body[:1899].rstrip()
    return _finalize_post(body)


# ---------------------------------------------------------------------------
# MODE 3: Macro & Bitcoin Market Overview Post (500 - 800 characters)
# ---------------------------------------------------------------------------

def format_market_overview_post(overview: dict[str, Any]) -> str:
    """Format a deep Bitcoin & Macro Market Overview post."""

    headline = str(overview.get("headline", "Bitcoin Consolidates Near Key Levels as Altcoin Momentum Expands")).strip()
    btc_price = float(overview.get("btc_price", 0.0))
    btc_change = float(overview.get("btc_change", 0.0))
    market_phase = str(overview.get("market_phase", "Consolidation Range")).strip()
    btc_thesis = str(overview.get("btc_thesis", "BTC is maintaining structure above local support as volume builds.")).strip()
    altcoin_summary = str(overview.get("altcoin_summary", "Altcoin momentum is rotating into high-volume setups.")).strip()
    btc_support = float(overview.get("btc_support", btc_price * 0.98))
    btc_resistance = float(overview.get("btc_resistance", btc_price * 1.02))
    strategy_outlook = str(overview.get("strategy_outlook", "Trade with clear invalidation levels and protect capital.")).strip()

    btc_price_s = _format_price(btc_price)
    btc_sup_s = _format_price(btc_support)
    btc_res_s = _format_price(btc_resistance)

    body = f"""🌐 MACRO & BITCOIN OUTLOOK: {headline} 📊

$BTC 24H: ${btc_price_s} ({btc_change:+.2f}%)
Market Phase: {market_phase}

📊 Key Market Dynamics:
• Bitcoin Structure: {btc_thesis}
• Altcoin Flows: {altcoin_summary}
• Crucial Levels: Support ${btc_sup_s} | Resistance ${btc_res_s}

💡 Trader Takeaway: {strategy_outlook}

$BTC $ETH #BinanceSquare #CryptoMarket
⚠️ Educational market analysis only. Not financial advice. Always DYOR."""

    return _finalize_post(body)


# ---------------------------------------------------------------------------
# MODE 4: Viral Trader Meme / Community Culture Post (200 - 450 characters)
# ---------------------------------------------------------------------------

def format_meme_community_post(meme_data: dict[str, Any]) -> str:
    """
    Format a viral crypto trader meme / community psychology post.
    Research benchmark: Meme/community posts had 902.5 median views (+49.0% paired uplift).
    """

    title = str(meme_data.get("title", "Crypto Trader Reality 101:")).strip()
    content = str(meme_data.get("content", "Looking at the 1M chart: 'This is definitely a generational breakout.'\nLooking at the 1D chart: 'Oh.'")).strip()
    lesson = str(meme_data.get("lesson", "Always check higher timeframe structure before taking 20x leverage.")).strip()
    question = str(meme_data.get("question", "Who else can relate to this? Be honest 👇")).strip()
    hashtag = _get_dynamic_hashtag()

    body = f"""🎭 {title}

{content}

💡 Reality Check: {lesson}

{question}

$BTC $ETH {hashtag} #CryptoCommunity #TradingHumor"""

    return _finalize_post(body)


# Backward-compatible wrapper
def format_signal_post(signal: dict[str, Any], mode: str = "explained") -> str:
    """Generate signal post in the requested mode (rapid or explained)."""
    if mode == "rapid":
        return format_rapid_alert_post(signal)
    return format_explained_post(signal)


# ---------------------------------------------------------------------------
# Target Hit / Success Posts (Separate Event-Driven Proofs)
# ---------------------------------------------------------------------------

def format_success_post(
    signal: dict[str, Any],
    hit: str,
    current_price: float,
) -> str:
    """Format a verified, celebratory target-hit post (0% forced A/B bait)."""

    coin = str(signal["coin"]).upper().strip()
    entry = float(signal["entry_price"])
    target = float(current_price)
    tp2 = float(signal.get("tp2", target))
    direction = _direction(signal)
    hit = str(hit).upper().strip()
    move = _move_percent(entry, target, direction)
    hashtag = _get_dynamic_hashtag()

    entry_s = _format_price(entry)
    target_s = _format_price(target)
    tp2_s = _format_price(tp2)

    if hit == "TP1":
        next_step = f"Next target: TP2 at ${tp2_s} 🎯\n💡 Pro Tip: Move Stop Loss to Entry (${entry_s}) now for a risk-free ride."
    else:
        next_step = "All planned targets have been reached! 🚀\n💡 Pro Tip: Secure profits and await fresh accumulation."

    templates = (
        f"🎯 ${coin} {hit} HIT — +{abs(move):.2f}% MOVE DELIVERED! 🚀\n\n• Entry: ${entry_s}\n• Target Hit: ${target_s}\n• Bias: {direction}\n\n{next_step}\n\n${coin} $BTC #Write2Earn #TargetHit",
        f"✅ TARGET SMASHED: ${coin} {hit} (+{abs(move):.2f}%) 📈\n\nPlanned {direction} setup touched ${target_s} from ${entry_s} entry.\n\n{next_step}\n\n${coin} $BTC #Write2Earn #TargetHit",
        f"🔥 ${coin} {hit} ACHIEVED WITH PRECISION (+{abs(move):.2f}%) 💰\n\nFrom ${entry_s} straight to ${target_s}.\n\n{next_step}\n\n${coin} $BTC #Write2Earn #TargetHit",
    )

    selected = _RNG.choice(templates)
    return _finalize_post(selected)


def get_current_price(coin: str) -> float:
    """Return a verified live price from the shared market-data layer."""

    symbol = f"{str(coin).upper()}USDT"
    data = get_24h_data(symbol)

    if not data.get("verified"):
        raise RuntimeError(f"Market data not verified for {symbol}")

    price = float(data["price"])
    if price <= 0:
        raise RuntimeError(f"Invalid verified price for {symbol}: {price}")

    return price
