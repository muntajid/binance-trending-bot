"""Monitor active signals and publish an image card when TP1 or TP2 is hit."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from bot.card_generator import generate_position_card, normalize_leverage
from bot.poster import format_success_post, get_current_price

ROOT_DIR = Path(__file__).resolve().parents[1]
ACTIVE_FILE = ROOT_DIR / "data" / "active_trades.json"
CLOSED_FILE = ROOT_DIR / "data" / "closed_trades.json"

PublishFunction = Callable[[str, list[str]], Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_trade_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read valid JSON from {path}: {exc}") from exc

    if not isinstance(data, list):
        raise RuntimeError(f"Expected a JSON list in {path}")

    return [item for item in data if isinstance(item, dict)]


def _atomic_write_json(path: Path, value: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_active() -> list[dict[str, Any]]:
    return _read_trade_list(ACTIVE_FILE)


def save_active(trades: list[dict[str, Any]]) -> None:
    _atomic_write_json(ACTIVE_FILE, trades)


def save_closed(trade: dict[str, Any]) -> None:
    closed = _read_trade_list(CLOSED_FILE)
    closed.append(trade)
    _atomic_write_json(CLOSED_FILE, closed)


def _direction(trade: dict[str, Any]) -> str:
    direction = str(trade.get("direction", "LONG")).upper().strip()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"Unsupported direction: {direction}")
    return direction


def _is_stop_hit(direction: str, price: float, stop: float) -> bool:
    return price <= stop if direction == "LONG" else price >= stop


def _is_target_hit(direction: str, price: float, target: float) -> bool:
    return price >= target if direction == "LONG" else price <= target


def _card_position_size(trade: dict[str, Any]) -> float:
    """Get an explicit notional size; never use a random value."""

    raw_value = trade.get(
        "position_size_usdt",
        trade.get(
            "position_size",
            os.getenv("TARGET_CARD_POSITION_SIZE_USDT", "5000"),
        ),
    )
    value = float(raw_value)
    if value <= 0:
        raise ValueError("Target-card position size must be greater than zero")
    return value


def _card_leverage(trade: dict[str, Any]) -> int:
    # Target cards use the exact configured leverage.  The production default
    # is 15x, even when an older signal contains a range such as "3x-5x".
    override = os.getenv("TARGET_CARD_LEVERAGE", "15").strip()
    raw_value: Any = override if override else trade.get("leverage", 15)
    return normalize_leverage(raw_value, default=15)


def _create_target_card(
    trade: dict[str, Any],
    hit: str,
    current_price: float,
) -> str:
    coin = str(trade["coin"]).upper()
    safe_hit = str(hit).upper()
    output = Path(tempfile.gettempdir()) / f"{coin}_{safe_hit}_target_card.png"

    card_path, stats = generate_position_card(
        coin=coin,
        entry_price=float(trade["entry_price"]),
        close_price=float(current_price),
        direction=_direction(trade),
        leverage=_card_leverage(trade),
        position_size=_card_position_size(trade),
        save_path=str(output),
    )

    print(
        "[Monitor] Target card ready:",
        json.dumps(stats, ensure_ascii=False),
    )
    return card_path


def _publish_target_hit(
    publish_func: PublishFunction,
    trade: dict[str, Any],
    hit: str,
    current_price: float,
) -> str:
    post = format_success_post(trade, hit, current_price)
    card_path = _create_target_card(trade, hit, current_price)

    if not Path(card_path).is_file():
        raise RuntimeError(f"Target card is missing: {card_path}")

    # Passing a non-empty image list makes monitor_runner use post-image.mjs.
    publish_func(post, [card_path])
    return card_path


def check_trades_and_maybe_post(publish_func: PublishFunction) -> None:
    trades = load_active()

    if not trades:
        print("No active trades.")
        return

    remaining: list[dict[str, Any]] = []

    for trade in trades:
        coin = str(trade.get("coin", "UNKNOWN")).upper()

        try:
            direction = _direction(trade)
            price = float(get_current_price(coin))
            entry = float(trade["entry_price"])
            tp1 = float(trade["tp1"])
            tp2 = float(trade["tp2"])
            stop = float(trade["sl"])

            if min(price, entry, tp1, tp2, stop) <= 0:
                raise ValueError("Trade contains a non-positive price")

            print(
                f"[Monitor] {coin} {direction}: Entry {entry} | Now {price} | "
                f"TP1 {tp1} | TP2 {tp2} | SL {stop}"
            )

            # Stop loss is intentionally a silent close.
            if _is_stop_hit(direction, price, stop):
                trade["status"] = "SL_HIT"
                trade["closed_price"] = price
                trade["closed_at"] = _utc_now()
                save_closed(trade)
                print(f"[Monitor] SL hit for {coin}; closed without a success post")
                continue

            # TP2 closes the trade and publishes a final image post.
            if _is_target_hit(direction, price, tp2):
                card_path = _publish_target_hit(
                    publish_func,
                    trade,
                    "TP2",
                    price,
                )
                trade["status"] = "TP2_HIT"
                trade["closed_price"] = price
                trade["closed_at"] = _utc_now()
                trade["tp2_card"] = Path(card_path).name
                save_closed(trade)
                print(f"[Monitor] TP2 hit for {coin}; image post published and trade closed")
                continue

            # TP1 remains active for TP2 and is published only once.
            if _is_target_hit(direction, price, tp1) and not trade.get("tp1_hit"):
                card_path = _publish_target_hit(
                    publish_func,
                    trade,
                    "TP1",
                    price,
                )
                trade["tp1_hit"] = True
                trade["tp1_hit_price"] = price
                trade["tp1_hit_at"] = _utc_now()
                trade["tp1_card"] = Path(card_path).name
                remaining.append(trade)
                print(f"[Monitor] TP1 hit for {coin}; image post published")
                continue

            remaining.append(trade)

        except Exception as exc:
            # Keep the trade active so a temporary API, image or publish failure
            # cannot silently lose it.  A future monitor run can retry safely.
            print(f"[Monitor Error] {coin}: {exc}")
            remaining.append(trade)

    save_active(remaining)
