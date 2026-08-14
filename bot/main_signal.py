"""Generate one verified signal while preventing overlapping coin trades."""

from __future__ import annotations

import datetime
import json
import os
import uuid
from pathlib import Path
from typing import Any

from bot.chart_generator import generate_both_charts
from bot.poster import format_signal_post
from bot.signal_generator import (
    generate_signal_with_groq,
    get_live_price,
    get_trending_coins,
)

POSTED_FILE = "data/posted_coins.json"
ACTIVE_FILE = "data/active_trades.json"
LATEST_POST_FILE = "data/latest_post.txt"
LATEST_SIGNAL_FILE = "data/latest_signal.json"
CHART_DIR = "charts"


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_datetime_utc(value: Any) -> datetime.datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.datetime.fromisoformat(
            str(value).strip().replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None

    # Historical repository records were created by UTC GitHub runners but
    # some of them were saved without an explicit timezone suffix.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)

    return parsed.astimezone(datetime.timezone.utc)


def _read_json_list(path: str, *, missing_ok: bool = True) -> list[dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        if missing_ok:
            return []
        raise FileNotFoundError(path)

    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read valid JSON list from {path}: {exc}") from exc

    if not isinstance(value, list):
        raise RuntimeError(f"Expected a JSON list in {path}")

    return [item for item in value if isinstance(item, dict)]


def _atomic_write_json(path: str, value: list[dict[str, Any]]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = file_path.with_suffix(file_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, file_path)


def get_active_coins() -> set[str]:
    """Return coins that already have an open model setup."""

    active_coins: set[str] = set()
    for trade in _read_json_list(ACTIVE_FILE):
        status = str(trade.get("status", "ACTIVE")).upper().strip()
        coin = str(trade.get("coin", "")).upper().strip()
        if coin and status == "ACTIVE":
            active_coins.add(coin)
    return active_coins


def can_post(coin: str, hours: int = 24) -> bool:
    """Return False when the same coin was signalled inside the cooldown."""

    cutoff = _utc_now() - datetime.timedelta(hours=hours)
    normalized_coin = str(coin).upper().strip()

    for entry in _read_json_list(POSTED_FILE):
        if str(entry.get("coin", "")).upper().strip() != normalized_coin:
            continue

        posted_time = _parse_datetime_utc(entry.get("time"))
        if posted_time is not None and posted_time > cutoff:
            return False

    return True


def record_posted(coin: str) -> None:
    """Persist the signal cooldown only after all generation checks pass."""

    posted = _read_json_list(POSTED_FILE)
    posted.append(
        {
            "coin": str(coin).upper().strip(),
            "time": _utc_now().isoformat(),
        }
    )
    _atomic_write_json(POSTED_FILE, posted[-100:])


def save_active(signal: dict[str, Any]) -> dict[str, Any]:
    """Save a signal with a stable ID and reject an overlapping same-coin trade."""

    trades = _read_json_list(ACTIVE_FILE)
    coin = str(signal.get("coin", "")).upper().strip()
    if not coin:
        raise ValueError("Signal coin cannot be empty")

    for existing in trades:
        existing_coin = str(existing.get("coin", "")).upper().strip()
        existing_status = str(existing.get("status", "ACTIVE")).upper().strip()
        if existing_coin == coin and existing_status == "ACTIVE":
            raise RuntimeError(
                f"Duplicate active signal blocked for {coin}; "
                "the existing setup must close before another is created"
            )

    created_at = _utc_now().isoformat()
    saved_trade = {
        **signal,
        "coin": coin,
        "trade_id": str(signal.get("trade_id") or uuid.uuid4().hex),
        "created_at": created_at,
        # Historical monitor scans begin at signal creation. This ensures a
        # TP/SL touch is not lost if price retraces before the next run.
        "last_checked_at": created_at,
        "status": "ACTIVE",
    }
    trades.append(saved_trade)
    _atomic_write_json(ACTIVE_FILE, trades)
    return saved_trade


def validate_chart(path: str) -> bool:
    """Confirm that a generated chart exists and is not empty or broken."""

    if not path or not os.path.isfile(path):
        return False

    try:
        return os.path.getsize(path) > 1000
    except OSError:
        return False


def run() -> None:
    print(f"[Signal] Starting at {_utc_now().isoformat()}")

    os.makedirs("data", exist_ok=True)
    os.makedirs(CHART_DIR, exist_ok=True)

    # 1. Get verified Binance trending data.
    trending = get_trending_coins(
        top_n=30,
        min_change=5.0,
        min_volume=5_000_000,
    )
    if not trending:
        raise RuntimeError("No valid trending coins returned by Binance.")

    print(
        "[Trending] Top 5:",
        [
            (
                item["coin"],
                round(float(item["change"]), 2),
                item["price"],
            )
            for item in trending[:5]
        ],
    )

    # 2. Select a coin that is neither inside the 24-hour signal cooldown nor
    # already represented by an active model setup.
    active_coins = get_active_coins()
    if active_coins:
        print("[Signal] Coins skipped because a setup is still active:", sorted(active_coins))

    selected: dict[str, Any] | None = None
    for coin_data in trending:
        candidate = str(coin_data["coin"]).upper().strip()
        if candidate in active_coins:
            continue
        if can_post(candidate, 24):
            selected = coin_data
            break

    if selected is None:
        raise RuntimeError(
            "All eligible trending coins are either active or were posted "
            "within the last 24 hours. No duplicate signal will be forced."
        )

    coin = str(selected["coin"]).upper().strip()
    change = float(selected["change"])

    # 3. Refresh the authoritative live Binance price.
    live_price = get_live_price(coin)
    scanner_price = float(selected["price"])
    print(f"[Price] {coin}: scanner=${scanner_price} live=${live_price}")

    # 4. Choose a different trending coin as context.
    trending_bonus = "BTC"
    for coin_data in trending:
        candidate = str(coin_data["coin"]).upper().strip()
        if candidate != coin:
            trending_bonus = candidate
            break

    print(
        f"[Selected] {coin} {change:+.2f}% @ ${live_price} "
        f"| Context {trending_bonus}"
    )

    # 5. Generate a signal from the verified price.
    signal = generate_signal_with_groq(
        coin=coin,
        price=live_price,
        change=change,
        trending_bonus=trending_bonus,
    )
    if not isinstance(signal, dict):
        raise RuntimeError("Signal generator returned invalid data.")

    signal_coin = str(signal.get("coin", "")).upper().strip()
    if signal_coin != coin:
        raise RuntimeError(f"Coin mismatch: expected {coin}, got {signal_coin}")

    # Enforce authoritative values regardless of model output.
    signal["coin"] = coin
    signal["entry_price"] = live_price
    signal["change"] = change

    print("[Signal] Validated:", json.dumps(signal, indent=2))

    # 6. Keep the current two-chart generation unchanged for this repair.
    print("[Chart] Generating charts...")
    try:
        chart_1h, chart_4h = generate_both_charts(
            coin,
            signal,
            out_dir=CHART_DIR,
        )
    except Exception as exc:
        raise RuntimeError(f"Chart generation failed: {exc}") from exc

    charts = [chart_1h, chart_4h]
    invalid_charts = [path for path in charts if not validate_chart(path)]
    if invalid_charts:
        raise RuntimeError(
            "Chart generation completed, but one or more chart files are "
            f"missing/invalid: {invalid_charts}"
        )
    print("[Chart] Verified:", charts)

    # 7. Build validated Unicode post text. format_signal_post blocks mojibake.
    post = format_signal_post(signal)

    # 8. Save outputs only after all market, chart, and text validation passes.
    Path(LATEST_POST_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(LATEST_POST_FILE).write_text(post, encoding="utf-8")
    Path(LATEST_SIGNAL_FILE).write_text(
        json.dumps(signal, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("[Post] Text generated successfully.")
    print(post)

    # A second same-coin guard runs inside save_active to protect against stale
    # selection state or future code changes.
    saved_trade = save_active(signal)
    record_posted(coin)

    print(
        f"[Done] {coin} signal and charts are ready. "
        f"Trade ID: {saved_trade['trade_id']}"
    )


if __name__ == "__main__":
    run()
