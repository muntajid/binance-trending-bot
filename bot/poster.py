"""Evidence-based Binance Square post templates built from top 100 creator benchmark data.

Supports 4 core publication modes:
1. Rapid Alert Mode (120-180 characters, +12.7% within-creator reach)
2. Explained Master Setup Mode (650-950 characters, Why Now + Invalidation, +9.0% reach)
3. Macro & Bitcoin Market Overview Mode (500-800 characters)
4. Viral Meme & Community Culture Mode (200-450 characters, +49.0% paired uplift)

Plus event-driven verifiable Target Hit updates with 0% forced A/B bait.
"""

from __future__ import annotations

import difflib
import json
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


# ---------------------------------------------------------------------------
# MODE 1: Rapid Alert Post (120 - 180 characters)
# ---------------------------------------------------------------------------

def format_rapid_alert_post(signal: dict[str, Any]) -> str:
    """
    Format an ultra-compact, high-speed 120-180 character trade alert.
    Research benchmark: Rapid alerts had 1,221.5 median views (+12.7% uplift).
    """

    coin = str(signal["coin"]).upper().strip()
    entry = float(signal["entry_price"])
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])
    stop = float(signal["sl"])
    direction = _direction(signal)
    rapid_reason = str(signal.get("rapid_reason", "Momentum breakout above 1H resistance.")).strip()
    hashtag = _get_dynamic_hashtag()

    entry_s = _format_price(entry)
    tp1_s = _format_price(tp1)
    tp2_s = _format_price(tp2)
    sl_s = _format_price(stop)

    templates = (
        f"⚡ ${coin} — {direction} at ${entry_s}\nTargets: ${tp1_s} | ${tp2_s}\nInvalidation: ${sl_s}\n{rapid_reason}\n\n${coin} {hashtag}",
        f"🚨 ${coin} MOMENTUM ALERT ({direction})\nEntry: ${entry_s}\nTP1: ${tp1_s} | TP2: ${tp2_s}\nSL: ${sl_s}\n{rapid_reason}\n\n${coin} {hashtag}",
        f"🎯 ${coin} {direction} SETUP\nEntry: ${entry_s} (CMP)\nTP: ${tp1_s} / ${tp2_s} | SL: ${sl_s}\n{rapid_reason}\n\n${coin} {hashtag}",
    )

    selected = _RNG.choice(templates)
    return _finalize_post(selected)


# ---------------------------------------------------------------------------
# MODE 2: Explained Master Setup Post (650 - 950 characters)
# ---------------------------------------------------------------------------

def format_explained_post(signal: dict[str, Any]) -> str:
    """
    Format an in-depth, explained setup with Why Now, Invalidation, and Risk Rules.
    Research benchmark: Explained setups had 1,183.5 median views (+9.0% paired uplift).
    """

    coin = str(signal["coin"]).upper().strip()
    entry = float(signal["entry_price"])
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])
    stop = float(signal["sl"])
    change = float(signal.get("change", 0.0))
    direction = _direction(signal)
    confidence = int(signal.get("confidence", 85))

    why_now = str(
        signal.get(
            "why_now",
            f"${coin} reclaimed key support with strong 24H volume surge ({change:+.1f}%) and confirmed a market structure shift.",
        )
    ).strip()

    invalidation = str(
        signal.get(
            "invalidation",
            f"A 1H candle close below ${_format_price(stop)} invalidates this continuation thesis.",
        )
    ).strip()

    tp1_pct = _move_percent(entry, tp1, direction)
    tp2_pct = _move_percent(entry, tp2, direction)
    stop_pct = abs(_move_percent(entry, stop, direction))
    rr = _risk_reward(entry, tp1, stop)

    hooks = (
        f"🚨 ${coin} Volume Expansion ({change:+.1f}% 24H) — Key Resistance Flipped! 📈",
        f"⚡ ${coin} Smart Money Accumulation Detected ({change:+.1f}%) — Setup in Play! 🎯",
        f"📊 ${coin} High-Probability {direction} Structure: Volatility Expanding ({change:+.1f}%)!",
        f"👀 ${coin} Tested Critical Decision Level ({change:+.1f}% 24H) — Setup Overview! 🚀",
    )

    management_tips = (
        "💡 Risk Rule: Lock in 50% at TP1 and trail Stop Loss to entry for a risk-free run to TP2.",
        "💡 Strategy: Wait for 1H candle confirmation. Never over-leverage on momentum expansions.",
        "💡 Execution: Maintain strict capital discipline. Protect profit systematically at target levels.",
    )

    selected_hook = _RNG.choice(hooks)
    selected_tip = _RNG.choice(management_tips)
    cta = _get_cta_prompt(coin)
    hashtag = _get_dynamic_hashtag()

    body = f"""{selected_hook}

${coin} — {direction} SETUP
• Entry Zone (CMP): ${_format_price(entry)}
• Target 1: ${_format_price(tp1)} (+{tp1_pct:.1f}%) 🎯
• Target 2: ${_format_price(tp2)} (+{tp2_pct:.1f}%) 🚀
• Invalidation (SL): ${_format_price(stop)} (-{stop_pct:.1f}%) 🛡️
• Risk/Reward: 1:{rr:.2f}R | Confidence: {confidence}%
• Suggested Leverage: 3x - 5x (Max 15x)

Why now: {why_now}
What changes the view: {invalidation}

{selected_tip}{cta}

${coin} $BTC {hashtag}
⚠️ Educational market analysis. Always manage your risk & DYOR."""

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
