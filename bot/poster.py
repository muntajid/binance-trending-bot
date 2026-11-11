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
import random
import re
from pathlib import Path
from typing import Any

from bot.market_data import get_24h_data

_RNG = random.SystemRandom()
ROOT_DIR = Path(__file__).resolve().parents[1]
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

# Phrases that must never leave the drafting pipeline. Accuracy is the brand;
# outcomed-promising language is both a compliance and a trust hazard.
BANNED_CLAIMS = (
    "guaranteed",
    "can't lose",
    "cannot lose",
    "100% win",
    "sure shot",
    "sure thing",
    "risk-free profit",
    "free money",
    "all in",
)


def _get_dynamic_hashtag() -> str:
    """Return exactly 1-2 relevant dynamic hashtags per benchmark."""
    return _RNG.choice(DYNAMIC_HASHTAGS)


def _content_config() -> dict[str, Any]:
    """Content knobs from bot/config.json (cta mix, hook length)."""
    try:
        from bot import trade_state  # late import: keep module load cheap

        content = trade_state.load_config().get("content", {})
        return content if isinstance(content, dict) else {}
    except Exception:
        return {}


def _cta_mix() -> dict[str, float]:
    raw = _content_config().get("cta_mix", {})
    mix = {"none": 0.5, "question": 0.35, "follow": 0.1, "comment": 0.05}
    if isinstance(raw, dict):
        for key in mix:
            try:
                value = float(raw.get(key, mix[key]))
                mix[key] = max(0.0, min(1.0, value))
            except (TypeError, ValueError):
                continue
    return mix


def _question_from_context(coin: str, context: dict[str, Any] | None) -> str:
    """Build a discussion question out of THIS post's own scenario levels.

    Creator-research finding: the reply leaders pair the question with the
    post's actual tension (131 replies on a content-specific prompt), not a
    generic "where do you see it?" line.
    """

    generic = (
        f"Where do you see ${coin} by end of week?",
        f"Are you trading this ${coin} setup or watching the reaction?",
        f"What is your target for ${coin} on this structure?",
    )
    context = context if isinstance(context, dict) else {}

    def _price(value: Any) -> str:
        number = _as_number(value)
        return _format_price(number) if number and number > 0 else ""

    direction = str(context.get("direction", "LONG")).upper()
    upside = _price(_first_value(context.get("resistance"), context.get("tp1")))
    downside = _price(_first_value(context.get("support"), context.get("sl")))

    if upside and downside:
        return f"Which hits first for ${coin}: ${upside} or ${downside}?"
    if upside:
        return f"What does ${coin} need to show before ${upside} comes into play?"
    return _RNG.choice(generic)


def _get_cta_prompt(coin: str, context: dict[str, Any] | None = None) -> str:
    """
    CTA distribution (mix configurable via bot/config.json -> content.cta_mix):
    - ~50% No CTA (clean analyst tone)
    - ~35% Scenario-specific discussion question (built from the post's levels)
    - ~10% Value-based follow prompt
    - ~5% Explicit comment prompt
    - 0% Forced A/B
    """
    roll = _RNG.random()
    mix = _cta_mix()

    q_end = mix["none"] + mix["question"]
    f_end = q_end + mix["follow"]

    if roll < mix["none"]:
        return ""

    if roll < q_end:
        return f"\n{_question_from_context(coin, context)}"

    if roll < f_end:
        return "\nFollow for real-time TP updates & verified setups."

    return f"\nDrop your price prediction for ${coin} below."


def _clip_sentence(text: Any, limit: int) -> str:
    """Clip at a sentence/semicolon boundary so cuts never look like glitches."""

    value = " ".join(str(text or "").split())
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    cut = value[:limit].rstrip()
    boundary = max(cut.rfind(". "), cut.rfind("; "), cut.rfind("? "), cut.rfind("! "))
    if boundary >= limit * 0.45:
        return cut[: boundary + 1].rstrip()
    comma = cut.rfind(", ")
    if comma >= limit * 0.6:
        return cut[:comma].rstrip() + "."
    return cut.rstrip(",;:") + "…"


def assert_post_quality(post: str, payload: dict[str, Any] | None = None) -> str:
    """Final editorial quality gate before a post may reach Binance Square.

    Checks (in addition to ``assert_clean_post_text`` which owns length and
    encoding):

    * banned outcome-promising language;
    * at most two question marks overall (one hook + one CTA question max);
    * every number in the model-written ``hook_line`` must match a number that
      exists in the verified payload (0.5% tolerance, matching the bot's
      price-drift guard), so a hook can never quote an invented level.
    """

    text = str(post)
    lowered = text.lower()
    for phrase in BANNED_CLAIMS:
        if phrase in lowered:
            raise ValueError(f"Banned claim language detected in post: {phrase!r}")

    if text.count("?") > 2:
        raise ValueError("Post carries more than two questions; one hook + one CTA question max")

    if isinstance(payload, dict):
        hook = str(payload.get("hook_line", "")).strip()
        # Ignore digits inside alphanumeric tokens (TP1, EMA20, 1H); only
        # standalone or $-prefixed figures count as quoted numbers.
        numbers = re.findall(r"(?<![A-Za-z])\d[\d,]*\.?\d*(?![A-Za-zx])", hook)
        if numbers:
            available: list[float] = []

            def _collect(value: Any) -> None:
                if isinstance(value, bool):
                    return
                if isinstance(value, (int, float)):
                    if math.isfinite(float(value)):
                        available.append(float(value))
                elif isinstance(value, dict):
                    for item in value.values():
                        _collect(item)
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        _collect(item)

            _collect(payload)
            for raw in numbers:
                try:
                    target = float(raw.replace(",", ""))
                except ValueError:
                    continue
                if target <= 0:
                    continue
                if not any(abs(known - target) <= abs(target) * 0.005 + 1e-12 for known in available):
                    raise ValueError(
                        f"Hook quotes {raw!r} which is absent from the verified payload"
                    )

    return text


def _trend_arrow(value: Any) -> str:
    mapping = {"up": "↑", "down": "↓", "flat": "→"}
    return mapping.get(_trend_short(value), "·")


def _fmt_volume_line(volume: Any) -> str:
    """Human one-liner for spot participation: spike status and buy/sell split."""

    v = _as_mapping(volume)
    spike = _boolish(v.get("volume_spike"))
    latest = _as_number(v.get("latest_volume"))
    average = _as_number(v.get("average_volume"))
    buy = _as_number(v.get("buying_volume"))
    sell = _as_number(v.get("selling_volume"))
    qv = _as_number(v.get("quote_volume_24h"))

    parts: list[str] = []
    if spike is True and average and average > 0 and latest is not None:
        parts.append(f"volume spike {latest / average:.1f}x vs average")
    elif spike is True:
        parts.append("volume spike detected")
    elif spike is False:
        parts.append("volume normal")
    if buy is not None and sell is not None and (buy + sell) > 0:
        share = buy / (buy + sell) * 100.0
        parts.append(f"taker buy share {share:.0f}%")
    if qv is not None and qv > 0:
        parts.append(f"24h turnover {_compact_money(qv)}")
    if _boolish(v.get("estimated")) is True:
        parts.append("(est.)")
    return "; ".join(parts) if parts else "unavailable"


def _fmt_futures_line(futures: Any) -> str:
    """Human one-liner for derivatives positioning; honest when feeds are down."""

    f = _as_mapping(futures)
    oi = _first_value(
        _nested_value(f, "open_interest", "open_interest_usdt"),
        _nested_value(f, "open_interest", "notional_usdt"),
        f.get("open_interest_usdt"),
    )
    funding = _first_value(
        _nested_value(f, "funding_rate", "funding_rate_pct"),
        _nested_value(f, "funding_rate", "rate"),
    )
    lsr = _nested_value(f, "long_short_ratio", "long_short_ratio")
    liq = _nested_value(f, "liquidation_estimate")
    liq_est = _boolish(_nested_value(liq, "estimated")) if isinstance(liq, dict) else None

    parts: list[str] = []
    if _as_number(oi) is not None:
        parts.append(f"OI {_compact_money(oi)}")
    if _as_number(funding) is not None:
        parts.append(f"funding {_compact_number(float(funding), 4)}%")
    if _as_number(lsr) is not None:
        parts.append(f"L/S ratio {_compact_number(lsr, 2)}")
    if not parts:
        return "derivatives feed unavailable today"
    suffix = " (est.)" if liq_est is True else ""
    return "; ".join(parts) + suffix


def _fmt_liquidity_line(liquidity: Any, heatmap: Any) -> str:
    """Order-book balance plus estimated liquidation zones, in prose."""

    book = _as_mapping(liquidity)
    heat = _as_mapping(heatmap)
    imbalance = str(book.get("imbalance", "")).replace("_", " ").strip()
    ratio = _as_number(book.get("imbalance_ratio"))

    parts: list[str] = []
    if imbalance:
        rendered = imbalance
        if ratio is not None:
            rendered += f" ({ratio:.2f}x)"
        parts.append(f"order book {rendered}")

    def _zone_mid(zone: Any) -> float | None:
        zone_map = _as_mapping(zone)
        return _as_number(_first_value(zone_map.get("mid"), zone_map.get("lower")))

    long_zone = _zone_mid(heat.get("long_liquidation_zone"))
    short_zone = _zone_mid(heat.get("short_liquidation_zone"))
    if long_zone or short_zone:
        zones = []
        if short_zone:
            zones.append(f"shorts ~${_format_price(short_zone)}")
        if long_zone:
            zones.append(f"longs ~${_format_price(long_zone)}")
        parts.append("est. liquidation magnets: " + " / ".join(zones))
    return "; ".join(parts) if parts else "unavailable"


def _fmt_trend_stack(mtf: Any) -> str:
    """Compact arrow stack across timeframes: 5m↓ 15m↑ 1H↑ 4H→ 1D↑ 1W·."""

    m = _as_mapping(mtf)
    labels = (("5m", "5m"), ("15m", "15m"), ("1h", "1H"), ("4h", "4H"), ("1d", "1D"), ("1w", "1W"))
    stack = " ".join(f"{label}{_trend_arrow(m.get(key, m.get(key.upper(), 'unavailable')))}" for key, label in labels)
    return stack


def _fmt_context_line(market_context: Any, sentiment: Any) -> str:
    """BTC trend, dominance and Fear & Greed in one measured sentence."""

    ctx = _as_mapping(market_context)
    sent = _as_mapping(sentiment)
    btc_trend = str(ctx.get("bitcoin_trend", "")).strip()
    dom = _as_number(_nested_value(ctx, "btc_dominance", "btc_dominance_percent"))
    fg = _as_mapping(_first_value(_nested_value(ctx, "fear_greed"), sent.get("fear_greed")))
    fg_value = _as_number(fg.get("value"))
    fg_label = str(fg.get("classification", "")).strip()

    parts: list[str] = []
    if btc_trend:
        parts.append(f"BTC {btc_trend}")
    if dom is not None:
        parts.append(f"dominance {dom:.1f}%")
    if fg_value is not None:
        parts.append(f"F&G {int(fg_value)}" + (f" {fg_label}" if fg_label else ""))
    return "; ".join(parts) if parts else "broader market context unavailable"


def _fmt_risk_line(risk: Any, fallback: dict[str, Any]) -> str:
    """Readable risk line: RR, sizing rule, invalidation plan."""

    r = _as_mapping(risk) or fallback
    rr = _first_value(r.get("risk_reward"), r.get("risk_reward_tp1"))
    sizing = _first_value(r.get("position_sizing"), r.get("guidance"))
    plan = _first_value(r.get("invalidation_plan"), r.get("invalidation"))

    parts: list[str] = []
    if rr is not None:
        rr_text = str(rr).strip()
        if _as_number(rr) is not None:
            rr_text = f"RR ≈1:{float(rr):.1f} to TP1"
        parts.append(rr_text)
    if sizing:
        parts.append(str(sizing).strip().rstrip("."))
    if plan:
        parts.append(f"plan: {str(plan).strip()}")
    return "; ".join(parts) if parts else "size positions to a predefined account-risk limit"


def _fmt_macro_line(macro: Any, fundamental: Any) -> str:
    """One honest macro/fundamental line; 'unavailable' when feeds are missing."""

    m = _as_mapping(macro)
    contexts = [str(m.get(key, "")).lower() for key in ("fed_rate_context", "cpi_context", "dxy_proxy")]
    feeds_missing = contexts and all(("not fetched" in item or "no live" in item or "unavailable" in item) for item in contexts if item)
    macro_text = "macro feeds unavailable - no event catalyst claimed" if feeds_missing else _describe_data(m, 90)
    fund_text = _clip_sentence(_describe_data(fundamental, 80), 80)
    return f"{macro_text}; token/event risk: {fund_text}"


def _hook_lead(payload: dict[str, Any]) -> str:
    """Return the model-written hook when present and usable, else ''."""

    hook = " ".join(str(payload.get("hook_line", "") or "").split())
    if not hook:
        return ""
    max_chars = 90
    try:
        max_chars = int(_content_config().get("hook_max_chars", 90))
    except (TypeError, ValueError):
        max_chars = 90
    if len(hook) > max_chars:
        hook = _clip_sentence(hook, max_chars)
    lowered = hook.lower()
    if any(phrase in lowered for phrase in BANNED_CLAIMS):
        return ""
    return hook


def _cta_context(signal: dict[str, Any]) -> dict[str, Any]:
    """Levels the contextual CTA question can reference."""
    analysis = _as_mapping(signal.get("institutional_analysis"))
    levels = _as_mapping(_nested_value(analysis, "indicators_1h", "support_resistance"))
    return {
        "direction": str(signal.get("direction", "LONG")).upper(),
        "tp1": signal.get("tp1"),
        "sl": signal.get("sl"),
        "support": _first_value(levels.get("support"), signal.get("sl")),
        "resistance": _first_value(levels.get("resistance"), signal.get("tp1")),
    }


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
    """Render a hook-led rapid alert (≈300-720 chars) with price, risk, and live research cues."""

    coin = _clip(signal["coin"], 12).upper().strip()
    entry = float(signal["entry_price"])
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])
    stop = float(signal["sl"])
    direction = _direction(signal)
    hashtag = _get_dynamic_hashtag()
    cta = _get_cta_prompt(coin, _cta_context(signal)).strip()

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
        f"📊 RSI {rsi_text} | VOL {volume_text} | OI {oi_text} | "
        f"MTF 1H {trend_1h} / 4H {trend_4h} | Est liq zones {zones_text}"
    )
    trigger = _clip_sentence(
        signal.get("rapid_reason") or signal.get("why_now", ""),
        120,
    )
    tag_line = f"💎 Context: ${bonus} {hashtag}"
    disclaimer = "⚠️ Not financial advice; verify levels first."

    hook = _hook_lead(signal)

    # Rapid alert budget: enough for a narrative hook + levels + evidence, far
    # above the old 120-300 char data line that gave the feed no story.
    max_body = 720

    lines: list[str] = [hook, headline, detail] if hook else [headline, detail]
    lines.append(trigger)
    lines.append(tag_line)
    cta_line = _clip_sentence(cta, 140)
    if cta_line:
        lines.append(cta_line)
    lines.append(disclaimer)

    body = "\n".join(line for line in lines if line)
    if len(body) > max_body:
        # Trim working backwards: trigger first, then detail; never the hook
        # or the verified levels line.
        kept: list[str] = []
        for line in lines:
            if not line:
                continue
            if line == trigger and len(body) > max_body:
                body = body.replace(line + "\n", "", 1)
                continue
            kept.append(line)
        body = "\n".join(kept)
    if len(body) > max_body:
        overflow = len(body) - max_body
        detail_budget = max(20, len(detail) - overflow - 2)
        body = body.replace(detail, _clip(detail, detail_budget), 1)
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

    why_now = _clip_sentence(
        signal.get(
            "why_now",
            f"{coin} has a {change:+.1f}% 24-hour move; wait for the measured trigger and volume confirmation.",
        ),
        150,
    )
    invalidation = _clip_sentence(
        signal.get(
            "invalidation",
            f"A decisive move beyond the stated ${_format_price(stop)} stop invalidates this thesis.",
        ),
        130,
    )
    technical = _clip_sentence(
        _describe_data(
            _first_value(signal.get("technical_analysis"), signal.get("smc_logic")), 170
        ),
        170,
    )
    indicators = _clip_sentence(
        _describe_data(
            _first_value(signal.get("indicators_summary"), _nested_value(analysis, "indicators_1h")),
            150,
        ),
        150,
    )
    participation_line = _clip_sentence(
        f"{_fmt_volume_line(volume)} | Futures: {_fmt_futures_line(futures)}",
        190,
    )
    liquidity_heatmap = _clip_sentence(_fmt_liquidity_line(liquidity, heatmap), 170)
    mtf_summary = _fmt_trend_stack(mtf)
    market_sentiment = _clip_sentence(_fmt_context_line(market_context, sentiment), 170)
    fundamental = _first_value(
        signal.get("fundamental_note"),
        signal.get("fundamental_risk"),
        _nested_value(market_context, "fundamental_notes"),
        "Token/event risk unavailable; do not assume a catalyst.",
    )
    macro_fundamental = _clip_sentence(_fmt_macro_line(macro, fundamental), 190)
    risk_management = _clip_sentence(
        _fmt_risk_line(
            signal.get("risk_management"),
            {
                "risk_reward": f"RR ≈1:{rr:.1f} to TP1",
                "position_sizing": "size to a predefined account-risk limit",
                "invalidation_plan": invalidation,
            },
        ),
        200,
    )

    # Scenario pair: model-written when present, otherwise derived from the
    # verified levels so the binary tension is always on the page.
    scenarios = signal.get("scenarios") if isinstance(signal.get("scenarios"), dict) else {}
    bull_if = " ".join(str(scenarios.get("bull_if", "") or "").split())
    bear_if = " ".join(str(scenarios.get("bear_if", "") or "").split())
    if not bull_if or not bear_if:
        if direction == "LONG":
            bull_if = bull_if or f"price holds above ${_format_price(stop)}"
            bear_if = bear_if or f"a decisive close below ${_format_price(stop)} breaks structure"
        else:
            bull_if = bull_if or f"price stays capped below ${_format_price(stop)}"
            bear_if = bear_if or f"a decisive close above ${_format_price(stop)} cancels the down-leg"

    fallback_hooks = (
        f"🔍 ${coin}: what the verified data says right now ({change:+.1f}% 24H)",
        f"⚖️ ${coin} evidence check — defined risk, clear invalidation ({change:+.1f}% 24H)",
        f"👀 ${coin} multi-timeframe read — the level that decides it ({change:+.1f}% 24H)",
    )
    selected_hook = _hook_lead(signal) or _RNG.choice(fallback_hooks)
    cta = _get_cta_prompt(coin, _cta_context(signal)).strip()
    hashtag = _get_dynamic_hashtag()

    # Data-confidence note: when a feed is missing the post says so out loud.
    # Honest gaps cost nothing; pretending completeness costs trust.
    confidence_notes: list[str] = []
    futures_map = futures if isinstance(futures, dict) else {}
    if _first_value(
        _nested_value(futures_map, "open_interest", "open_interest_usdt"),
        _nested_value(futures_map, "funding_rate", "funding_rate_pct"),
    ) is None:
        confidence_notes.append("derivatives feed unavailable")
    fg = _nested_value(market_context, "fear_greed")
    if isinstance(fg, dict) and fg.get("estimated") is True:
        confidence_notes.append("sentiment index estimated")
    data_confidence = (
        f"📎 Data confidence: {'; '.join(confidence_notes)} — the rest is measured."
        if confidence_notes
        else ""
    )

    header = (
        f"👀 ${coin} {direction} | Entry CMP ${_format_price(entry)} | "
        f"TP1 ${_format_price(tp1)} ({tp1_pct:+.1f}%) | "
        f"TP2 ${_format_price(tp2)} ({tp2_pct:+.1f}%) | "
        f"SL ${_format_price(stop)} (-{stop_pct:.1f}%) | RR 1:{rr:.2f} | Conf {confidence}%"
    )

    evidence_bullets = [
        f"Structure: {technical}",
        f"Indicators: {indicators}",
        f"Participation: {participation_line}",
        f"Liquidity: {liquidity_heatmap}",
        f"Trend stack: {mtf_summary}",
        f"Context: {market_sentiment}",
    ]

    scenario_line = (
        f"🔀 If {bull_if.rstrip('.')} → path to ${_format_price(tp1)} stays open; "
        f"if {bear_if.rstrip('.')} → thesis cancelled."
    )

    body_lines = [
        selected_hook,
        "",
        header,
        f"Why now: {why_now}",
        "",
        "Key evidence (verified):",
        *(f"• {bullet}" for bullet in evidence_bullets),
        f"• Macro & fundamental: {macro_fundamental}",
        "",
        scenario_line,
        f"❌ Invalidation: {invalidation}",
        f"🛡️ Risk: {risk_management} (validation {validation}/100)",
    ]
    if data_confidence:
        body_lines.append(data_confidence)
    if cta:
        body_lines.append(_clip_sentence(cta, 140))
    body_lines.append(f"Tags: ${coin} $BTC {hashtag}")
    body_lines.append(
        "⚠️ Educational market analysis, not financial advice. Recheck live data and manage risk."
    )
    body = "\n".join(body_lines)

    # If the model returned unusually thin prose, keep the useful data-quality
    # context instead of shipping a naked skeleton.
    if len(body) < 700:
        body += (
            "\nData discipline: Entry CMP comes from the verified live-price path; candles and volume are observations, "
            "while futures, order-book and liquidation levels are snapshots or estimates where marked. Unavailable "
            "sentiment, on-chain, Fed, CPI or DXY feeds are unknown rather than directional. Reconfirm the trigger "
            "before acting; use the stated invalidation and size positions to a predefined account-risk limit."
        )

    # Keep the target under 1,900 chars (well below the 2,100 hard cap).
    if len(body) + 1 > 1900:
        # Trim the lowest-signal bullets first (macro/context), keeping the
        # hook, levels, scenario and invalidation fully intact.
        for label in ("• Macro & fundamental: ", "• Context: ", "• Trend stack: "):
            if len(body) + 1 <= 1900:
                break
            body = "\n".join(line for line in body.split("\n") if not line.startswith(label))
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

    fear_greed = _as_mapping(overview.get("fear_greed"))
    dominance = _as_mapping(overview.get("btc_dominance"))
    fg_value = _as_number(fear_greed.get("value"))
    fg_label = str(fear_greed.get("classification", "")).strip()
    dom_pct = _as_number(dominance.get("btc_dominance_percent"))
    fg_est = " (est.)" if fear_greed.get("estimated") is True else ""
    dom_est = " (est.)" if dominance.get("estimated") is True else ""
    sentiment_line = ""
    if fg_value is not None or dom_pct is not None:
        fg_text = f"Fear & Greed {int(fg_value)}{fg_est}" + (f" ({fg_label})" if fg_label else "") if fg_value is not None else ""
        dom_text = f"BTC dominance {dom_pct:.1f}%{dom_est}" if dom_pct is not None else ""
        sentiment_line = "\n📈 Sentiment: " + " | ".join(part for part in (fg_text, dom_text) if part)

    flip_bull = " ".join(str(overview.get("what_flips_bullish", "") or "").split())
    flip_bear = " ".join(str(overview.get("what_flips_bearish", "") or "").split())
    flip_line = ""
    if flip_bull or flip_bear:
        flip_line = (
            f"\n🔄 What flips it: bullish if {flip_bull.rstrip('.') or 'structure holds'}; "
            f"watch out if {flip_bear.rstrip('.') or 'support gives way'}."
        )

    hook = _hook_lead(overview)
    hook_block = f"{hook}\n\n" if hook else ""

    body = f"""🌐 MACRO & BITCOIN OUTLOOK: {headline} 📊

{hook_block}$BTC 24H: ${btc_price_s} ({btc_change:+.2f}%)
Market Phase: {market_phase}

📊 Key Market Dynamics:
• Bitcoin Structure: {btc_thesis}
• Altcoin Flows: {altcoin_summary}
• Crucial Levels: Support ${btc_sup_s} | Resistance ${btc_res_s}{sentiment_line}{flip_line}

💡 Trader Takeaway: {strategy_outlook}

$BTC $ETH #BinanceSquare #CryptoMarket
⚠️ Educational market analysis only. Not financial advice. Always DYOR."""

    if len(body) + 1 > 1900:
        body = body[:1899].rstrip()
    return _finalize_post(body)


def format_stop_post(trade: dict[str, Any], current_price: float) -> str:
    """Verified loss-resolution post: the accountability half of the journal.

    Creator research: loss-admission posts built trust AND ranked among the
    highest-reply content. This bot can do it with a candle-verified ledger
    behind every number, which is exactly the gap the researched creator has.
    Tone: calm, factual, zero drama - the stop working as designed.
    """

    coin = str(trade["coin"]).upper().strip()
    entry = float(trade["entry_price"])
    stop = float(trade["sl"])
    tp1 = float(trade.get("tp1", entry))
    direction = _direction(trade)
    price = float(current_price)
    adverse = abs(_move_percent(entry, price, direction))

    entry_s = _format_price(entry)
    price_s = _format_price(price)
    stop_s = _format_price(stop)

    created = str(trade.get("created_at", ""))[:10]
    called = f"called {created} " if re.fullmatch(r"\d{4}-\d{2}-\d{2}", created) else "called "

    tp1_note = ""
    if trade.get("tp1_hit"):
        tp1_pct = abs(_move_percent(entry, tp1, direction))
        tp1_note = (
            f"\nTP1 (${_format_price(tp1)}, +{tp1_pct:.1f}%) was reached first — "
            "partial profit discipline softened this one."
        )

    if direction == "LONG":
        watch = f"Next watch: whether ${stop_s} holds as a floor on retest; a fresh setup needs a new verified trigger."
    else:
        watch = f"Next watch: whether ${stop_s} caps the reclaim; a fresh setup needs a new verified trigger."

    body = f"""🛑 ${coin} {direction} STOPPED — invalidation respected at ${stop_s}

Setup {called}at ${entry_s}; price traded to ${price_s} ({adverse:.1f}% against entry).{tp1_note}
The risk was defined before entry and the exit did its job. No widening, no revenge entry.

{watch}

${coin} $BTC #BinanceSquare
⚠️ Verified from the 1M candle ledger. Educational market analysis, not financial advice."""

    if len(body) + 1 > 1200:
        body = body[:1199].rstrip()
    return _finalize_post(body)


def format_pressure_post(trade: dict[str, Any], current_price: float) -> str:
    """Mid-trade 'under pressure' journal update (max one per open trade).

    Serialized tension is the researched creator's strongest narrative loop
    (setup -> stress -> resolution). This is the verified version: no DCA
    claims, no emotions invented - just the level that decides.
    """

    coin = str(trade["coin"]).upper().strip()
    entry = float(trade["entry_price"])
    stop = float(trade["sl"])
    tp1 = float(trade["tp1"])
    direction = _direction(trade)
    price = float(current_price)

    entry_s = _format_price(entry)
    price_s = _format_price(price)
    stop_s = _format_price(stop)
    tp1_s = _format_price(tp1)
    drift = _move_percent(entry, price, direction)

    state_line = (
        f"Entry ${entry_s} → now ${price_s} ({drift:+.1f}% vs entry)."
    )
    hold_line = (
        f"While ${stop_s} holds, the thesis stays open. A decisive break closes it - no arguments, no moving the stop."
    )

    body = f"""⏳ ${coin} {direction} update — thesis under pressure

{state_line}
Price is testing the invalidation zone near ${stop_s}. {hold_line}

Checkpoint unchanged: TP1 at ${tp1_s}. Nothing to do but watch the level do the talking.

${coin} $BTC #BinanceSquare
⚠️ Verified levels, not a prediction. Educational analysis only."""

    if len(body) + 1 > 900:
        body = body[:899].rstrip()
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


# ---------------------------------------------------------------------------
# Verified Scorecard & Educational Explainer (manual dispatch types)
# ---------------------------------------------------------------------------


def scorecard_stats(
    closed_trades: list[dict[str, Any]],
    active_trades: list[dict[str, Any]],
    *,
    days: int = 7,
    now: Any = None,
) -> dict[str, Any]:
    """Count outcomes from the verified ledger for the public scorecard.

    Wins and losses are counted identically - that symmetry is the point.
    """

    import datetime as _dt

    now = now or _dt.datetime.now(_dt.timezone.utc)
    window_start = now - _dt.timedelta(days=max(1, int(days)))

    def _parse(value: Any) -> Any:
        if not value:
            return None
        try:
            parsed = _dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed.astimezone(_dt.timezone.utc)

    closed_in_window = 0
    tp2 = tp1_partial = stopped = expired = superseded = 0
    gains_to_tp2: list[float] = []
    losses_to_stop: list[float] = []

    for trade in closed_trades or []:
        if not isinstance(trade, dict):
            continue
        closed_at = _parse(trade.get("closed_at")) or _parse(trade.get("created_at"))
        if closed_at is None or closed_at < window_start:
            continue
        closed_in_window += 1
        status = str(trade.get("status", "")).upper()
        if trade.get("tp1_hit") and status != "TP2_HIT":
            tp1_partial += 1
        if status == "TP2_HIT":
            tp2 += 1
        elif status == "SL_HIT":
            stopped += 1
            try:
                entry = float(trade.get("entry_price", 0))
                close = float(trade.get("closed_price", 0))
                if entry > 0 and close > 0:
                    losses_to_stop.append(abs(_move_percent(entry, close, _direction(trade))))
            except (TypeError, ValueError):
                continue
        elif status == "EXPIRED":
            expired += 1
        elif status == "SUPERSEDED":
            superseded += 1

        if status == "TP2_HIT":
            try:
                entry = float(trade.get("entry_price", 0))
                close = float(trade.get("closed_price", trade.get("tp2", 0)))
                if entry > 0 and close > 0:
                    gains_to_tp2.append(abs(_move_percent(entry, close, _direction(trade))))
            except (TypeError, ValueError):
                continue

    open_now = sum(
        1
        for trade in active_trades or []
        if isinstance(trade, dict)
        and str(trade.get("status", "ACTIVE")).upper().strip() == "ACTIVE"
    )

    return {
        "days": max(1, int(days)),
        "closed": closed_in_window,
        "tp2": tp2,
        "tp1_partial": tp1_partial,
        "stopped": stopped,
        "expired": expired,
        "superseded": superseded,
        "open": open_now,
        "avg_tp2_pct": (sum(gains_to_tp2) / len(gains_to_tp2)) if gains_to_tp2 else None,
        "avg_stop_pct": (sum(losses_to_stop) / len(losses_to_stop)) if losses_to_stop else None,
    }


def format_scorecard_post(stats: dict[str, Any]) -> str:
    """Render the weekly verified scorecard (wins and losses alike)."""

    days = int(stats.get("days", 7))
    closed = int(stats.get("closed", 0))
    tp2 = int(stats.get("tp2", 0))
    tp1_partial = int(stats.get("tp1_partial", 0))
    stopped = int(stats.get("stopped", 0))
    expired = int(stats.get("expired", 0))
    open_now = int(stats.get("open", 0))

    tp2_share = f" ({tp2 / closed * 100:.0f}% of closed)" if closed else ""
    avg_tp2 = stats.get("avg_tp2_pct")
    avg_stop = stats.get("avg_stop_pct")
    avg_line = ""
    if avg_tp2 is not None and avg_stop is not None:
        avg_line = f"\nAverage verified move: +{avg_tp2:.1f}% to TP2, -{avg_stop:.1f}% to stop."

    body = f"""📒 Verified Setup Scorecard — last {days} day(s)

Every call this account publishes carries real Entry / TP1 / TP2 / SL levels, and every outcome below is counted from the 1-minute candle ledger - wins and losses the same way.

Closed in window: {closed}
• Reached TP2 (full target): {tp2}{tp2_share}
• Banked TP1 first, then stopped: {tp1_partial}
• Stopped at invalidation: {stopped}
• Expired unresolved / superseded: {expired}
Currently open: {open_now}{avg_line}

Honesty note: a signal service that only shows wins is marketing. These numbers are the complete record for the window.

$BTC #BinanceSquare
⚠️ Past setups do not guarantee future results. Not financial advice."""

    if len(body) + 1 > 1900:
        body = body[:1899].rstrip()
    return _finalize_post(body)


def format_explainer_post(data: dict[str, Any]) -> str:
    """Render the educational explainer (manual ``educational_explainer`` slot)."""

    title = " ".join(str(data.get("title", "Reading today's market structure")).split())
    hook = _hook_lead(data)
    concept = " ".join(str(data.get("concept", "Market structure")).split())
    points_raw = data.get("points", [])
    points = (
        [" ".join(str(point).split()) for point in points_raw if str(point).strip()][:5]
        if isinstance(points_raw, list)
        else []
    )
    takeaway = _clip_sentence(data.get("takeaway", ""), 200)
    question = _clip_sentence(data.get("question", ""), 160)

    numbered = [f"{index}. {point}" for index, point in enumerate(points, start=1)]

    body_lines = [f"📚 {title}"]
    if hook:
        body_lines += ["", hook]
    body_lines += ["", f"Concept: {concept}", ""]
    body_lines += numbered
    if takeaway:
        body_lines += ["", f"💡 Takeaway: {takeaway}"]
    if question:
        body_lines += ["", question]
    body_lines += [
        "",
        "$BTC $ETH #BinanceSquare #LearnCrypto",
        "⚠️ Educational content only. Not financial advice; always DYOR.",
    ]

    body = "\n".join(body_lines)
    if len(body) + 1 > 1900:
        body = body[:1899].rstrip()
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
