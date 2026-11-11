"""Monitor active signals and publish image cards for TP1/TP2 touches.

Unlike a current-price-only monitor, this version scans Binance one-minute
candle highs/lows between successful checks.  A target that was touched and
then retraced before the next GitHub Actions run is therefore still detected.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from bot import trade_state
from bot.card_generator import generate_position_card, normalize_leverage
from bot.market_data import INTERVAL_TO_MS, get_klines_range
from bot.poster import (
    format_pressure_post,
    format_stop_post,
    format_success_post,
    get_current_price,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
ACTIVE_FILE = ROOT_DIR / "data" / "active_trades.json"
CLOSED_FILE = ROOT_DIR / "data" / "closed_trades.json"
MONITOR_POSTS_FILE = ROOT_DIR / "data" / "monitor_posts.json"

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


def _trade_id(trade: dict[str, Any]) -> str:
    """Return a stable ID, migrating legacy records deterministically."""

    existing = str(trade.get("trade_id", "")).strip()
    if existing:
        return existing

    identity_fields = {
        "coin": str(trade.get("coin", "")).upper().strip(),
        "direction": str(trade.get("direction", "LONG")).upper().strip(),
        "entry_price": trade.get("entry_price"),
        "tp1": trade.get("tp1"),
        "tp2": trade.get("tp2"),
        "sl": trade.get("sl"),
        "created_at": trade.get("created_at"),
    }
    encoded = json.dumps(
        identity_fields,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    digest = hashlib.sha256(encoded).hexdigest()[:20]
    coin = identity_fields["coin"].lower() or "trade"
    generated = f"{coin}-{digest}"
    trade["trade_id"] = generated
    return generated


def save_closed(trade: dict[str, Any]) -> None:
    """Append a closed trade once; repeated IDs update instead of duplicating."""

    closed = _read_trade_list(CLOSED_FILE)
    identifier = _trade_id(trade)

    for index, existing in enumerate(closed):
        if _trade_id(existing) == identifier:
            closed[index] = trade
            _atomic_write_json(CLOSED_FILE, closed)
            return

    closed.append(trade)
    _atomic_write_json(CLOSED_FILE, closed)


def _created_sort_key(trade: dict[str, Any], fallback_index: int) -> tuple[float, int]:
    created = _parse_iso_datetime(trade.get("created_at"))
    timestamp = created.timestamp() if created is not None else float("-inf")
    return timestamp, fallback_index


def _deduplicate_active_trades(
    trades: list[dict[str, Any]],
    cleanup_time: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep only the newest active setup for each coin.

    Older overlapping records are archived as SUPERSEDED without publishing a
    target or stop update. This migrates the historical state that produced two
    ACE TP2 posts during one monitor run.
    """

    indexed: list[tuple[int, dict[str, Any]]] = []
    newest_index_by_coin: dict[str, int] = {}

    for index, trade in enumerate(trades):
        _trade_id(trade)
        indexed.append((index, trade))

        coin = str(trade.get("coin", "")).upper().strip()
        status = str(trade.get("status", "ACTIVE")).upper().strip()
        if not coin or status != "ACTIVE":
            continue

        previous_index = newest_index_by_coin.get(coin)
        if previous_index is None:
            newest_index_by_coin[coin] = index
            continue

        previous_trade = trades[previous_index]
        if _created_sort_key(trade, index) >= _created_sort_key(
            previous_trade,
            previous_index,
        ):
            newest_index_by_coin[coin] = index

    kept: list[dict[str, Any]] = []
    superseded: list[dict[str, Any]] = []
    cleanup_iso = _iso_utc(cleanup_time)

    for index, trade in indexed:
        coin = str(trade.get("coin", "")).upper().strip()
        status = str(trade.get("status", "ACTIVE")).upper().strip()

        # A non-active record does not belong in active_trades.json. Archive it
        # idempotently but do not rewrite a meaningful terminal status.
        if status != "ACTIVE":
            trade.setdefault("closed_at", cleanup_iso)
            superseded.append(trade)
            continue

        winner_index = newest_index_by_coin.get(coin)
        if not coin or winner_index == index:
            kept.append(trade)
            continue

        winner = trades[winner_index]
        trade["status"] = "SUPERSEDED"
        trade["superseded_at"] = cleanup_iso
        trade["closed_at"] = cleanup_iso
        trade["superseded_by_trade_id"] = _trade_id(winner)
        trade["superseded_reason"] = (
            "Newer active setup exists for the same coin; archived to prevent "
            "duplicate target posts"
        )
        superseded.append(trade)

    return kept, superseded


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
    """Card leverage follows the trade's own configured label by default.

    The old default hardcoded 15x on every proof card while the configured
    trading leverage said 3x-5x - an inconsistency an observant follower can
    spot. TARGET_CARD_LEVERAGE remains as an explicit override.
    """

    override = os.getenv("TARGET_CARD_LEVERAGE", "").strip()
    raw_value: Any = override if override else trade.get("leverage", "")
    return normalize_leverage(raw_value, default=5)


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


# ---------------------------------------------------------------------------
# Outcome-narration budget (SL resolutions + under-pressure updates)
# ---------------------------------------------------------------------------


def _monitor_config() -> dict[str, Any]:
    settings = trade_state.load_config().get("monitor", {})
    return settings if isinstance(settings, dict) else {}


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _sl_posts_enabled() -> bool:
    default = bool(_monitor_config().get("sl_posts_enabled", True))
    return _env_flag("MONITOR_SL_POSTS_ENABLED", default)


def _pressure_posts_enabled() -> bool:
    default = bool(_monitor_config().get("pressure_posts_enabled", True))
    return _env_flag("MONITOR_PRESSURE_POSTS_ENABLED", default)


def _daily_cap(kind: str) -> int:
    """Per-UTC-day cap for an outcome-narration post kind (sl/pressure)."""

    key = {"sl": "sl_posts_max_per_day", "pressure": "pressure_posts_max_per_day"}[kind]
    env_name = {"sl": "MONITOR_SL_POST_MAX_PER_DAY", "pressure": "MONITOR_PRESSURE_POST_MAX_PER_DAY"}[kind]
    default = {"sl": 4, "pressure": 2}[kind]
    raw = os.getenv(env_name, "").strip()
    if not raw:
        try:
            raw = str(int(_monitor_config().get(key, default)))
        except (TypeError, ValueError):
            raw = str(default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(0, min(24, value))


def _load_monitor_budget(now: datetime) -> dict[str, Any]:
    """Daily counters reset each UTC day; increments persist via the workflow commit."""

    day = _iso_utc(now)[:10]
    state: dict[str, Any] = {"day_utc": day, "sl_posts": 0, "pressure_posts": 0}
    if MONITOR_POSTS_FILE.exists():
        try:
            stored = json.loads(MONITOR_POSTS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        if isinstance(stored, dict) and stored.get("day_utc") == day:
            for key in ("sl_posts", "pressure_posts"):
                try:
                    state[key] = max(0, int(stored.get(key, 0)))
                except (TypeError, ValueError):
                    state[key] = 0
    return state


def _save_monitor_budget(state: dict[str, Any]) -> None:
    MONITOR_POSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = MONITOR_POSTS_FILE.with_suffix(MONITOR_POSTS_FILE.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, MONITOR_POSTS_FILE)


def _budget_allows(budget: dict[str, Any], kind: str) -> bool:
    return int(budget.get(f"{kind}_posts", 0)) < _daily_cap(kind)


def _near_invalidation(trade: dict[str, Any], current_price: float) -> bool:
    """True when price is close to the stop without touching it.

    'Close' is a fraction (monitor.pressure_zone_fraction, default 0.35) of the
    trade's own entry->stop distance, so the zone scales with each setup's
    volatility instead of being a fixed absolute number.
    """

    direction = _direction(trade)
    entry = float(trade["entry_price"])
    stop = float(trade["sl"])
    risk = abs(entry - stop)
    if risk <= 0 or current_price <= 0:
        return False

    try:
        fraction = float(_monitor_config().get("pressure_zone_fraction", 0.35))
    except (TypeError, ValueError):
        fraction = 0.35
    fraction = max(0.05, min(0.9, fraction))

    if direction == "LONG":
        return stop < current_price <= stop + fraction * risk
    return stop > current_price >= stop - fraction * risk


def _publish_stop(
    publish_func: PublishFunction,
    trade: dict[str, Any],
    event: TradeEvent,
) -> str | None:
    """Publish the verified stop-resolution post + red STOPPED card."""

    post = format_stop_post(trade, event.price)
    card_path = _create_target_card(trade=trade, hit="SL", hit_price=event.price, hit_time=event.happened_at)
    if not Path(card_path).is_file():
        raise RuntimeError(f"Stop card is missing: {card_path}")
    publish_func(post, [card_path])
    return card_path


def _publish_pressure(
    publish_func: PublishFunction,
    trade: dict[str, Any],
    current_price: float,
) -> None:
    """Publish the mid-trade 'under pressure' journal update (text only)."""

    post = format_pressure_post(trade, current_price)
    publish_func(post, [])


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
        outcome=safe_hit,
        called_at=str(trade.get("created_at", ""))[:10] or None,
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

    # Migrate legacy records and remove overlapping same-coin setups before any
    # market scan or publication. The newest signal remains authoritative.
    cleanup_time = _utc_now()
    trades, superseded = _deduplicate_active_trades(trades, cleanup_time)
    for archived_trade in superseded:
        save_closed(archived_trade)
        print(
            f"[Monitor] Archived duplicate/non-active record "
            f"{archived_trade.get('trade_id')} for "
            f"{str(archived_trade.get('coin', 'UNKNOWN')).upper()} "
            f"with status {archived_trade.get('status')}"
        )

    if not trades:
        save_active([])
        print("No active trades remain after state cleanup.")
        return

    # Expire records the monitor can no longer resolve. A trade whose symbol
    # stopped responding, was delisted, or simply predates the scan window keeps
    # an old ``last_checked_at`` forever; leaving it ACTIVE blocked that coin
    # from ever being signalled again and made every state file drift.
    ttl_hours = trade_state.active_trade_ttl_hours()
    trades, expired = trade_state.split_expired(trades, ttl_hours=ttl_hours, now=cleanup_time)
    for stale_trade in expired:
        stale_trade["status"] = "EXPIRED"
        stale_trade["closed_at"] = _iso_utc(cleanup_time)
        stale_trade["expiry_reason"] = (
            f"No terminal event recorded for more than {ttl_hours:.0f}h"
        )
        save_closed(stale_trade)
        print(
            f"[Monitor] Expired stale ACTIVE record {stale_trade.get('trade_id')} for "
            f"{str(stale_trade.get('coin', 'UNKNOWN')).upper()}; "
            f"{stale_trade['expiry_reason']}"
        )

    if expired and not trades:
        save_active([])
        print("No active trades remain after expiry.")
        return

    monitor_now = _utc_now()
    budget = _load_monitor_budget(monitor_now)
    budget_changed = False

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

                # Verified outcome honesty: the loss gets a post too, budget
                # capped so the feed is not flooded on a red day. The closure
                # above must survive even when this post cannot be published.
                if _sl_posts_enabled() and _budget_allows(budget, "sl"):
                    try:
                        _publish_stop(publish_func, trade, terminal)
                        budget["sl_posts"] = int(budget.get("sl_posts", 0)) + 1
                        budget_changed = True
                        print(
                            f"[Monitor] SL resolution post published for {coin} "
                            f"({budget['sl_posts']}/{_daily_cap('sl')} today)"
                        )
                    except Exception as exc:
                        print(f"[Monitor Warning] SL post for {coin} failed after closure: {exc}")
                else:
                    print(
                        f"[Monitor] SL detected for {coin} at {_iso_utc(terminal.happened_at)}; "
                        "closed (SL post disabled or daily cap reached)"
                    )
                continue

            if new_tp1 and not trade.get("tp1_hit"):
                card_path = _publish_target_hit(publish_func, trade, new_tp1)
                _record_tp1(trade, new_tp1, card_path)
                print(
                    f"[Monitor] TP1 detected for {coin} at "
                    f"{_iso_utc(new_tp1.happened_at)}; image published"
                )

            # Mid-trade journal: one 'under pressure' update per open trade
            # when price drifts close to invalidation without touching it.
            if (
                not trade.get("pressure_posted")
                and _pressure_posts_enabled()
                and _budget_allows(budget, "pressure")
                and _near_invalidation(trade, current_price)
            ):
                try:
                    _publish_pressure(publish_func, trade, current_price)
                    trade["pressure_posted"] = True
                    trade["pressure_posted_at"] = _iso_utc(scan_end)
                    budget["pressure_posts"] = int(budget.get("pressure_posts", 0)) + 1
                    budget_changed = True
                    print(
                        f"[Monitor] Under-pressure update published for {coin} "
                        f"({budget['pressure_posts']}/{_daily_cap('pressure')} today)"
                    )
                except Exception as exc:
                    print(f"[Monitor Warning] Pressure post for {coin} failed: {exc}")

            trade["last_checked_at"] = _iso_utc(scan_end)
            remaining.append(trade)

        except Exception as exc:
            # Keep the previous last_checked_at on any API/image/publish error.
            # The next run will rescan the same range and cannot silently miss it.
            print(f"[Monitor Error] {coin}: {exc}")
            remaining.append(trade)

    save_active(remaining)
    if budget_changed:
        _save_monitor_budget(budget)
