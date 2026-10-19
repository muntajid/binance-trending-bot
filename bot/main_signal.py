"""Master multi-mode signal, macro overview, and community content engine.

Executes the 12-slot daily portfolio schedule defined in ``bot/config.json``
(benchmarked against top 100 creator data):

- 5x Rapid Alerts (verified trade levels plus a concise institutional read)
- 5x Explained Master Setups (15-point evidence, Why Now + Invalidation)
- 1x Macro & Bitcoin Market Overview (BTC structure, flows & market phases)
- 1x Viral Trader Psychology & Community Meme (+49% paired uplift)

Which of those four shapes a run takes is decided by :mod:`bot.schedule`: the
next *pending* slot of the current UTC day. It is deliberately not derived from
the clock any more - a manual "Run workflow" click must advance the rotation
instead of republishing whichever slot the hour happened to map to, and a slot
whose run failed goes back to the front of the queue rather than being lost.
"""

from __future__ import annotations

import datetime
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from bot import schedule, trade_state
from bot.chart_generator import generate_btc_market_chart, generate_single_chart
from bot.market_data import MarketDataError
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
CHART_DIR = "charts"


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


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


def load_config() -> dict[str, Any]:
    """Read ``bot/config.json`` through the shared helper, so the engine, the
    queue and the monitor all agree on the plan and the filters."""

    return trade_state.load_config()


def get_active_coins(*, include_stale: bool = False) -> set[str]:
    """Return coins that already have an open model setup.

    Records older than the active-trade TTL are ignored by default: the monitor
    only scans a bounded lookback window, so a stale ``ACTIVE`` entry would
    otherwise block its coin from ever being signalled again.
    """

    trades = _read_json_list(ACTIVE_FILE)
    if include_stale:
        live = trades
    else:
        live, expired = trade_state.split_expired(trades, now=_utc_now())
        if expired:
            names = ", ".join(
                str(item.get("coin", "?")).upper() for item in expired if item.get("coin")
            )
            print(
                f"[Active Trades] Ignoring {len(expired)} stale ACTIVE record(s) past the "
                f"{trade_state.active_trade_ttl_hours():.0f}h TTL: {names}"
            )

    return {
        str(trade.get("coin", "")).upper().strip()
        for trade in live
        if str(trade.get("status", "ACTIVE")).upper().strip() == "ACTIVE"
    }


def can_post(coin: str, hours: int | float | None = None) -> bool:
    """Return False when the same coin was signalled inside the cooldown."""

    if hours is None:
        hours = float(load_config().get("duplicate_filter_hours", 24))

    return not trade_state.in_cooldown(
        _read_json_list(POSTED_FILE), coin, hours=float(hours), now=_utc_now()
    )


def record_posted(coin: str) -> str:
    """Persist the signal cooldown only after all generation checks pass.

    Returns the timestamp that was written, which is what lets a failed publish
    remove exactly this entry again.
    """

    posted_time = _utc_now().isoformat()
    posted = _read_json_list(POSTED_FILE)
    posted.append(
        {
            "coin": str(coin).upper().strip(),
            "time": posted_time,
        }
    )
    _atomic_write_json(POSTED_FILE, posted[-100:])
    return posted_time


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


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def _change_thresholds(config: dict[str, Any]) -> list[float]:
    """Strict momentum filter first, then the configured relaxations."""

    trending = config.get("trending", {})
    wanted = [trending.get("min_change_percent", 5.0), *trending.get("min_change_fallbacks", [])]
    thresholds: list[float] = []
    for value in wanted:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number >= 0 and number not in thresholds:
            thresholds.append(number)
    return thresholds or [0.5]


def select_trending_coin(
    config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], float]:
    """Pick the strongest trending coin that is neither open nor in cooldown.

    The thresholds are relaxed in turn (``trending.min_change_fallbacks``) so a
    quiet market still yields a verified setup instead of a dead slot.
    """

    settings = config if config is not None else load_config()
    trending_config = settings.get("trending", {})
    try:
        top_n = int(trending_config.get("top_n", 30))
        min_volume = float(trending_config.get("min_volume_usdt", 5_000_000))
    except (TypeError, ValueError):
        top_n, min_volume = 30, 5_000_000

    active_coins = get_active_coins()
    cooldown_hours = float(settings.get("duplicate_filter_hours", 24))
    thresholds = _change_thresholds(settings)
    strict_threshold = thresholds[0]
    errors: list[str] = []
    blocked_active = blocked_cooldown = 0

    for threshold in thresholds:
        try:
            trending = get_trending_coins(
                top_n=top_n, min_change=threshold, min_volume=min_volume
            )
        except (RuntimeError, MarketDataError) as exc:
            errors.append(f">={threshold}%: {exc}")
            continue

        for coin_data in trending:
            candidate = str(coin_data["coin"]).upper().strip()
            if candidate in active_coins:
                blocked_active += 1
                continue
            if trade_state.in_cooldown(
                _read_json_list(POSTED_FILE), candidate, hours=cooldown_hours, now=_utc_now()
            ):
                blocked_cooldown += 1
                continue
            if threshold != strict_threshold:
                print(
                    f"[Trending] Nothing passed the strict filter; relaxing the momentum "
                    f"threshold to >={threshold}%"
                )
            return coin_data, trending, threshold

        errors.append(f">={threshold}%: all {len(trending)} candidates already open or in cooldown")

    raise RuntimeError(
        "No postable trending coin found. "
        + "; ".join(errors)
        + f" | blocked: {blocked_active} active, {blocked_cooldown} in cooldown"
    )


# ---------------------------------------------------------------------------
# Content builders
# ---------------------------------------------------------------------------

def _with_duplicate_guard(
    builder: Callable[[dict[str, Any]], tuple[str, dict[str, Any], str]],
    config: dict[str, Any],
) -> tuple[str, dict[str, Any], str]:
    """Regenerate while the caption is a near-duplicate of a recent post.

    ``check_and_record_similarity`` used to be called for its side effect only
    and its verdict thrown away, so a flagged caption still went out. Duplicated
    wording costs reach on Binance Square, so a hit now costs a regeneration
    instead of a bad post.
    """

    try:
        attempts = max(1, int(config.get("caption_regeneration_attempts", 3)))
    except (TypeError, ValueError):
        attempts = 3
    try:
        max_similarity = float(config.get("caption_similarity_max", 0.82))
    except (TypeError, ValueError):
        max_similarity = 0.82

    result = builder(config)
    for attempt in range(1, attempts + 1):
        if check_and_record_similarity(result[0], max_similarity=max_similarity):
            return result
        if attempt < attempts:
            print(
                f"[Duplicate Guard] Variant {attempt}/{attempts} is too similar to a recent "
                "post; regenerating"
            )
            result = builder(config)

    print("[Duplicate Guard] Accepting the last variant to avoid losing the slot")
    return result


def build_market_overview(config: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    """Return ``(post_text, payload, chart_path)`` for the macro overview slot."""

    print("[Engine] Generating Macro & Bitcoin Market Overview...")
    trending: list[dict[str, Any]] = []
    try:
        trending = get_trending_coins(top_n=10, min_change=2.0, min_volume=3_000_000)
    except (RuntimeError, MarketDataError) as exc:
        print(f"[Warning] Could not get trending list for macro context: {exc}")

    overview_data = generate_market_overview_with_groq(trending)
    post = format_market_overview_post(overview_data)

    chart_path = generate_btc_market_chart(os.path.join(CHART_DIR, "BTC_1H.png"))
    if not validate_chart(chart_path):
        raise RuntimeError(f"BTC Chart generation failed: {chart_path}")

    return post, overview_data, chart_path


def build_meme_post(config: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    """Return ``(post_text, payload, card_path)`` for the community meme slot."""

    print("[Engine] Generating Viral Trader Psychology / Community Meme...")
    meme_data = generate_meme_post_with_groq()
    post = format_meme_community_post(meme_data)

    card_path = generate_meme_card(
        title=meme_data.get("title", "Crypto Trader Psychology 101"),
        content=meme_data.get(
            "content", "Me: 'Strict risk management today.'\nAlso me: 'Market order 50x!'"
        ),
        lesson=meme_data.get(
            "lesson", "Discipline is what separates traders from gamblers."
        ),
        save_path=os.path.join(CHART_DIR, "MEME_CARD.png"),
    )
    if not validate_chart(card_path):
        raise RuntimeError(f"Meme Card generation failed: {card_path}")

    return post, meme_data, card_path


def build_signal_post(
    mode: str, config: dict[str, Any]
) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
    """Return ``(post_text, payload, chart_path, effects)`` for a coin signal."""

    selected, trending, threshold = select_trending_coin(config)
    coin = str(selected["coin"]).upper().strip()
    change = float(selected["change"])
    live_price = get_live_price(coin)

    trending_bonus = "BTC"
    for coin_data in trending:
        candidate = str(coin_data["coin"]).upper().strip()
        if candidate != coin:
            trending_bonus = candidate
            break

    print(
        f"[Selected] {coin} {change:+.2f}% @ ${live_price} "
        f"| momentum filter >={threshold}% | Context: {trending_bonus}"
    )

    signal = generate_signal_with_groq(
        coin=coin,
        price=live_price,
        change=change,
        trending_bonus=trending_bonus,
    )
    signal["coin"] = coin
    signal["entry_price"] = live_price
    signal["change"] = change
    signal["slot_mode"] = mode

    print(f"[Chart] Generating single 1H chart for {coin}...")
    chart_path = generate_single_chart(coin, signal, out_dir=CHART_DIR)
    if not validate_chart(chart_path):
        raise RuntimeError(f"Chart generation failed or invalid: {chart_path}")

    post = format_rapid_alert_post(signal) if mode == "rapid_alert" else format_explained_post(signal)

    saved_trade = save_active(signal)
    posted_time = record_posted(coin)

    effects = {
        "coin": coin,
        "trade_id": saved_trade["trade_id"],
        "posted_time": posted_time,
    }
    print(
        f"[Done] {mode.upper()} post and 1H chart ready for {coin} "
        f"(Trade ID: {saved_trade['trade_id']})"
    )
    return post, signal, chart_path, effects


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _write_step_summary(lines: list[str]) -> None:
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return
    try:
        with open(summary_file, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        print(f"[Warning] Could not write the step summary: {exc}")


def run() -> None:
    config = load_config()
    now = _utc_now()
    print(f"[Signal Engine] Starting run at {now.isoformat()}")

    os.makedirs("data", exist_ok=True)
    os.makedirs(CHART_DIR, exist_ok=True)

    try:
        slot = schedule.select_slot(
            os.getenv("SLOT"),
            reset=_env_flag("SLOT_RESET"),
        )
    except schedule.ScheduleError as exc:
        raise SystemExit(f"[Schedule] {exc}") from None

    schedule.begin_run(slot)
    mode = str(slot["type"])
    print(
        f"[Slot Engine] Slot #{slot['slot']}: {slot['name']} (Mode: {mode}) - "
        f"rotation {slot['rotation']} of day {slot['day_utc']}, "
        f"{slot['published_before']}/{slot['total_slots']} already published"
    )
    if slot.get("forced"):
        print(
            f"[Slot Engine] Slot #{slot['slot']} was requested explicitly and is not "
            "pending today; the queue is left untouched"
        )
    if slot.get("wrapped"):
        print("[Slot Engine] The day's queue was complete, so the rotation wrapped around")

    try:
        if mode == "market_overview":
            post, payload, chart_path = _with_duplicate_guard(build_market_overview, config)
        elif mode == "meme_community":
            post, payload, chart_path = _with_duplicate_guard(build_meme_post, config)
        else:
            post, payload, chart_path, effects = build_signal_post(mode, config)
            # Remember what this run mutated: a failed publish undoes exactly
            # these entries so the retried slot starts from a clean slate.
            schedule.record_run_effects(**effects)

        Path(LATEST_POST_FILE).write_text(post, encoding="utf-8")
        _atomic_write_json(LATEST_SIGNAL_FILE, payload)

    except BaseException as exc:  # noqa: BLE001 - re-raised after cleanup
        reason = f"{type(exc).__name__}: {exc}"
        schedule.abort_run(reason)
        _write_step_summary([f"### ❌ Slot #{slot['slot']} failed", f"- {reason}", "- Slot returned to the queue."])
        raise

    preview_only = _env_flag("PREVIEW_ONLY")
    if preview_only:
        # Nothing reached Square, so the slot and its side effects must be undone.
        schedule.abort_run("preview-only run (no post was published)")
        print("[Preview] Post generated but not published; the slot is back in the queue.")

    print(f"[Done] {mode} content generated. Chart: {chart_path}")
    print(post)
    _write_step_summary(
        [
            f"### 📝 Slot #{slot['slot']} · `{mode}`",
            f"- Coin: `{payload.get('coin', 'n/a')}`" if payload.get("coin") else "- Coin: n/a",
            f"- Chart: `{Path(chart_path).name}`",
            f"- Published: {'no (preview)' if preview_only else 'awaiting Square step'}",
        ]
    )


if __name__ == "__main__":
    run()
