"""Monitor active signals and publish image cards for TP1/TP2 touches.

Unlike a current-price-only monitor, this version scans Binance one-minute
candle highs/lows between successful checks.  A target that was touched and
then retraced before the next GitHub Actions run is therefore still detected.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from bot.card_generator import generate_position_card, normalize_leverage
from bot.market_data import INTERVAL_TO_MS, get_klines_range
from bot.poster import format_success_post, get_current_price

ROOT_DIR = Path(__file__).resolve().parents[1]
ACTIVE_FILE = ROOT_DIR / "data" / "active_trades.json"
CLOSED_FILE = ROOT_DIR / "data" / "closed_trades.json"

PublishFunction = Callable[[str, list[str]], Any]


@dataclass(frozen=True)
class TradeEvent:
    kind: str
    price: float
    happened_at: datetime


@dataclass(frozen=True)
class ScanResult:
    terminal_event: TradeEvent | None = None
    new_tp1_event: TradeEvent | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None

    try:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None

    # Existing repository records use naive timestamps written by UTC runners.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _datetime_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


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
    override = os.getenv("TARGET_CARD_LEVERAGE", "15").strip()
    raw_value: Any = override if override else trade.get("leverage", 15)
    return normalize_leverage(raw_value, default=15)


def _monitor_interval() -> str:
    interval = os.getenv("MONITOR_CANDLE_INTERVAL", "1m").strip()
    if interval not in INTERVAL_TO_MS:
        raise ValueError(
            f"MONITOR_CANDLE_INTERVAL must be one of {sorted(INTERVAL_TO_MS)}, "
            f"got {interval!r}"
        )
    return interval


def _monitor_max_lookback_hours() -> int:
    value = int(os.getenv("MONITOR_MAX_LOOKBACK_HOURS", "168"))
    if not 1 <= value <= 24 * 30:
        raise ValueError("MONITOR_MAX_LOOKBACK_HOURS must be between 1 and 720")
    return value


def _monitor_max_pages() -> int:
    value = int(os.getenv("MONITOR_MAX_KLINE_PAGES", "20"))
    if not 1 <= value <= 100:
        raise ValueError("MONITOR_MAX_KLINE_PAGES must be between 1 and 100")
    return value


def _create_target_card(
    trade: dict[str, Any],
    hit: str,
    hit_price: float,
    hit_time: datetime,
) -> str:
    coin = str(trade["coin"]).upper()
    safe_hit = str(hit).upper()
    output = Path(tempfile.gettempdir()) / f"{coin}_{safe_hit}_target_card.png"

    card_path, stats = generate_position_card(
        coin=coin,
        entry_price=float(trade["entry_price"]),
        close_price=float(hit_price),
        direction=_direction(trade),
        leverage=_card_leverage(trade),
        position_size=_card_position_size(trade),
        save_path=str(output),
        timestamp=hit_time,
    )

    print(
        "[Monitor] Target card ready:",
        json.dumps(stats, ensure_ascii=False),
    )
    return card_path


def _publish_target_hit(
    publish_func: PublishFunction,
    trade: dict[str, Any],
    event: TradeEvent,
) -> str:
    post = format_success_post(trade, event.kind, event.price)
    card_path = _create_target_card(
        trade=trade,
        hit=event.kind,
        hit_price=event.price,
        hit_time=event.happened_at,
    )

    if not Path(card_path).is_file():
        raise RuntimeError(f"Target card is missing: {card_path}")

    publish_func(post, [card_path])
    return card_path


def _touches_for_candle(
    trade: dict[str, Any],
    high: float,
    low: float,
) -> tuple[bool, bool, bool]:
    direction = _direction(trade)
    tp1 = float(trade["tp1"])
    tp2 = float(trade["tp2"])
    stop = float(trade["sl"])

    if direction == "LONG":
        return low <= stop, high >= tp1, high >= tp2
    return high >= stop, low <= tp1, low <= tp2


def _scan_candles(
    trade: dict[str, Any],
    candles: list[dict[str, float | int]],
    scan_end: datetime,
) -> ScanResult:
    """Process candles chronologically and return newly discovered events.

    If stop and target occur inside the same one-minute candle, their exact
    order is unknowable from OHLC data.  The conservative rule records SL first
    and does not claim a target from that ambiguous candle.
    """

    tp1_already_recorded = bool(trade.get("tp1_hit"))
    discovered_tp1: TradeEvent | None = None

    for candle in candles:
        high = float(candle["high"])
        low = float(candle["low"])
        event_time = _datetime_from_ms(int(candle["close_time"]))
        if event_time > scan_end:
            event_time = scan_end

        stop_touched, tp1_touched, tp2_touched = _touches_for_candle(
            trade,
            high,
            low,
        )

        # Conservative ordering for an ambiguous one-minute candle.
        if stop_touched and (tp1_touched or tp2_touched):
            return ScanResult(
                terminal_event=TradeEvent(
                    kind="SL",
                    price=float(trade["sl"]),
                    happened_at=event_time,
                ),
                new_tp1_event=discovered_tp1,
            )

        if stop_touched:
            return ScanResult(
                terminal_event=TradeEvent(
                    kind="SL",
                    price=float(trade["sl"]),
                    happened_at=event_time,
                ),
                new_tp1_event=discovered_tp1,
            )

        # TP2 is terminal.  If both targets are crossed in one candle, publish
        # only the stronger TP2 result instead of sending two posts together.
        if tp2_touched:
            return ScanResult(
                terminal_event=TradeEvent(
                    kind="TP2",
                    price=float(trade["tp2"]),
                    happened_at=event_time,
                ),
                new_tp1_event=discovered_tp1,
            )

        if tp1_touched and not tp1_already_recorded:
            discovered_tp1 = TradeEvent(
                kind="TP1",
                price=float(trade["tp1"]),
                happened_at=event_time,
            )
            tp1_already_recorded = True

    return ScanResult(new_tp1_event=discovered_tp1)


def _scan_current_price(
    trade: dict[str, Any],
    current_price: float,
    scan_end: datetime,
) -> ScanResult:
    """Fallback for symbols that return no candle history."""

    direction = _direction(trade)
    stop = float(trade["sl"])
    tp1 = float(trade["tp1"])
    tp2 = float(trade["tp2"])

    if _is_stop_hit(direction, current_price, stop):
        return ScanResult(
            terminal_event=TradeEvent("SL", stop, scan_end)
        )
    if _is_target_hit(direction, current_price, tp2):
        return ScanResult(
            terminal_event=TradeEvent("TP2", tp2, scan_end)
        )
    if _is_target_hit(direction, current_price, tp1) and not trade.get("tp1_hit"):
        return ScanResult(
            new_tp1_event=TradeEvent("TP1", tp1, scan_end)
        )

    return ScanResult()


def _history_start(
    trade: dict[str, Any],
    scan_end: datetime,
) -> datetime | None:
    """Return the last successful check time.

    Existing legacy trades do not have ``last_checked_at``.  They are baselined
    at the current run instead of replaying many old targets and flooding
    Binance Square.  New signals receive ``last_checked_at`` when created.
    """

    last_checked = _parse_iso_datetime(trade.get("last_checked_at"))
    if last_checked is None:
        return None

    max_start = scan_end - timedelta(hours=_monitor_max_lookback_hours())
    start = max(last_checked, max_start)

    if start > scan_end:
        return scan_end
    return start


def _scan_trade_history(
    trade: dict[str, Any],
    current_price: float,
    scan_end: datetime,
) -> ScanResult:
    start = _history_start(trade, scan_end)
    if start is None:
        print(
            f"[Monitor] {trade.get('coin')} has no last_checked_at; "
            "starting history tracking now without replaying legacy candles"
        )
        return _scan_current_price(trade, current_price, scan_end)

    interval = _monitor_interval()
    symbol = f"{str(trade['coin']).upper()}USDT"
    candles = get_klines_range(
        symbol=symbol,
        interval=interval,
        start_time_ms=int(start.timestamp() * 1000),
        end_time_ms=int(scan_end.timestamp() * 1000),
        max_pages=_monitor_max_pages(),
    )

    print(
        f"[Monitor] Historical scan {symbol}: {len(candles)} {interval} candle(s) "
        f"from {_iso_utc(start)} to {_iso_utc(scan_end)}"
    )

    if not candles:
        return _scan_current_price(trade, current_price, scan_end)

    return _scan_candles(trade, candles, scan_end)


def _record_tp1(
    trade: dict[str, Any],
    event: TradeEvent,
    card_path: str,
) -> None:
    trade["tp1_hit"] = True
    trade["tp1_hit_price"] = event.price
    trade["tp1_hit_at"] = _iso_utc(event.happened_at)
    trade["tp1_card"] = Path(card_path).name


def _close_trade(
    trade: dict[str, Any],
    event: TradeEvent,
    status: str,
) -> None:
    trade["status"] = status
    trade["closed_price"] = event.price
    trade["closed_at"] = _iso_utc(event.happened_at)
    trade["last_checked_at"] = _iso_utc(event.happened_at)
    save_closed(trade)


def check_trades_and_maybe_post(publish_func: PublishFunction) -> None:
    trades = load_active()

    if not trades:
        print("No active trades.")
        return

    remaining: list[dict[str, Any]] = []

    for trade in trades:
        coin = str(trade.get("coin", "UNKNOWN")).upper()
        scan_end = _utc_now()

        try:
            direction = _direction(trade)
            current_price = float(get_current_price(coin))
            entry = float(trade["entry_price"])
            tp1 = float(trade["tp1"])
            tp2 = float(trade["tp2"])
            stop = float(trade["sl"])

            if min(current_price, entry, tp1, tp2, stop) <= 0:
                raise ValueError("Trade contains a non-positive price")

            print(
                f"[Monitor] {coin} {direction}: Entry {entry} | Now {current_price} | "
                f"TP1 {tp1} | TP2 {tp2} | SL {stop}"
            )

            result = _scan_trade_history(trade, current_price, scan_end)
            terminal = result.terminal_event
            new_tp1 = result.new_tp1_event

            if terminal and terminal.kind == "TP2":
                card_path = _publish_target_hit(publish_func, trade, terminal)
                trade["tp2_card"] = Path(card_path).name
                _close_trade(trade, terminal, "TP2_HIT")
                print(
                    f"[Monitor] TP2 detected for {coin} at "
                    f"{_iso_utc(terminal.happened_at)}; image published and trade closed"
                )
                continue

            if terminal and terminal.kind == "SL":
                # If TP1 occurred in an earlier candle before the later SL,
                # preserve the target-hit post that the delayed monitor missed.
                if new_tp1 and not trade.get("tp1_hit"):
                    card_path = _publish_target_hit(publish_func, trade, new_tp1)
                    _record_tp1(trade, new_tp1, card_path)
                    print(
                        f"[Monitor] Historical TP1 detected for {coin} before later SL; "
                        "image published"
                    )

                _close_trade(trade, terminal, "SL_HIT")
                print(
                    f"[Monitor] SL detected for {coin} at "
                    f"{_iso_utc(terminal.happened_at)}; closed without an SL post"
                )
                continue

            if new_tp1 and not trade.get("tp1_hit"):
                card_path = _publish_target_hit(publish_func, trade, new_tp1)
                _record_tp1(trade, new_tp1, card_path)
                print(
                    f"[Monitor] TP1 detected for {coin} at "
                    f"{_iso_utc(new_tp1.happened_at)}; image published"
                )

            trade["last_checked_at"] = _iso_utc(scan_end)
            remaining.append(trade)

        except Exception as exc:
            # Keep the previous last_checked_at on any API/image/publish error.
            # The next run will rescan the same range and cannot silently miss it.
            print(f"[Monitor Error] {coin}: {exc}")
            remaining.append(trade)

    save_active(remaining)
