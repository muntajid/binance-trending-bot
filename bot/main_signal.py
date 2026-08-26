"""Master multi-mode signal, macro overview, and community content engine.

Executes an 8-slot daily portfolio schedule based on top 100 creator benchmark data:
- 3x Rapid Alerts (120-180 chars, high-views fast alerts)
- 3x Explained Master Setups (Why Now + Invalidation + structured SMC)
- 1x Macro & Bitcoin Market Overview (BTC structure, flows & market phases)
- 1x Viral Trader Psychology & Community Meme (+49% paired uplift)
"""

from __future__ import annotations

import datetime
import json
import os
import uuid
from pathlib import Path
from typing import Any

from bot.chart_generator import generate_btc_market_chart, generate_single_chart
from bot.meme_card_generator import generate_meme_card
from bot.poster import (
    check_and_record_similarity,
    format_explained_post,
    format_market_overview_post,
    format_meme_community_post,
    format_rapid_alert_post,
)
from bot.signal_generator import (
    generate_market_overview_with_groq,
    generate_meme_post_with_groq,
    generate_signal_with_groq,
    get_live_price,
    get_trending_coins,
)

POSTED_FILE = "data/posted_coins.json"
ACTIVE_FILE = "data/active_trades.json"
LATEST_POST_FILE = "data/latest_post.txt"
LATEST_SIGNAL_FILE = "data/latest_signal.json"
SLOT_STATE_FILE = "data/slot_state.json"
CONFIG_FILE = "bot/config.json"
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


def _atomic_write_json(path: str, value: Any) -> None:
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
                f"Duplicate active signal blocked for {coin}; existing setup must close first."
            )

    created_at = _utc_now().isoformat()
    saved_trade = {
        **signal,
        "coin": coin,
        "trade_id": str(signal.get("trade_id") or uuid.uuid4().hex),
        "created_at": created_at,
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


def _determine_current_slot() -> dict[str, Any]:
    """Determine the current schedule slot based on UTC hour."""
    hour = _utc_now().hour
    slot_index = (hour // 3) % 8

    slots = [
        {"slot": 0, "type": "market_overview", "name": "Macro & Bitcoin Outlook (00:00 UTC)"},
        {"slot": 1, "type": "rapid_alert", "name": "Rapid Signal Alert #1 (03:00 UTC)"},
        {"slot": 2, "type": "explained_setup", "name": "Explained Master Setup #1 (06:00 UTC)"},
        {"slot": 3, "type": "rapid_alert", "name": "Rapid Signal Alert #2 (09:00 UTC)"},
        {"slot": 4, "type": "meme_community", "name": "Trader Psychology & Community Meme (12:00 UTC)"},
        {"slot": 5, "type": "explained_setup", "name": "Explained Master Setup #2 (15:00 UTC)"},
        {"slot": 6, "type": "rapid_alert", "name": "Rapid Signal Alert #3 (18:00 UTC)"},
        {"slot": 7, "type": "explained_setup", "name": "Explained Master Setup #3 (21:00 UTC)"},
    ]

    selected = slots[slot_index]

    state = {
        "last_slot": slot_index,
        "last_type": selected["type"],
        "last_run_utc": _utc_now().isoformat(),
    }
    _atomic_write_json(SLOT_STATE_FILE, state)

    return selected


def run() -> None:
    now = _utc_now()
    print(f"[Signal Engine] Starting run at {now.isoformat()}")

    os.makedirs("data", exist_ok=True)
    os.makedirs(CHART_DIR, exist_ok=True)

    # 1. Determine slot mode
    slot_info = _determine_current_slot()
    mode = slot_info["type"]
    print(f"[Slot Engine] Active Slot #{slot_info['slot']}: {slot_info['name']} (Mode: {mode})")

    # -----------------------------------------------------------------------
    # PATH A: Macro & Bitcoin Market Overview (Slot 0)
    # -----------------------------------------------------------------------
    if mode == "market_overview":
        print("[Engine] Generating Macro & Bitcoin Market Overview...")
        trending = []
        try:
            trending = get_trending_coins(top_n=10, min_change=2.0, min_volume=3_000_000)
        except Exception as e:
            print(f"[Warning] Could not get trending list for macro context: {e}")

        overview_data = generate_market_overview_with_groq(trending)
        post = format_market_overview_post(overview_data)

        # Generate single 1H BTC chart
        chart_path = generate_btc_market_chart(os.path.join(CHART_DIR, "BTC_1H.png"))
        if not validate_chart(chart_path):
            raise RuntimeError(f"BTC Chart generation failed: {chart_path}")

        Path(LATEST_POST_FILE).write_text(post, encoding="utf-8")
        _atomic_write_json(LATEST_SIGNAL_FILE, overview_data)
        check_and_record_similarity(post)

        print("[Done] Market Overview post and BTC chart generated successfully:")
        print(post)
        return

    # -----------------------------------------------------------------------
    # PATH B: Viral Trader Meme / Community Culture Post (Slot 4)
    # -----------------------------------------------------------------------
    if mode == "meme_community":
        print("[Engine] Generating Viral Trader Psychology / Community Meme...")
        meme_data = generate_meme_post_with_groq()
        post = format_meme_community_post(meme_data)

        # Generate sleek dark-mode Meme Card visual
        card_path = generate_meme_card(
            title=meme_data.get("title", "Crypto Trader Psychology 101"),
            content=meme_data.get("content", "Me: 'Strict risk management today.'\nAlso me: 'Market order 50x!'"),
            lesson=meme_data.get("lesson", "Discipline is what separates traders from gamblers."),
            save_path=os.path.join(CHART_DIR, "MEME_CARD.png"),
        )
        if not validate_chart(card_path):
            raise RuntimeError(f"Meme Card generation failed: {card_path}")

        Path(LATEST_POST_FILE).write_text(post, encoding="utf-8")
        _atomic_write_json(LATEST_SIGNAL_FILE, meme_data)
        check_and_record_similarity(post)

        print("[Done] Meme/Community post and visual generated successfully:")
        print(post)
        return

    # -----------------------------------------------------------------------
    # PATH C: Single-Coin Signals (Rapid Alert 3x / Explained Setup 3x)
    # -----------------------------------------------------------------------
    trending = get_trending_coins(top_n=30, min_change=5.0, min_volume=5_000_000)
    if not trending:
        raise RuntimeError("No valid trending coins returned by Binance.")

    active_coins = get_active_coins()
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
            "All eligible trending coins are active or recently posted. No duplicate forced."
        )

    coin = str(selected["coin"]).upper().strip()
    change = float(selected["change"])
    live_price = get_live_price(coin)

    trending_bonus = "BTC"
    for coin_data in trending:
        candidate = str(coin_data["coin"]).upper().strip()
        if candidate != coin:
            trending_bonus = candidate
            break

    print(f"[Selected] {coin} {change:+.2f}% @ ${live_price} | Context: {trending_bonus}")

    # Generate validated signal with Why Now and Invalidation
    signal = generate_signal_with_groq(
        coin=coin,
        price=live_price,
        change=change,
        trending_bonus=trending_bonus,
    )
    signal["coin"] = coin
    signal["entry_price"] = live_price
    signal["change"] = change

    # Generate single 1H chart (Evidence benchmark: single chart performs best)
    print(f"[Chart] Generating single 1H chart for {coin}...")
    chart_path = generate_single_chart(coin, signal, out_dir=CHART_DIR)
    if not validate_chart(chart_path):
        raise RuntimeError(f"Chart generation failed or invalid: {chart_path}")

    # Format post text according to slot mode
    if mode == "rapid_alert":
        post = format_rapid_alert_post(signal)
    else:
        post = format_explained_post(signal)

    # Save outputs
    Path(LATEST_POST_FILE).write_text(post, encoding="utf-8")
    _atomic_write_json(LATEST_SIGNAL_FILE, signal)
    check_and_record_similarity(post)

    saved_trade = save_active(signal)
    record_posted(coin)

    print(f"[Done] {mode.upper()} post and 1H chart ready for {coin} (Trade ID: {saved_trade['trade_id']})")
    print(post)


if __name__ == "__main__":
    run()
