"""Shared lifecycle helpers for ``data/active_trades.json`` / ``data/posted_coins.json``.

Both the signal engine and the live monitor read these files, so the rules for
"is this trade still open?" and "is this coin still in cooldown?" live here
once instead of drifting apart in two places.

The monitor only scans a bounded lookback window, so an ``ACTIVE`` record that
is never touched again (a coin delisted from the scan window, a run that kept
erroring) used to sit in ``active_trades.json`` forever. That silently blocked
the coin from ever being signalled again and polluted every follow-up report.
:func:`split_expired` gives those records a TTL.

Standard library only, so it can be imported by the state CLI and by tests
without pulling in matplotlib/pandas.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
ACTIVE_TRADES_FILE = ROOT_DIR / "data" / "active_trades.json"
POSTED_COINS_FILE = ROOT_DIR / "data" / "posted_coins.json"
CONFIG_FILE = ROOT_DIR / "bot" / "config.json"

DEFAULT_ACTIVE_TRADE_TTL_HOURS = 72.0
TERMINAL_STATUSES = {"TP1_HIT", "TP2_HIT", "SL_HIT", "CLOSED", "SUPERSEDED", "EXPIRED"}


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_iso(value: Any) -> datetime.datetime | None:
    """Parse an ISO-8601 timestamp, assuming UTC when no offset is present."""

    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def load_config() -> dict[str, Any]:
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return config if isinstance(config, dict) else {}


def active_trade_ttl_hours(config: dict[str, Any] | None = None) -> float:
    """Hours after which an untouched ``ACTIVE`` trade is treated as stale."""

    raw = os.getenv("ACTIVE_TRADE_TTL_HOURS", "")
    if not raw.strip():
        settings = config if config is not None else load_config()
        raw = str(settings.get("trading", {}).get("active_trade_ttl_hours", DEFAULT_ACTIVE_TRADE_TTL_HOURS))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_ACTIVE_TRADE_TTL_HOURS
    # 0 or less disables expiry, which is the old (blocking) behaviour.
    return value if value > 0 else 0.0


def is_active(trade: dict[str, Any]) -> bool:
    return str(trade.get("status", "ACTIVE")).upper().strip() == "ACTIVE"


def trade_age_hours(trade: dict[str, Any], *, now: datetime.datetime | None = None) -> float | None:
    """Hours since the trade was created, or since it was last checked."""

    reference = parse_iso(trade.get("last_checked_at")) or parse_iso(trade.get("created_at"))
    if reference is None:
        return None
    return ((now or utc_now()) - reference).total_seconds() / 3600.0


def split_expired(
    trades: list[dict[str, Any]],
    *,
    ttl_hours: float | None = None,
    now: datetime.datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split open trades into ``(live, expired)`` using the TTL.

    Records that are not ``ACTIVE`` are returned as live: the monitor owns their
    archival, and this helper must not silently change that decision.
    """

    limit = active_trade_ttl_hours() if ttl_hours is None else float(ttl_hours)
    live: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []

    for trade in trades:
        if limit <= 0 or str(trade.get("status", "ACTIVE")).upper().strip() != "ACTIVE":
            live.append(trade)
            continue
        age = trade_age_hours(trade, now=now)
        if age is not None and age > limit:
            expired.append(trade)
        else:
            live.append(trade)

    return live, expired


def expired_coins(
    trades: list[dict[str, Any]],
    *,
    ttl_hours: float | None = None,
    now: datetime.datetime | None = None,
) -> list[str]:
    _, expired = split_expired(trades, ttl_hours=ttl_hours, now=now)
    return sorted({str(item.get("coin", "")).upper().strip() for item in expired if item.get("coin")})


def in_cooldown(
    posted_entries: list[dict[str, Any]],
    coin: str,
    *,
    hours: float,
    now: datetime.datetime | None = None,
) -> bool:
    """Return True when ``coin`` was posted inside the duplicate filter window."""

    if hours <= 0:
        return False

    cutoff = (now or utc_now()) - datetime.timedelta(hours=float(hours))
    target = str(coin).upper().strip()

    for entry in posted_entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("coin", "")).upper().strip() != target:
            continue
        posted_at = parse_iso(entry.get("time"))
        if posted_at is not None and posted_at > cutoff:
            return True

    return False


def read_json_list(path: Path) -> list[dict[str, Any]]:
    """Read a JSON array of objects, ignoring malformed or non-list files."""

    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
