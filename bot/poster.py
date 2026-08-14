"""Text formatting and verified live-price helpers for Binance Square posts."""

from __future__ import annotations

import random
from typing import Any

from bot.market_data import get_24h_data


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


def format_signal_post(signal: dict[str, Any]) -> str:
    """Create a concise, rotating signal post for LONG or SHORT setups."""

    coin = str(signal["coin"]).upper()
    entry = float(signal["entry_price"])
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])
    sl = float(signal["sl"])
    change = float(signal.get("change", 0.0))
    direction = _direction(signal)

    if direction == "LONG" and not (sl < entry < tp1 < tp2):
        raise ValueError("Invalid LONG levels: expected SL < Entry < TP1 < TP2")
    if direction == "SHORT" and not (tp2 < tp1 < entry < sl):
        raise ValueError("Invalid SHORT levels: expected TP2 < TP1 < Entry < SL")

    hooks = [
        f"${coin} just made a strong structural shift. Momentum building? ðŸš€",
        f"Explosive 24H move on ${coin}. Is continuation next?",
        f"${coin} is gaining traction fast. Watching the next expansion.",
        f"Breakout structure forming on ${coin}. Watching closely ðŸ‘€",
        f"${coin} is showing expansion after a liquidity sweep.",
    ]

    engagement_lines = [
        "Continuation or a short-term pullback first? ðŸ‘‡",
        "Would you hold for TP2 or secure profits at TP1? ðŸ¤”",
        "Are you entering on momentum or waiting for a retrace?",
        "Do you see further expansion from here?",
    ]

    hashtag_sets = [
        f"${coin} #BinanceSquare #CryptoSignals #Altcoins",
        f"${coin} #SMC #PriceAction #Crypto",
        f"${coin} #TechnicalAnalysis #CryptoTrading",
        f"${coin} #AltcoinSeason #CryptoMarket",
    ]

    tp1_pct = _move_percent(entry, tp1, direction)
    tp2_pct = _move_percent(entry, tp2, direction)
    sl_pct = abs(_move_percent(entry, sl, direction))

    change_label = f"{change:+.1f}%"

    return f"""{random.choice(hooks)}

24H move: {change_label}. Direction: {direction}.

ðŸ“Œ Entry: ${entry}
ðŸŽ¯ TP1: ${tp1} (+{tp1_pct:.1f}%)
ðŸŽ¯ TP2: ${tp2} (+{tp2_pct:.1f}%)
ðŸ›‘ SL: ${sl} (-{sl_pct:.1f}%)

Risk remains defined by the stop level.

{random.choice(engagement_lines)}

{random.choice(hashtag_sets)}

Not financial advice. DYOR.
"""


def format_success_post(
    signal: dict[str, Any],
    hit: str,
    current_price: float,
) -> str:
    """Create a TP1/TP2 result post using direction-aware PnL."""

    coin = str(signal["coin"]).upper()
    entry = float(signal["entry_price"])
    current = float(current_price)
    direction = _direction(signal)
    hit = str(hit).upper().strip()

    if hit not in {"TP1", "TP2"}:
        raise ValueError(f"Unsupported target label: {hit}")
    if entry <= 0 or current <= 0:
        raise ValueError("Entry and current prices must be greater than zero")

    pnl = _move_percent(entry, current, direction)

    reactions = [
        "Momentum delivered as anticipated âœ…",
        "The planned structure reached its target ðŸ”¥",
        "Liquidity â†’ Expansion â†’ Target hit ðŸš€",
        "Clean movement into the planned level ðŸ“ˆ",
        "The setup reached its defined objective âœ…",
    ]

    celebration_lines = [
        "Discipline always matters.",
        "Patience over emotion.",
        "Risk management comes first.",
        "The planned target was respected.",
    ]

    return f"""ðŸŽ‰ Target Hit!

${coin} {hit} âœ…

Direction: {direction}
Entry: ${entry}
Current: ${current}
Move: {pnl:+.2f}%

{random.choice(reactions)}
{random.choice(celebration_lines)}

${coin} #TargetHit #Crypto

Not financial advice. DYOR.
"""


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
