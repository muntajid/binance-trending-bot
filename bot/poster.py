"""Varied, engagement-focused Binance Square post templates.

Target-hit posts use 12 distinct layouts and choose one with SystemRandom.
The UTF-8 workflow patch keeps the emojis readable through Square OpenAPI.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any, Callable

from bot.market_data import get_24h_data

_RNG = random.SystemRandom()
ROOT_DIR = Path(__file__).resolve().parents[1]
TEMPLATE_STATE_FILE = ROOT_DIR / "data" / "post_template_state.json"
MAX_SQUARE_POST_CHARACTERS = 2100

# These escaped markers identify common UTF-8-as-Windows-1252/Latin-1 damage.
# The source file deliberately stays ASCII-only; emoji are written as Python
# Unicode escapes and become real Unicode only when a post is generated.
_MOJIBAKE_MARKERS = (
    "\u00f0\u0178",  # Common prefix produced from four-byte emoji.
    "\u00e2\u0153",  # Common prefix produced from check-mark style symbols.
    "\u00e2\u0161",  # Common prefix produced from lightning style symbols.
    "\u00ef\u00b8",  # Corrupted variation selector.
    "\u00c2",        # Stray UTF-8 lead byte rendered as text.
    "\ufffd",        # Unicode replacement character.
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

    # A strict round trip catches unpaired surrogates and other invalid text.
    value.encode("utf-8", errors="strict").decode("utf-8", errors="strict")

    if len(value) > MAX_SQUARE_POST_CHARACTERS:
        raise ValueError(
            f"Binance Square post is {len(value)} characters; "
            f"maximum is {MAX_SQUARE_POST_CHARACTERS}"
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
        decimals = 6
    else:
        decimals = 8

    result = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return result if result else "0"


def _risk_reward(entry: float, target: float, stop: float) -> float:
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0:
        raise ValueError("Entry and stop cannot be equal")
    return reward / risk


def _target_context(
    signal: dict[str, Any],
    hit: str,
    current_price: float,
) -> dict[str, Any]:
    coin = str(signal["coin"]).upper().strip()
    entry = float(signal["entry_price"])
    target = float(current_price)
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])
    direction = _direction(signal)
    hit = str(hit).upper().strip()

    if hit not in {"TP1", "TP2"}:
        raise ValueError(f"Unsupported target label: {hit}")
    if entry <= 0 or target <= 0:
        raise ValueError("Entry and target prices must be greater than zero")

    move = _move_percent(entry, target, direction)

    if hit == "TP1":
        next_plan = f"Next target: TP2 at ${_format_price(tp2)}"
        status = "The setup remains active for TP2."
    else:
        next_plan = "Final target reached."
        status = "The planned setup is now complete."

    return {
        "coin": coin,
        "hit": hit,
        "direction": direction,
        "entry": _format_price(entry),
        "target": _format_price(target),
        "tp1": _format_price(tp1),
        "tp2": _format_price(tp2),
        "move": move,
        "next_plan": next_plan,
        "status": status,
    }


def _template_01(c: dict[str, Any]) -> str:
    return f"""\U0001F3AF ${c['coin']} {c['hit']} HIT - {c['move']:+.2f}%

Entry: ${c['entry']}
Target: ${c['target']}
{c['next_plan']}

What would you do here?
A) Secure partial profit
B) Hold for the next target

Comment A or B."""


def _template_02(c: dict[str, Any]) -> str:
    return f"""\u2705 TARGET CONFIRMED: ${c['coin']} {c['hit']}

The {c['direction']} setup moved {c['move']:+.2f}% from entry.
Entry: ${c['entry']}
Hit price: ${c['target']}

{c['status']}

Would you move the stop to breakeven now?
YES or NO?"""


def _template_03(c: dict[str, Any]) -> str:
    return f"""\U0001F525 ${c['coin']} DELIVERED - {c['hit']} REACHED

Entry: ${c['entry']}
Target reached: ${c['target']}
Move: {c['move']:+.2f}%

{c['next_plan']}

Rate this setup from 1 to 10 in the comments."""


def _template_04(c: dict[str, Any]) -> str:
    return f"""\U0001F4C8 ${c['coin']} UPDATE: {c['hit']} IS IN

{c['direction']} entry: ${c['entry']}
Target: ${c['target']}
Result: {c['move']:+.2f}%

Momentum continuation or a retest next?
A) Continuation
B) Retest

Drop A or B below."""


def _template_05(c: dict[str, Any]) -> str:
    return f"""\U0001F680 ${c['coin']} JUST REACHED {c['hit']}

The planned level at ${c['target']} has been touched.
Entry was ${c['entry']}.
Price move: {c['move']:+.2f}%.

{c['next_plan']}

Did you follow this setup?
A) Yes
B) Watched only"""


def _template_06(c: dict[str, Any]) -> str:
    return f"""\U0001F3C6 TARGET UPDATE - ${c['coin']} {c['hit']}

Entry to target: ${c['entry']} -> ${c['target']}
Move captured: {c['move']:+.2f}%
Direction: {c['direction']}

{c['status']}

Which matters more after a target?
A) Protecting profit
B) Giving the trade more room"""


def _template_07(c: dict[str, Any]) -> str:
    return f"""\u26A1 ${c['coin']} MOMENTUM CHECK

{c['hit']} reached at ${c['target']}.
Entry: ${c['entry']}
Result: {c['move']:+.2f}%

{c['next_plan']}

Would you secure 50% here or keep the full position?
A) Secure 50%
B) Keep full"""


def _template_08(c: dict[str, Any]) -> str:
    return f"""\U0001F49A ${c['coin']} TARGET REACHED

Planned {c['direction']} entry: ${c['entry']}
{c['hit']} level: ${c['target']}
Move: {c['move']:+.2f}%

Risk management question:
A) Move SL to entry
B) Keep the original SL

What is your choice?"""


def _template_09(c: dict[str, Any]) -> str:
    return f"""\U0001F4A5 ${c['coin']} {c['hit']} COMPLETE

From ${c['entry']} to ${c['target']}.
Performance: {c['move']:+.2f}%.

{c['next_plan']}

Be honest: would you take profit here or wait?
A) Take profit
B) Wait"""


def _template_10(c: dict[str, Any]) -> str:
    return f"""\U0001F7E2 RESULT ALERT: ${c['coin']} {c['hit']}

Direction: {c['direction']}
Entry: ${c['entry']}
Target hit: ${c['target']}
Move: {c['move']:+.2f}%

{c['status']}

What should the next update include?
A) More chart details
B) More risk-management details"""


def _template_11(c: dict[str, Any]) -> str:
    return f"""\U0001F947 ${c['coin']} REACHED THE PLANNED {c['hit']}

Entry: ${c['entry']}
Exit level: ${c['target']}
Price move: {c['move']:+.2f}%

{c['next_plan']}

Would you prefer one final target or multiple take-profit levels?
A) One target
B) Multiple targets"""


def _template_12(c: dict[str, Any]) -> str:
    return f"""\U0001F514 ${c['coin']} TRADE UPDATE - {c['hit']} HIT

The market touched ${c['target']} after the ${c['entry']} entry.
Total move: {c['move']:+.2f}%.

{c['status']}

Your decision at this level?
A) Lock in gains
B) Continue with managed risk

Reply A or B."""


TARGET_TEMPLATES: tuple[Callable[[dict[str, Any]], str], ...] = (
    _template_01,
    _template_02,
    _template_03,
    _template_04,
    _template_05,
    _template_06,
    _template_07,
    _template_08,
    _template_09,
    _template_10,
    _template_11,
    _template_12,
)

HASHTAG_SETS = (
    "#Altcoins #CryptoTrading",
    "#PriceAction #CryptoMarket",
    "#TechnicalAnalysis #Trading",
    "#TargetHit #AltcoinTrading",
)


def _load_template_state() -> dict[str, Any]:
    if not TEMPLATE_STATE_FILE.exists():
        return {}

    try:
        value = json.loads(TEMPLATE_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    return value if isinstance(value, dict) else {}


def _save_template_state(value: dict[str, Any]) -> None:
    TEMPLATE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = TEMPLATE_STATE_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, TEMPLATE_STATE_FILE)


def _select_target_template() -> int:
    """Choose templates in a shuffled non-repeating cycle.

    All 12 layouts are used in random order before the pool is refilled.  The
    immediately previous layout is excluded when a new pool starts, so two
    consecutive target posts cannot use the same template.
    """

    state = _load_template_state()
    total = len(TARGET_TEMPLATES)

    raw_last = state.get("last_template_id")
    last_id = raw_last if isinstance(raw_last, int) and 0 <= raw_last < total else None

    raw_remaining = state.get("remaining_template_ids", [])
    remaining: list[int] = []
    if isinstance(raw_remaining, list):
        for value in raw_remaining:
            if (
                isinstance(value, int)
                and 0 <= value < total
                and value != last_id
                and value not in remaining
            ):
                remaining.append(value)

    if not remaining:
        remaining = [index for index in range(total) if index != last_id]
        if last_id is None:
            remaining = list(range(total))

    selected = _RNG.choice(remaining)
    remaining.remove(selected)

    _save_template_state(
        {
            "last_template_id": selected,
            "remaining_template_ids": remaining,
        }
    )
    return selected


def format_signal_post(signal: dict[str, Any]) -> str:
    """Create a varied signal post with a single clear engagement prompt."""

    coin = str(signal["coin"]).upper().strip()
    entry = float(signal["entry_price"])
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])
    stop = float(signal["sl"])
    change = float(signal.get("change", 0.0))
    direction = _direction(signal)

    if direction == "LONG" and not (stop < entry < tp1 < tp2):
        raise ValueError("Invalid LONG levels: expected SL < Entry < TP1 < TP2")
    if direction == "SHORT" and not (tp2 < tp1 < entry < stop):
        raise ValueError("Invalid SHORT levels: expected TP2 < TP1 < Entry < SL")

    tp1_pct = _move_percent(entry, tp1, direction)
    tp2_pct = _move_percent(entry, tp2, direction)
    stop_pct = abs(_move_percent(entry, stop, direction))
    rr = _risk_reward(entry, tp1, stop)

    hooks = (
        f"\U0001F50D ${coin} moved {change:+.1f}% in 24H. Momentum or exhaustion?",
        f"\u26A1 ${coin} is active. Breakout continuation or pullback first?",
        f"\U0001F4CA ${coin} setup watch: the next reaction could define direction.",
        f"\U0001F680 ${coin} has strong attention today. Is momentum sustainable?",
        f"\U0001F440 ${coin} is approaching a decision zone. Watching the reaction.",
        f"\U0001F525 ${coin} volatility is expanding. Here is the risk-defined plan.",
    )

    questions = (
        "A) Enter on momentum\nB) Wait for a pullback",
        "A) Take the setup\nB) Wait for confirmation",
        "A) Focus on TP1\nB) Hold for TP2",
        "A) Bullish continuation\nB) Short-term retrace",
    )

    hashtags = _RNG.choice(HASHTAG_SETS)

    return _finalize_post(
        f"""{_RNG.choice(hooks)}

{direction} SETUP

Entry: ${_format_price(entry)}
TP1: ${_format_price(tp1)} (+{tp1_pct:.1f}%)
TP2: ${_format_price(tp2)} (+{tp2_pct:.1f}%)
SL: ${_format_price(stop)} (-{stop_pct:.1f}%)
Risk/Reward to TP1: {rr:.2f}R

What would you do?
{_RNG.choice(questions)}

Reply A or B. Follow for TP updates.

${coin} {hashtags}
Simulated setup. Trade responsibly.
"""
    )


def format_success_post(
    signal: dict[str, Any],
    hit: str,
    current_price: float,
) -> str:
    """Choose one of 12 complete target-hit layouts at random."""

    context = _target_context(signal, hit, current_price)
    template_index = _select_target_template()
    body = TARGET_TEMPLATES[template_index](context)
    hashtags = _RNG.choice(HASHTAG_SETS)

    notional = os.getenv("TARGET_CARD_POSITION_SIZE_USDT", "5000").strip()
    leverage = os.getenv("TARGET_CARD_LEVERAGE", "15").strip()

    print(f"[Poster] Selected target template {template_index + 1}/{len(TARGET_TEMPLATES)}")

    return _finalize_post(
        f"""{body}

Follow for the next setup.
${context['coin']} {hashtags}
"""
    )


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
