"""Verified Binance market scanning and resilient Groq signal, macro overview, and meme generation."""

from __future__ import annotations

import json
import math
import os
from typing import Any

import requests
from groq import Groq

from bot import trade_state
from bot.indicators import calculate_all_indicators
from bot.market_context import get_market_context
from bot.market_data import (
    calculate_volume_analysis,
    get_24h_data,
    get_klines_multi_timeframe,
    get_orderbook,
    get_ticker_24h_all,
)
from bot.content_quality import (
    SignalContentError,
    clip_sentence,
    sanitize_hook,
    validate_reason,
    word_count as _word_count,
)
from bot.futures_data import (
    get_funding_rate,
    get_futures_vs_spot_volume,
    get_liquidation_estimate,
    get_liquidation_heatmap_levels,
    get_long_short_ratio,
    get_open_interest,
)

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
DEFAULT_GROQ_MODELS = (
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
)


class SignalQualityError(ValueError):
    """A candidate failed a deterministic safety or setup-quality gate."""


_SIGNAL_REGENERATION_ATTEMPTS = 2
_MIN_DIRECTION_VOTES = 3


def get_live_price(coin: str) -> float:
    """Get the authoritative live price from the verified market-data layer."""

    symbol = f"{str(coin).upper()}USDT"
    data = get_24h_data(symbol)

    if not data.get("verified"):
        raise RuntimeError(f"Market data not verified for {symbol}")

    price = float(data["price"])
    if not math.isfinite(price) or price <= 0:
        raise RuntimeError(f"Invalid verified price for {symbol}: {price}")

    print(f"[Live Price - VERIFIED] {symbol} = ${price}")
    return price


def get_trending_coins(
    top_n: int = 30,
    min_change: float = 5.0,
    min_volume: float = 5_000_000,
    exclude_coins: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return verified USDT movers, excluding configured non-crypto markets."""

    if exclude_coins is None:
        try:
            trending_config = trade_state.load_config().get("trending", {})
            exclude_coins = trending_config.get("exclude_coins", [])
        except Exception:
            exclude_coins = []
    excluded_bases = {
        str(value).upper().strip().removesuffix("USDT")
        for value in (exclude_coins or [])
        if str(value).strip()
    }

    data = get_ticker_24h_all()

    if not isinstance(data, list):
        raise RuntimeError("Invalid ticker list from Binance vision API.")

    excluded_symbols = {
        "USDCUSDT",
        "FDUSDUSDT",
        "TUSDUSDT",
        "DAIUSDT",
        "BUSDUSDT",
        "USDPUSDT",
    }
    leveraged_suffixes = (
        "UPUSDT",
        "DOWNUSDT",
        "BEARUSDT",
        "BULLUSDT",
    )
    filtered: list[dict[str, Any]] = []

    for item in data:
        try:
            symbol = str(item["symbol"]).upper()
            if not symbol.endswith("USDT"):
                continue
            if symbol in excluded_symbols or symbol[:-4] in excluded_bases:
                continue
            if any(marker in symbol for marker in leveraged_suffixes):
                continue

            change = float(item["priceChangePercent"])
            volume = float(item["quoteVolume"])
            price = float(item["lastPrice"])

            if (not all(math.isfinite(value) for value in (price, change, volume))
                    or price <= 0 or change < min_change or volume < min_volume):
                continue

            entry_data: dict[str, Any] = {
                "coin": symbol[:-4],
                "change": change,
                "volume": volume,
                "price": price,
            }
            # Optional enrichment for topic scoring; absent on synthetic data.
            for src_key, dst_key in (
                ("highPrice", "high"),
                ("lowPrice", "low"),
                ("openPrice", "open"),
                ("count", "trade_count"),
            ):
                try:
                    entry_data[dst_key] = float(item[src_key])
                except (KeyError, TypeError, ValueError):
                    continue

            filtered.append(entry_data)
        except (KeyError, TypeError, ValueError):
            continue

    filtered.sort(key=lambda item: item["change"], reverse=True)
    return filtered[:top_n]


# ---------------------------------------------------------------------------
# Topic scoring (content opportunity ranking)
# ---------------------------------------------------------------------------

# Narrative angle families, chosen by the model *from verified evidence* only.
# They shape the hook and CTA; they never justify inventing data.
ANGLE_FAMILIES = (
    "contrarian_conflict",   # data pushes against the prevailing crowd read
    "binary_scenario",       # reclaim X or sweep Y first
    "data_finding",          # a measured datapoint is the story (flow, spike, OI)
    "event_risk",            # verified macro/schedule context frames the setup
    "outcome_update",        # follow-up on an existing thesis
    "structure_break",       # level flip / breakout-retest
)

DEFAULT_TOPIC_WEIGHTS = {
    "momentum": 0.35,
    "volatility": 0.20,
    "liquidity": 0.15,
    "participation": 0.15,
    "novelty": 0.15,
}

_NOVELTY_WINDOW_DAYS = 7.0


def _component(value: float, cap: float) -> float:
    if cap <= 0:
        return 0.0
    return max(0.0, min(1.0, value / cap))


def score_trending_candidates(
    candidates: list[dict[str, Any]],
    posted_entries: list[dict[str, Any]] | None = None,
    *,
    now: datetime.datetime | None = None,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Rank candidates by composite topic score instead of raw 24h gain.

    Components (each 0-1, weighted via ``trending.score_weights``):

    * ``momentum``     — 24h % change (capped at +50%: beyond that the extra
      attention a bigger candle buys is marginal);
    * ``volatility``   — intraday high-low range (sharp moves are the raw
      material of high-reach setups);
    * ``liquidity``    — 24h quote volume (a setup the audience can actually
      trade);
    * ``participation``— Binance trade count (crowd attention proxy);
    * ``novelty``      — decays over 7 days since the coin was last posted, so
      the same hot coin does not monopolise the feed rotation after rotation.

    Every component derives from fields already fetched from Binance or from
    the local post history: no new network calls, no unverifiable "news".
    The function is pure and offline-testable; it annotates each candidate
    with ``score`` and ``score_parts`` and returns a copy sorted descending.
    """

    import datetime as _datetime  # local import keeps module import cost low

    merged_weights = {**DEFAULT_TOPIC_WEIGHTS, **(weights or {})}
    posted_entries = posted_entries if isinstance(posted_entries, list) else []
    now = now or _datetime.datetime.now(_datetime.timezone.utc)

    last_posted: dict[str, _datetime.datetime] = {}
    for entry in posted_entries:
        if not isinstance(entry, dict):
            continue
        coin_name = str(entry.get("coin", "")).upper().strip()
        stamp = str(entry.get("time", "")).strip().replace("Z", "+00:00")
        try:
            moment = _datetime.datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=_datetime.timezone.utc)
        moment = moment.astimezone(_datetime.timezone.utc)
        if coin_name and (coin_name not in last_posted or moment > last_posted[coin_name]):
            last_posted[coin_name] = moment

    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        coin_name = str(candidate.get("coin", "")).upper().strip()
        try:
            change = float(candidate.get("change", 0.0))
            volume = float(candidate.get("volume", 0.0))
            price = float(candidate.get("price", 0.0))
        except (TypeError, ValueError):
            continue
        if not coin_name or price <= 0:
            continue

        # Intraday range measures realised volatility; fall back to |change|.
        try:
            high = float(candidate.get("high", 0.0))
            low = float(candidate.get("low", 0.0))
            open_price = float(candidate.get("open", 0.0))
        except (TypeError, ValueError):
            high = low = open_price = 0.0
        range_pct = ((high - low) / open_price * 100.0) if open_price > 0 and low > 0 else abs(change)

        try:
            trade_count = float(candidate.get("trade_count", 0.0))
        except (TypeError, ValueError):
            trade_count = 0.0

        last_seen = last_posted.get(coin_name)
        if last_seen is None:
            novelty = 1.0
        else:
            days_since = max(0.0, (now - last_seen).total_seconds() / 86400.0)
            novelty = min(1.0, days_since / _NOVELTY_WINDOW_DAYS)

        parts = {
            "momentum": _component(change, 50.0),
            "volatility": _component(range_pct, 40.0),
            "liquidity": _component(volume, 100_000_000.0),
            # Neutral 0.5 when the ticker did not supply a trade count.
            "participation": _component(trade_count, 2_000_000.0) if trade_count > 0 else 0.5,
            "novelty": novelty,
        }
        total = sum(parts[name] * merged_weights.get(name, 0.0) for name in parts)
        scored.append(
            {
                **candidate,
                "score": round(total, 4),
                "score_parts": {name: round(value, 4) for name, value in parts.items()},
            }
        )

    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored


def _available_groq_models(api_key: str) -> set[str] | None:
    """Return models available to this API key, or None on a transient failure."""

    try:
        response = requests.get(
            GROQ_MODELS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=20,
        )

        if response.status_code in {401, 403}:
            raise RuntimeError(
                "GROQ_API_KEY was rejected while checking available models"
            )

        response.raise_for_status()
        payload = response.json()
        items = payload.get("data", []) if isinstance(payload, dict) else []
        models = {
            str(item.get("id", "")).strip()
            for item in items
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
        if not models:
            raise RuntimeError("Groq returned an empty model list")

        print(f"[Groq] Account exposes {len(models)} active model(s)")
        return models

    except RuntimeError:
        raise
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        print(
            f"[Groq Warning] Could not query the model list ({exc}); "
            "trying configured production fallbacks"
        )
        return None


def _groq_model_candidates(api_key: str) -> list[str]:
    """Build an account-aware, non-repeating production model preference list."""

    configured = os.getenv("GROQ_MODEL", "").strip()
    preferred = [configured, *DEFAULT_GROQ_MODELS]
    unique_preferred: list[str] = []
    for model in preferred:
        if model and model not in unique_preferred:
            unique_preferred.append(model)

    available = _available_groq_models(api_key)
    if available is None:
        return unique_preferred

    candidates = [model for model in unique_preferred if model in available]
    if not candidates:
        visible = ", ".join(sorted(available))
        raise RuntimeError(
            "None of the supported Groq models are available to this "
            f"account. Available model IDs: {visible}"
        )

    skipped = [model for model in unique_preferred if model not in available]
    if skipped:
        print("[Groq] Unavailable candidates skipped:", ", ".join(skipped))

    return candidates


def _groq_chat_kwargs(model: str, completion_budget: int) -> dict[str, Any]:
    """Chat-completion kwargs with a reasoning-aware token budget.

    gpt-oss models spend part of the completion budget on hidden reasoning
    tokens, so a small ``max_tokens`` truncates the visible JSON document and
    Groq rejects it with ``json_validate_failed`` ("max completion tokens
    reached before generating a valid document").  A generous budget plus low
    reasoning effort keeps the JSON document complete.
    """

    kwargs: dict[str, Any] = {
        "max_tokens": completion_budget,
        "response_format": {"type": "json_object"},
    }
    if "gpt-oss" in str(model).lower():
        kwargs["extra_body"] = {"reasoning_effort": "low"}
    return kwargs


def _coerce_level(value: Any) -> float | None:
    """Parse a model-supplied price level leniently; None when unusable."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace("$", "").replace(",", "")
        text = text.replace(" ", "").replace("usdt", "").replace("USDT", "")
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _configured_leverage() -> str:
    """Single source of truth for the leverage label shown in payloads.

    The value used to be hardcoded as ``15x`` here while ``bot/config.json``
    said ``3x-5x`` and proof cards rendered another number. Content integrity
    starts with the bot quoting one consistent risk label everywhere.
    """

    leverage = str(trade_state.load_config().get("trading", {}).get("leverage", "3x-5x")).strip()
    return leverage or "3x-5x"


def _sanitize_hook_line(value: Any, *, max_chars: int = 70) -> str:
    """Shared whole-word/whole-price hook limit used again by the publisher."""

    return sanitize_hook(value, max_chars=max_chars)


def _sanitize_angle_family(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text if text in ANGLE_FAMILIES else "structure_break"


def _sanitize_scenarios(value: Any) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    return {
        "bull_if": clip_sentence(raw.get("bull_if", ""), 140),
        "bear_if": clip_sentence(raw.get("bear_if", ""), 140),
    }


def _validate_public_prose(result: dict[str, Any]) -> dict[str, str]:
    """Reject missing, short or copied public explanations; request a new draft."""

    fields = {
        name: validate_reason(result.get(name), field=name)
        for name in ("why_now", "rapid_reason")
    }
    hook = " ".join(str(result.get("hook_line", "") or "").split())
    if hook:
        validate_reason(hook, field="hook_line")
    return fields


def _trend_vote(value: Any) -> int:
    text = str(value or "").strip().lower()
    if text in {"up", "positive"} or any(marker in text for marker in ("bull", "uptrend", "upward", "rising", "higher")):
        return 1
    if text in {"down", "negative"} or any(marker in text for marker in ("bear", "downtrend", "downward", "falling", "lower")):
        return -1
    return 0


def rule_based_direction(
    live_price: float,
    research: dict[str, Any] | None,
    *,
    min_votes: int = _MIN_DIRECTION_VOTES,
) -> tuple[str | None, dict[str, int]]:
    """Infer a direction from 1H/4H trends, RSI and price-vs-VWAP evidence.

    A side is returned only when at least ``min_votes`` independent inputs
    agree, with no opposing 4H trend. The model cannot choose a LONG/SHORT
    against this rule-based read. RSI >=55 / <=45 and a VWAP distance over
    0.1% count as directional votes; neutral/missing readings do not vote.
    """

    research = research if isinstance(research, dict) else {}
    timeframes = research.get("timeframe_trends", {})
    timeframes = timeframes if isinstance(timeframes, dict) else {}
    indicators = research.get("indicators_1h", {})
    indicators = indicators if isinstance(indicators, dict) else {}

    votes: dict[str, int] = {
        "1h_trend": _trend_vote(timeframes.get("1h", timeframes.get("1H"))),
        "4h_trend": _trend_vote(timeframes.get("4h", timeframes.get("4H"))),
    }
    try:
        rsi = float(indicators.get("rsi"))
        if math.isfinite(rsi):
            votes["rsi"] = 1 if rsi >= 55 else -1 if rsi <= 45 else 0
    except (TypeError, ValueError, OverflowError):
        pass

    try:
        vwap = float(indicators.get("vwap"))
        if math.isfinite(vwap) and vwap > 0 and live_price > 0:
            distance = (live_price - vwap) / live_price
            votes["vwap_side"] = 1 if distance > 0.001 else -1 if distance < -0.001 else 0
    except (TypeError, ValueError, OverflowError):
        pass

    bullish = sum(value == 1 for value in votes.values())
    bearish = sum(value == -1 for value in votes.values())
    if bullish >= max(1, min_votes) and bullish > bearish and votes["4h_trend"] != -1:
        return "LONG", votes
    if bearish >= max(1, min_votes) and bearish > bullish and votes["4h_trend"] != 1:
        return "SHORT", votes
    return None, votes


def _trading_quality_settings(overrides: dict[str, Any] | None = None) -> dict[str, float]:
    try:
        stored = trade_state.load_config().get("trading", {})
    except Exception:
        stored = {}
    values = dict(stored) if isinstance(stored, dict) else {}
    if isinstance(overrides, dict):
        values.update(overrides)

    def number(key: str, fallback: float, *, minimum: float = 0.0) -> float:
        try:
            parsed = float(values.get(key, fallback))
            return max(minimum, parsed) if math.isfinite(parsed) else fallback
        except (TypeError, ValueError, OverflowError):
            return fallback

    return {
        "min_rr_tp1": number("min_rr_tp1", 1.0, minimum=1.0),
        "min_rr_tp2": number("min_rr_tp2", 2.0, minimum=2.0),
        "min_tp1_atr_fraction": number("min_tp1_atr_fraction", 0.6, minimum=0.6),
    }


def _level_quality_failures(
    entry: float,
    tp1: float,
    tp2: float,
    stop: float,
    research: dict[str, Any],
    thresholds: dict[str, float],
) -> list[str]:
    """Check reward-to-risk and TP1 distance against the measured 1H ATR."""

    risk = abs(entry - stop)
    atr = _research_float(
        _nested_dict(research, "indicators_1h", "atr"),
        float("nan"),
    )
    if risk <= 0 or not math.isfinite(risk):
        return ["stop distance is not positive"]
    if not math.isfinite(atr) or atr <= 0:
        return ["verified 1H ATR is unavailable"]

    rr_tp1 = abs(tp1 - entry) / risk
    rr_tp2 = abs(tp2 - entry) / risk
    atr_distance = abs(tp1 - entry) / atr
    failures: list[str] = []
    if rr_tp1 + 1e-12 < thresholds["min_rr_tp1"]:
        failures.append(f"TP1 RR {rr_tp1:.2f} < {thresholds['min_rr_tp1']:.2f}")
    if rr_tp2 + 1e-12 < thresholds["min_rr_tp2"]:
        failures.append(f"TP2 RR {rr_tp2:.2f} < {thresholds['min_rr_tp2']:.2f}")
    if atr_distance + 1e-12 < thresholds["min_tp1_atr_fraction"]:
        failures.append(
            f"TP1 distance {atr_distance:.2f} ATR < {thresholds['min_tp1_atr_fraction']:.2f} ATR"
        )
    return failures


def _nested_dict(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _round_level(price: float) -> float:
    """Round a level to a market-sane precision for its price magnitude."""

    if price >= 1000:
        decimals = 2
    elif price >= 100:
        decimals = 3
    elif price >= 1:
        decimals = 4
    elif price >= 0.01:
        decimals = 6
    else:
        decimals = 8
    return round(price, decimals)


def _derive_levels(
    direction: str, live_price: float, research: dict[str, Any] | None
) -> tuple[float, float, float]:
    """Derive deterministic, strictly ordered (tp1, tp2, sl) from ATR and S/R.

    Used as a repair path when a model returns null/non-numeric/unordered
    levels so one bad completion can no longer fail an entire signal run.
    """

    research = research if isinstance(research, dict) else {}
    indicators_1h = research.get("indicators_1h", {})
    indicators_1h = indicators_1h if isinstance(indicators_1h, dict) else {}
    sr_levels = indicators_1h.get("support_resistance", {})
    sr_levels = sr_levels if isinstance(sr_levels, dict) else {}

    atr = _research_float(indicators_1h.get("atr"), 0.0)
    if not math.isfinite(atr) or atr <= 0:
        atr = live_price * 0.01
    # Floor/cap keep every derived level positive and strictly separated.
    atr = min(atr, live_price * 0.10)
    atr = max(atr, live_price * 0.002)

    support = _coerce_level(sr_levels.get("support"))
    resistance = _coerce_level(sr_levels.get("resistance"))

    if direction == "SHORT":
        stop = live_price + 1.5 * atr
        if resistance is not None and resistance > live_price + 0.2 * atr:
            stop = min(resistance + 0.25 * atr, live_price + 3.0 * atr)
        tp1 = live_price - 1.5 * atr
        if support is not None and support < live_price - 0.5 * atr:
            tp1 = max(support + 0.25 * atr, live_price - 3.0 * atr)
        tp2 = min(live_price - 3.0 * atr, tp1 - 1.5 * atr)
        if support is not None and 0 < support < tp1 - 0.5 * atr:
            tp2 = min(tp2, support - 0.25 * atr)
        if tp2 <= 0:
            tp2 = tp1 / 2.0
    else:
        stop = live_price - 1.5 * atr
        if support is not None and support < live_price - 0.2 * atr:
            stop = max(support - 0.25 * atr, live_price - 3.0 * atr)
        tp1 = live_price + 1.5 * atr
        if resistance is not None and resistance > live_price + 0.5 * atr:
            tp1 = min(resistance - 0.25 * atr, live_price + 3.0 * atr)
        tp2 = max(live_price + 3.0 * atr, tp1 + 1.5 * atr)

    return _round_level(tp1), _round_level(tp2), _round_level(stop)


def _parse_json_object(raw_content: Any) -> dict[str, Any]:
    """Parse a model response defensively while still requiring one JSON object."""

    text = str(raw_content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    value = json.loads(text)
    if not isinstance(value, dict):
        raise RuntimeError("Groq returned JSON that is not an object")
    return value


def _normalize_signal(
    result: dict[str, Any],
    *,
    coin: str,
    live_price: float,
    change: float,
    trending_bonus: str,
    model: str,
    research: dict[str, Any] | None = None,
    trading_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize verified levels and enforce deterministic direction/RR gates."""

    if not math.isfinite(live_price) or live_price <= 0:
        raise SignalQualityError("Verified entry price must be finite and positive")
    direction = str(result.get("direction", "LONG")).upper().strip()
    if direction not in {"LONG", "SHORT"}:
        raise SignalQualityError(f"Unsupported signal direction: {direction}")

    research = research if isinstance(research, dict) else {}
    rule_direction, direction_votes = rule_based_direction(live_price, research)
    if rule_direction is None:
        raise SignalQualityError(
            "rule-based direction has insufficient agreement across 1H/4H trend, RSI and VWAP "
            f"(votes: {direction_votes})"
        )
    if direction != rule_direction:
        raise SignalQualityError(
            f"model direction {direction} conflicts with rule-based {rule_direction} "
            f"(votes: {direction_votes})"
        )

    thresholds = _trading_quality_settings(trading_config)
    # Round before the model gate so rounding-induced failures also get the
    # deterministic repair attempt, then check the repaired values again.
    def rounded(value: Any) -> float | None:
        level = _coerce_level(value)
        return _round_level(level) if level is not None else None

    tp1, tp2, stop = (rounded(result.get(key)) for key in ("tp1", "tp2", "sl"))

    def _levels_ordered(stop_level: float | None, tp1_level: float | None, tp2_level: float | None) -> bool:
        if any(level is None or not math.isfinite(level) or level <= 0
               for level in (stop_level, tp1_level, tp2_level)):
            return False
        if direction == "LONG":
            return stop_level < live_price < tp1_level < tp2_level
        return tp2_level < tp1_level < live_price < stop_level

    missing = [
        name for name, value in (("tp1", tp1), ("tp2", tp2), ("sl", stop)) if value is None
    ]
    model_failures = [] if missing or not _levels_ordered(stop, tp1, tp2) else _level_quality_failures(
        live_price, tp1, tp2, stop, research, thresholds
    )
    derive_reason = (
        f"missing {', '.join(missing)}" if missing
        else "unordered levels" if not _levels_ordered(stop, tp1, tp2)
        else "; ".join(model_failures)
    )
    if derive_reason:
        derived_tp1, derived_tp2, derived_stop = _derive_levels(
            direction, live_price, research
        )
        print(
            f"[Groq] Model levels failed quality checks ({derive_reason}); trying ATR/SR levels "
            f"TP1={derived_tp1} TP2={derived_tp2} SL={derived_stop}"
        )
        tp1, tp2, stop = derived_tp1, derived_tp2, derived_stop

    derived_failures = [] if _levels_ordered(stop, tp1, tp2) else ["derived levels are not strictly ordered"]
    if not derived_failures:
        derived_failures = _level_quality_failures(
            live_price, tp1, tp2, stop, research, thresholds
        )
    if derived_failures:
        raise SignalQualityError(
            "setup failed minimum risk/reward or ATR-distance gate after deterministic derivation: "
            + "; ".join(derived_failures)
        )

    def safe_text(field: str, fallback: str) -> str:
        value = result.get(field)
        text = str(value).strip() if value is not None else ""
        return text or fallback

    public_prose = _validate_public_prose(result)
    why_now = public_prose["why_now"]
    rapid_reason = public_prose["rapid_reason"]
    invalidation = safe_text(
        "invalidation", f"A 1H candle close beyond ${stop} invalidates this setup."
    )

    research = research if isinstance(research, dict) else {}
    market_context_default = research.get("market_context", {})
    market_context_default = market_context_default if isinstance(market_context_default, dict) else {}
    indicators_1h = research.get("indicators_1h", {})
    indicators_1h = indicators_1h if isinstance(indicators_1h, dict) else {}
    indicators_4h = research.get("indicators_4h", {})
    indicators_4h = indicators_4h if isinstance(indicators_4h, dict) else {}
    futures_default = research.get("futures", {})
    futures_default = futures_default if isinstance(futures_default, dict) else {}
    volume_default = research.get("volume_analysis", {})
    volume_default = volume_default if isinstance(volume_default, dict) else {}

    def safe_field(field: str, fallback: Any) -> Any:
        value = result.get(field)
        if (
            value is None
            or (isinstance(value, str) and not value.strip())
            or (isinstance(value, (dict, list, tuple)) and not value)
        ):
            return fallback
        return value

    def safe_score(value: Any, fallback: int) -> int:
        try:
            score = float(value)
            if not math.isfinite(score):
                return fallback
            return max(0, min(100, int(round(score))))
        except (TypeError, ValueError, OverflowError):
            return fallback

    try:
        confidence = safe_score(result.get("confidence", 85), 85)
    except Exception:
        confidence = 85
    trendlines_4h = indicators_4h.get("trendlines", {})
    trendlines_4h = trendlines_4h if isinstance(trendlines_4h, dict) else {}
    indicators_summary_default = (
        f"1H RSI {indicators_1h.get('rsi', 'n/a')}; EMA20/50/200 "
        f"{indicators_1h.get('ema_20', 'n/a')}/{indicators_1h.get('ema_50', 'n/a')}/"
        f"{indicators_1h.get('ema_200', 'n/a')}; 4H trend "
        f"{trendlines_4h.get('trend', 'unavailable')}"
    )
    liquidity_default = research.get("orderbook", {})
    liquidity_default = liquidity_default if isinstance(liquidity_default, dict) else {}
    heatmap_default = futures_default.get("liquidation_levels", {})
    heatmap_default = heatmap_default if isinstance(heatmap_default, dict) else {}
    sentiment_default = {
        "social_sentiment": market_context_default.get("social_sentiment", "unavailable"),
        "on_chain_proxy": market_context_default.get("on_chain_proxy", {}),
    }
    atr_1h = _research_float(indicators_1h.get("atr"), float("nan"))
    volatility_default = {
        "atr_1h": atr_1h if math.isfinite(atr_1h) and atr_1h > 0 else None,
        "atr_percent": (
            atr_1h / live_price * 100.0
            if live_price > 0 and math.isfinite(atr_1h) and atr_1h > 0
            else None
        ),
        "bollinger_bands": indicators_1h.get("bollinger_bands", {}),
        "estimated": False,
    }
    macro_default = market_context_default.get("macro_factors", {})
    macro_default = macro_default if isinstance(macro_default, dict) else {}
    mtf_default = research.get("timeframe_trends", {})
    mtf_default = mtf_default if isinstance(mtf_default, dict) else {}
    fundamental_default = market_context_default.get("fundamental_notes", "Event risk unverified.")
    try:
        content_config = trade_state.load_config().get("content", {})
        hook_limit = min(70, max(1, int(content_config.get("hook_max_chars", 70))))
    except (TypeError, ValueError, AttributeError):
        hook_limit = 70
    hook_line = _sanitize_hook_line(result.get("hook_line", ""), max_chars=hook_limit)
    if hook_line and _word_count(hook_line) < 6:
        raise SignalContentError(
            f"hook_line was reduced below six words while trimming to {hook_limit} characters; regenerate it"
        )
    computed_risk_management = {
        "entry": live_price,
        "tp1": tp1,
        "tp2": tp2,
        "stop_loss": stop,
        "risk_reward_tp1": abs(tp1 - live_price) / abs(live_price - stop),
        "risk_reward_tp2": abs(tp2 - live_price) / abs(live_price - stop),
        "guidance": "Position size must be based on a predefined account-risk limit.",
    }

    normalized = {
        **result,
        "coin": str(coin).upper(),
        "trending_bonus": str(trending_bonus).upper(),
        "direction": direction,
        "entry": "CMP",
        "entry_price": live_price,
        "tp1": tp1,
        "tp2": tp2,
        "sl": stop,
        "leverage": _configured_leverage(),
        "hook_line": hook_line,
        "direction_validation": {"direction": rule_direction, "votes": direction_votes},
        "angle_family": _sanitize_angle_family(result.get("angle_family")),
        "scenarios": _sanitize_scenarios(result.get("scenarios")),
        "risk": str(result.get("risk", "Medium Risk")),
        "confidence": confidence,
        "why_now": why_now,
        "invalidation": invalidation,
        "rapid_reason": rapid_reason,
        "smc_logic": safe_text("smc_logic", why_now),
        "smc_logic_short": safe_text("smc_logic_short", rapid_reason),
        "change": change,
        "groq_model": model,
        "technical_analysis": safe_field(
            "technical_analysis", result.get("smc_logic") or why_now
        ),
        "indicators_summary": safe_field(
            "indicators_summary", indicators_summary_default
        ),
        "volume_analysis": safe_field("volume_analysis", volume_default),
        "futures_data": safe_field("futures_data", futures_default),
        "liquidity_analysis": safe_field("liquidity_analysis", liquidity_default),
        "liquidation_heatmap": safe_field("liquidation_heatmap", heatmap_default),
        "mtf_analysis": safe_field("mtf_analysis", mtf_default),
        "market_context": safe_field("market_context", market_context_default),
        "sentiment_onchain": safe_field("sentiment_onchain", sentiment_default),
        "volatility_assessment": safe_field(
            "volatility_assessment", volatility_default
        ),
        "risk_management": computed_risk_management,
        "validation_score": safe_score(
            result.get("validation_score", confidence), confidence
        ),
        "macro_factors": safe_field("macro_factors", macro_default),
        "fundamental_note": safe_text(
            "fundamental_note",
            safe_text("fundamental_risk", str(fundamental_default)),
        ),
    }
    normalized["fundamental_risk"] = safe_text(
        "fundamental_risk", str(normalized["fundamental_note"])
    )
    return normalized


def _is_authentication_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "invalid api key",
            "authentication",
            "unauthorized",
            "status code: 401",
            "error code: 401",
        )
    )


def _research_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else default
    except (TypeError, ValueError, OverflowError):
        return default


def _build_institutional_research(
    coin: str,
    change: float,
    live_price: float,
    trending_bonus: str,
) -> dict[str, Any]:
    """Gather optional spot, technical, futures, and macro context defensively."""
    symbol = f"{str(coin).upper()}USDT"
    try:
        klines_mtf = get_klines_multi_timeframe(symbol)
    except Exception as exc:
        print(f"[Research Warning] Multi-timeframe candles unavailable for {symbol}: {exc}")
        klines_mtf = {frame: [] for frame in ("5m", "15m", "1h", "4h", "1d", "1w")}

    indicators_1h = calculate_all_indicators(klines_mtf.get("1h", []))
    indicators_4h = calculate_all_indicators(klines_mtf.get("4h", []))
    timeframe_trends: dict[str, str] = {}
    for interval in ("5m", "15m", "1h", "4h", "1d", "1w"):
        candles = klines_mtf.get(interval, [])
        if not candles:
            timeframe_trends[interval] = "unavailable"
            continue
        indicators = (
            indicators_1h
            if interval == "1h"
            else indicators_4h
            if interval == "4h"
            else calculate_all_indicators(candles)
        )
        timeframe_trends[interval] = str(
            indicators.get("trendlines", {}).get("trend", "unavailable")
        )

    try:
        ticker = get_24h_data(symbol)
    except Exception as exc:
        print(f"[Research Warning] 24h ticker unavailable for {symbol}: {exc}")
        ticker = {
            "symbol": symbol,
            "price": live_price,
            "change_24h": change,
            "quote_volume_24h": None,
            "verified": False,
        }

    try:
        orderbook = get_orderbook(symbol, limit=20)
    except Exception as exc:
        print(f"[Research Warning] Order book unavailable for {symbol}: {exc}")
        orderbook = {"bids": [], "asks": [], "imbalance": "unavailable", "imbalance_ratio": None}

    volume_analysis = calculate_volume_analysis(klines_mtf.get("1h", []), ticker)
    try:
        open_interest = get_open_interest(symbol)
    except Exception as exc:
        print(f"[Research Warning] Open interest unavailable for {symbol}: {exc}")
        open_interest = None
    try:
        funding_rate = get_funding_rate(symbol)
    except Exception as exc:
        print(f"[Research Warning] Funding unavailable for {symbol}: {exc}")
        funding_rate = None
    try:
        long_short_ratio = get_long_short_ratio(symbol)
    except Exception as exc:
        print(f"[Research Warning] Long/short ratio unavailable for {symbol}: {exc}")
        long_short_ratio = None
    try:
        liquidation_estimate = get_liquidation_estimate(symbol)
    except Exception as exc:
        print(f"[Research Warning] Liquidation estimate unavailable for {symbol}: {exc}")
        liquidation_estimate = None
    try:
        futures_spot_volume = get_futures_vs_spot_volume(symbol)
    except Exception as exc:
        print(f"[Research Warning] Futures/spot volume unavailable for {symbol}: {exc}")
        futures_spot_volume = None
    try:
        liquidation_levels = get_liquidation_heatmap_levels(
            symbol,
            klines_mtf.get("1h", []),
            open_interest_data=open_interest,
            long_short_data=long_short_ratio,
        )
    except Exception as exc:
        print(f"[Research Warning] Liquidation levels unavailable for {symbol}: {exc}")
        liquidation_levels = {
            "long_liquidation_zone": None,
            "short_liquidation_zone": None,
            "magnet_price": None,
            "clusters": [],
            "estimated": True,
        }

    futures = {
        "open_interest": open_interest,
        "funding_rate": funding_rate,
        "long_short_ratio": long_short_ratio,
        "liquidation_estimate": liquidation_estimate,
        "futures_vs_spot_volume": futures_spot_volume,
        "liquidation_levels": liquidation_levels,
    }

    try:
        btc_data = get_24h_data("BTCUSDT")
    except Exception as exc:
        print(f"[Research Warning] BTC ticker unavailable: {exc}")
        btc_data = None
    try:
        eth_data = get_24h_data("ETHUSDT")
    except Exception as exc:
        print(f"[Research Warning] ETH ticker unavailable: {exc}")
        eth_data = None
    try:
        trending = get_trending_coins(top_n=20, min_change=-100.0, min_volume=0.0)
    except Exception as exc:
        print(f"[Research Warning] Broad market scan unavailable: {exc}")
        trending = [{"coin": str(trending_bonus).upper(), "change": change}]
    try:
        market_context = get_market_context(
            btc_data,
            eth_data,
            trending,
            coin_symbol=coin,
        )
    except Exception as exc:
        print(f"[Research Warning] Market context unavailable: {exc}")
        market_context = {
            "bitcoin_trend": "unavailable",
            "btc_dominance": {"estimated": True, "source": "unavailable"},
            "fundamental_notes": "Macro/event context unavailable; no event claims should be made.",
        }
    btc_dominance = market_context.get("btc_dominance", {})

    def number(value: Any, digits: int = 4) -> str:
        value = _research_float(value, float("nan"))
        return f"{value:.{digits}f}" if value == value else "unavailable"

    trend_1h = timeframe_trends.get("1h", "unavailable")
    trend_4h = timeframe_trends.get("4h", "unavailable")
    macd = indicators_1h.get("macd", {})
    bands = indicators_1h.get("bollinger_bands", {})
    stochastic = indicators_1h.get("stoch_rsi", {})
    levels = indicators_1h.get("support_resistance", {})
    breakout = indicators_1h.get("breakout_breakdown", {})
    orderbook_ratio = orderbook.get("imbalance_ratio") if isinstance(orderbook, dict) else None
    orderbook_imbalance = orderbook.get("imbalance", "unavailable") if isinstance(orderbook, dict) else "unavailable"
    funding_bias = funding_rate.get("bias", "unavailable") if isinstance(funding_rate, dict) else "unavailable"
    lsr = long_short_ratio.get("long_short_ratio") if isinstance(long_short_ratio, dict) else None
    oi_usdt = open_interest.get("open_interest_usdt") if isinstance(open_interest, dict) else None
    heatmap = liquidation_levels if isinstance(liquidation_levels, dict) else {}

    analysis_points = [
        {"number": 1, "name": "Multi-timeframe trend", "observation": "; ".join(f"{frame} {timeframe_trends.get(frame, 'unavailable')}" for frame in ("5m", "15m", "1h", "4h", "1d", "1w")) + "."},
        {"number": 2, "name": "EMA regime", "observation": f"1H close {number(indicators_1h.get('close'))}; EMA20 {number(indicators_1h.get('ema_20'))}; EMA50 {number(indicators_1h.get('ema_50'))}; EMA200 {number(indicators_1h.get('ema_200'))}."},
        {"number": 3, "name": "RSI momentum", "observation": f"1H RSI(14) {number(indicators_1h.get('rsi'), 2)}."},
        {"number": 4, "name": "MACD momentum", "observation": f"MACD {number(macd.get('macd'))}; signal {number(macd.get('signal'))}; histogram {number(macd.get('histogram'))}."},
        {"number": 5, "name": "Bollinger volatility/position", "observation": f"Position {number(bands.get('position'), 2)}; bandwidth {number(bands.get('bandwidth'), 2)}%; bands {number(bands.get('lower'))}–{number(bands.get('upper'))}."},
        {"number": 6, "name": "Stochastic RSI", "observation": f"%K {number(stochastic.get('k'), 2)}; %D {number(stochastic.get('d'), 2)}."},
        {"number": 7, "name": "VWAP location", "observation": f"VWAP {number(indicators_1h.get('vwap'))} versus verified entry {number(live_price)}."},
        {"number": 8, "name": "Candlestick trigger", "observation": ", ".join(indicators_1h.get("candlestick_patterns", [])) or "no listed reversal pattern detected."},
        {"number": 9, "name": "Market structure", "observation": str(indicators_1h.get("market_structure", "insufficient data")) + f"; 4H {indicators_4h.get('market_structure', 'insufficient data')}."},
        {"number": 10, "name": "Breakout/BOS confirmation", "observation": f"{breakout.get('type', 'none')}; confirmed={bool(breakout.get('confirmed', False))}; level={number(breakout.get('level'))}."},
        {"number": 11, "name": "Support and resistance", "observation": f"Support {number(levels.get('support'))}; resistance {number(levels.get('resistance'))}."},
        {"number": 12, "name": "ATR risk regime", "observation": f"1H ATR(14) {number(indicators_1h.get('atr'))} ({( _research_float(indicators_1h.get('atr')) / live_price * 100.0 if live_price > 0 else 0.0):.2f}% of entry)."},
        {"number": 13, "name": "Volume participation", "observation": f"buy {number(volume_analysis.get('buying_volume'), 2)} / sell {number(volume_analysis.get('selling_volume'), 2)}; spike={bool(volume_analysis.get('volume_spike'))}; breakout confirmation={bool(volume_analysis.get('breakout_volume_confirmed'))}; divergence={volume_analysis.get('volume_divergence', 'none')}."},
        {"number": 14, "name": "Order-book liquidity", "observation": f"{orderbook_imbalance}; bid/ask notional ratio {number(orderbook_ratio, 3)}."},
        {"number": 15, "name": "Derivatives positioning/liquidation", "observation": f"funding bias {funding_bias}; L/S {number(lsr, 3)}; OI notional ${number(oi_usdt, 0)}; liquidation zones estimated={bool(heatmap.get('estimated', True))}."},
    ]

    orderbook_summary = {
        "imbalance": orderbook_imbalance,
        "imbalance_ratio": orderbook_ratio,
        "bid_depth_usdt": orderbook.get("bid_depth_usdt") if isinstance(orderbook, dict) else None,
        "ask_depth_usdt": orderbook.get("ask_depth_usdt") if isinstance(orderbook, dict) else None,
        "top_bids": (orderbook.get("bids", [])[:5] if isinstance(orderbook, dict) else []),
        "top_asks": (orderbook.get("asks", [])[:5] if isinstance(orderbook, dict) else []),
        "available": bool(orderbook.get("available", False)) if isinstance(orderbook, dict) else False,
    }
    ticker_summary = {
        "price": _research_float(ticker.get("price"), live_price),
        "change_24h": _research_float(ticker.get("change_24h"), change),
        "quote_volume_24h": ticker.get("quote_volume_24h"),
        "verified": bool(ticker.get("verified", False)),
    }
    return {
        "analysis_points": analysis_points,
        "indicators_1h": indicators_1h,
        "indicators_4h": indicators_4h,
        "timeframe_trends": timeframe_trends,
        "klines_available": {frame: len(rows) for frame, rows in klines_mtf.items()},
        "ticker": ticker_summary,
        "volume_analysis": volume_analysis,
        "orderbook": orderbook_summary,
        "futures": futures,
        "market_context": market_context,
        "btc_dominance": btc_dominance,
    }


def generate_signal_with_groq(
    coin: str,
    price: float,
    change: float,
    trending_bonus: str,
    previous_angle_family: str = "",
) -> dict[str, Any]:
    """Generate one validated signal with hook, Why Now, scenarios and invalidation.

    ``previous_angle_family`` is the last post's angle family; when two posts
    in a row lead with the same hook pattern the feed starts to feel
    templated (a documented weakness in the creator research), so the model
    is nudged toward a different family whenever the data supports one.
    """

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found in GitHub Secrets.")

    supplied_price = float(price)
    if not math.isfinite(supplied_price) or supplied_price <= 0:
        raise ValueError("Supplied signal price must be greater than zero")

    live_price = get_live_price(coin)
    difference_percent = abs(live_price - supplied_price) / supplied_price * 100
    if difference_percent > 0.5:
        raise RuntimeError(
            f"Price mismatch detected for {coin}: "
            f"supplied={supplied_price}, verified={live_price}"
        )

    institutional_research = _build_institutional_research(
        coin=coin,
        change=float(change),
        live_price=live_price,
        trending_bonus=trending_bonus,
    )
    expected_direction, votes = rule_based_direction(live_price, institutional_research)
    if expected_direction is None:
        raise SignalQualityError(f"No rule-based direction consensus for {coin}: {votes}")
    atr = _research_float(_nested_dict(institutional_research, "indicators_1h", "atr"))
    if atr <= 0:
        raise SignalQualityError(f"Verified 1H ATR is unavailable for {coin}")
    research_json = json.dumps(
        institutional_research,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )

    system_prompt = """
You are an institutional crypto analyst writing for busy mobile readers on Binance Square. Use only the verified research snapshot. Distinguish measured values from estimates, but never advertise a missing feed; omit unavailable evidence from public prose. Never invent prices, flows, token events, macro releases or execution. Build curiosity from a real measured tension, not clickbait or promises. Return exactly one valid JSON object and no prose outside JSON.
""".strip()

    angle_families = ", ".join(ANGLE_FAMILIES)
    previous_family = str(previous_angle_family or "").strip().lower()
    rotation_hint = (
        f"The previous post on this account used hook angle '{previous_family}'. "
        "If the data honestly supports a different angle family, choose it - "
        "repeating the same hook shape twice in a row reads as templated. Never "
        "force an angle the data contradicts.\n"
        if previous_family in ANGLE_FAMILIES
        else ""
    )
    user_prompt = f"""
Coin: {str(coin).upper()}
Verified Binance live price (Entry CMP): {live_price}
24h change: {float(change):.2f}%
Context: {str(trending_bonus).upper()}
Rule-based direction: {expected_direction} (4H trend cannot oppose it)
{rotation_hint}
Research snapshot (JSON):
{research_json}

Use only verified entry price. Assess EMA/RSI/MACD/VWAP/Bollinger/Stoch RSI, volume spike/breakout, futures OI/funding/long-short, order-book, liquidation heatmap zones, MTF 5m/15m/1H/4H/1D/1W, BTC dominance/trend, ETH/BTC, fear/greed, ATR, and Fed/CPI/DXY macros. Never turn missing feeds into a claim; the publisher omits unavailable evidence. Mark estimates only when an estimated measurement is actually used. Keep public prose direct and mobile-readable.

The JSON strings below are schema instructions, not text to copy. Replace every instruction with a complete, specific sentence grounded in the research snapshot. Never return literal placeholders such as "Trigger." or "1H trigger.". The publisher computes the risk line from verified entry, TP1 and SL; do not invent a risk score or sizing claim.

Return one valid JSON object with every required key below:
{{
  "direction": "LONG or SHORT, consistent with the supplied 1H/4H trend stack, RSI and price-vs-VWAP evidence",
  "entry_price": {live_price},
  "tp1": 0,
  "tp2": 0,
  "sl": 0,
  "confidence": 0,
  "hook_line": "Write one complete, verified hook in 6 or more words and 70 characters or fewer. Use one real data tension or finding; no promises or unsupported numbers.",
  "angle_family": "exactly one of: {angle_families}",
  "scenarios": {{
    "bull_if": "A concise measured condition that keeps the upside path open",
    "bear_if": "A concise measured condition that invalidates the thesis"
  }},
  "rapid_reason": "One complete sentence of at least 6 words naming the measured 1H trigger or evidence and why it matters.",
  "why_now": "One complete sentence of at least 6 words explaining the measured reason this setup exists now.",
  "invalidation": "One precise invalidation condition tied to the verified stop level.",
  "smc_logic": "A concise evidence-based structure read using only supplied observations.",
  "smc_logic_short": "A short chart annotation based on a supplied measured level.",
  "technical_analysis": "One concise sentence describing actual structure and indicator alignment.",
  "indicators_summary": "Summarize only indicator readings present in the research snapshot.",
  "validation_score": 0,
  "analysis_summary": "A concise readout using measured evidence only.",
  "risk_factors": ["A specific measured or explicitly unverified risk", "Another relevant risk if supported"],
  "fundamental_note": "A verified catalyst or a concise statement that none was confirmed in the supplied data."
}}

Do NOT output these keys; they are auto-attached from the verified research
snapshot above: "volume_analysis", "futures_data", "liquidity_analysis",
"liquidation_heatmap", "mtf_analysis", "market_context", "sentiment_onchain",
"volatility_assessment", "macro_factors", "risk_management" (computed from levels).

Rules:
1. entry_price must equal {live_price} exactly.
2. tp1, tp2, sl must be positive numbers, never null. LONG: SL < entry_price < TP1 < TP2; SHORT: TP2 < TP1 < entry_price < SL. The bot will enforce RR >= 1.0 to TP1, RR >= 2.0 to TP2, and TP1 distance >= 0.6 of measured 1H ATR.
3. Direction is not a free choice: use {expected_direction}, following the 1H/4H trend stack, RSI and VWAP side in the snapshot. A response that disagrees with the bot's rule-based direction check is rejected.
4. Never invent a price, macro release, token event or flow. Do not advertise missing feeds. Mark a proxy estimated only when an estimated measurement is used.
5. why_now and rapid_reason must each be a complete sentence of at least 6 words with a specific measured reason; never use generic trigger placeholders.
6. hook_line must be 70 characters or fewer, at least 6 words, anchored to this coin or a verified finding, and must not end in a cut-off word or partial price. At most one question mark; no promises or banned claims.
7. scenarios.bull_if / bear_if must be mutually exclusive conditions tied to measured levels.
8. Return JSON only; do not copy any schema instruction string literally.
""".strip()

    client = Groq(api_key=api_key)
    candidates = _groq_model_candidates(api_key)
    failures: list[str] = []
    rejected_setups: list[str] = []
    rejected_narratives: list[str] = []
    try:
        trading_config = trade_state.load_config().get("trading", {})
    except Exception:
        trading_config = {}

    for model in candidates:
        print(f"[Groq] Generating signal with model: {model}")
        for attempt in range(_SIGNAL_REGENERATION_ATTEMPTS):
            user_message = user_prompt
            if attempt:
                user_message += (
                    "\n\nREGENERATION REQUIRED: The previous draft used placeholder or too-short "
                    "public prose. Write fresh, complete why_now and rapid_reason sentences (at "
                    "least 6 words each) and a specific hook no longer than 70 characters. Do not "
                    "copy instructions from the JSON schema. Return the full JSON object."
                )
            try:
                completion = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0.2,
                    **_groq_chat_kwargs(model, 16384),
                )
                result = _parse_json_object(completion.choices[0].message.content)
                normalized = _normalize_signal(
                    result,
                    coin=coin,
                    live_price=live_price,
                    change=float(change),
                    trending_bonus=trending_bonus,
                    model=model,
                    research=institutional_research,
                    trading_config=trading_config,
                )
                normalized["institutional_analysis"] = institutional_research
                normalized["analysis_summary"] = str(
                    result.get("analysis_summary", "Market structure and participation were checked against the available data.")
                ).strip()
                normalized["fundamental_risk"] = str(
                    result.get(
                        "fundamental_risk",
                        institutional_research.get("market_context", {}).get("fundamental_notes", "Unverified."),
                    )
                ).strip()
                risk_factors = result.get("risk_factors", [])
                normalized["risk_factors"] = (
                    [str(item).strip() for item in risk_factors if str(item).strip()][:5]
                    if isinstance(risk_factors, list)
                    else [str(risk_factors).strip()]
                )
                print(f"[Groq] Signal validated with {model}")
                return normalized

            except SignalContentError as exc:
                failure = f"{model} attempt {attempt + 1}: {exc}"
                print(f"[Groq Warning] {failure}")
                if attempt + 1 == _SIGNAL_REGENERATION_ATTEMPTS:
                    rejected_narratives.append(failure)
            except SignalQualityError as exc:
                failure = f"{model}: {exc}"
                rejected_setups.append(failure)
                print(f"[Signal Quality] {failure}")
                break
            except Exception as exc:
                if _is_authentication_error(exc):
                    raise RuntimeError("GROQ_API_KEY authentication failed") from exc

                failure = f"{model}: {type(exc).__name__}: {exc}"
                failures.append(failure)
                print(f"[Groq Warning] {failure}")
                break

    if rejected_setups or rejected_narratives:
        reasons = rejected_setups + rejected_narratives
        raise SignalQualityError(
            "All model completions were rejected by the signal quality gate: " + " | ".join(reasons)
        )
    summary = " | ".join(failures) if failures else "no candidate model was attempted"
    raise RuntimeError(f"All Groq signal models failed: {summary}")


def generate_market_overview_with_groq(
    top_coins: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate verified Macro & Bitcoin Market Overview using Binance market data and Groq."""

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found in GitHub Secrets.")

    btc_data = get_24h_data("BTCUSDT")
    eth_data = get_24h_data("ETHUSDT")

    btc_price = float(btc_data["price"])
    btc_change = float(btc_data["change_24h"])
    btc_volume = float(btc_data.get("quote_volume_24h", 0))

    eth_price = float(eth_data["price"])
    eth_change = float(eth_data["change_24h"])

    if not top_coins:
        try:
            top_coins = get_trending_coins(top_n=5, min_change=2.0, min_volume=3_000_000)
        except Exception:
            top_coins = []

    try:
        btc_klines_mtf = get_klines_multi_timeframe("BTCUSDT")
    except Exception as exc:
        print(f"[Market Overview Warning] BTC multi-timeframe data unavailable: {exc}")
        btc_klines_mtf = {frame: [] for frame in ("5m", "15m", "1h", "4h", "1d", "1w")}
    try:
        eth_klines_mtf = get_klines_multi_timeframe("ETHUSDT")
    except Exception as exc:
        print(f"[Market Overview Warning] ETH multi-timeframe data unavailable: {exc}")
        eth_klines_mtf = {frame: [] for frame in ("5m", "15m", "1h", "4h", "1d", "1w")}
    btc_indicators_1h = calculate_all_indicators(btc_klines_mtf.get("1h", []))
    btc_indicators_4h = calculate_all_indicators(btc_klines_mtf.get("4h", []))
    eth_indicators_1h = calculate_all_indicators(eth_klines_mtf.get("1h", []))
    eth_indicators_4h = calculate_all_indicators(eth_klines_mtf.get("4h", []))
    try:
        overview_market_context = get_market_context(
            {**btc_data, "klines_mtf": btc_klines_mtf},
            {**eth_data, "klines_mtf": eth_klines_mtf},
            top_coins or [],
        )
    except Exception as exc:
        print(f"[Market Overview Warning] Optional macro context unavailable: {exc}")
        overview_market_context = {
            "btc_dominance": {"estimated": True, "source": "unavailable"},
            "fear_greed": {"estimated": True, "source": "unavailable"},
            "macro_factors": {"fed_rate_context": "unavailable", "cpi_context": "unavailable", "dxy_proxy": "unavailable"},
        }
    overview_research = {
        "btc_indicators_1h": btc_indicators_1h,
        "btc_indicators_4h": btc_indicators_4h,
        "eth_indicators_1h": eth_indicators_1h,
        "eth_indicators_4h": eth_indicators_4h,
        "btc_dominance": overview_market_context.get("btc_dominance", {}),
        "fear_greed": overview_market_context.get("fear_greed", {}),
        "market_context": overview_market_context,
    }
    overview_research_json = json.dumps(
        overview_research, ensure_ascii=False, separators=(",", ":"), default=str
    )

    gainers_summary = ", ".join(
        [f"{c['coin']} ({c['change']:+.1f}%)" for c in top_coins[:4]]
    ) if top_coins else "Altcoin momentum mixed"

    system_prompt = """
You are a top-tier institutional crypto macro and technical strategist on Binance Square.
You analyze Bitcoin structure, liquidity flows, dominance, and altcoin rotation from verified Binance data.
Provide crisp, insightful, and authoritative market intelligence for active traders.
Return exactly one valid JSON object and no prose outside that JSON object.
""".strip()

    user_prompt = f"""
Verified Binance Market Data:
• BTC/USDT: ${btc_price:,.2f} ({btc_change:+.2f}% 24H, 24H Vol: ${btc_volume:,.0f})
• ETH/USDT: ${eth_price:,.2f} ({eth_change:+.2f}% 24H)
• Top Momentum Gainers: {gainers_summary}

Verified BTC/ETH technical and macro context (JSON; unavailable values are intentionally marked):
{overview_research_json}

Use the supplied BTC and ETH 1H/4H RSI and EMA20/50/200 readings in the thesis whenever present. Explicitly incorporate BTC dominance and Fear & Greed in the market pulse; if either is estimated, label it estimated. Do not invent technical readings or macro data. Keep the existing overview output structure and add the new fields below.

Return exactly this JSON structure:
{{
  "headline": "Punchy market headline summarizing BTC structure and current market pulse",
  "hook_line": "<=70 chars and at least 6 words, one line: the single most interesting verified tension in today's data (e.g. price vs sentiment divergence, dominance shift vs alt momentum). No promises, no clickbait, no 'guaranteed'; every number from the supplied data.",
  "btc_price": {btc_price},
  "btc_change": {btc_change},
  "market_phase": "e.g. Bullish Expansion / Consolidation / Key Resistance Retest / Liquidity Hunt",
  "btc_thesis": "1-2 sentences on Bitcoin 1H/4H price action, structure holding, and liquidity zones.",
  "altcoin_summary": "1-2 sentences explaining altcoin capital rotation and momentum leaders.",
  "btc_support": <numeric nearby key support level below btc_price>,
  "btc_resistance": <numeric nearby key resistance level above btc_price>,
  "what_flips_bullish": "one short measured condition that would strengthen the constructive read",
  "what_flips_bearish": "one short measured condition that would weaken it",
  "strategy_outlook": "One clear, actionable rule/insight for spot and futures traders today."
}}

Rules:
1. btc_price must equal {btc_price}.
2. btc_support < btc_price < btc_resistance.
3. Keep the tone professional, objective, and analytical; curiosity from data tension, never hype.
4. hook_line must be anchored in BTC, ETH, dominance, or Fear & Greed numbers from the supplied data.
5. Return JSON only.
""".strip()

    client = Groq(api_key=api_key)
    candidates = _groq_model_candidates(api_key)
    failures: list[str] = []

    for model in candidates:
        print(f"[Groq] Generating market overview with model: {model}")
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.25,
                **_groq_chat_kwargs(model, 8192),
            )
            result = _parse_json_object(completion.choices[0].message.content)

            support = float(result.get("btc_support", btc_price * 0.98))
            resistance = float(result.get("btc_resistance", btc_price * 1.02))
            if support >= btc_price:
                support = round(btc_price * 0.98, 2)
            if resistance <= btc_price:
                resistance = round(btc_price * 1.02, 2)

            overview = {
                "headline": str(result.get("headline", "Bitcoin Consolidates as Momentum Expands across Altcoins")).strip(),
                "hook_line": _sanitize_hook_line(result.get("hook_line", "")),
                "what_flips_bullish": _sanitize_hook_line(result.get("what_flips_bullish", ""), max_chars=140),
                "what_flips_bearish": _sanitize_hook_line(result.get("what_flips_bearish", ""), max_chars=140),
                "btc_price": btc_price,
                "btc_change": btc_change,
                "eth_price": eth_price,
                "eth_change": eth_change,
                "market_phase": str(result.get("market_phase", "Consolidation Range")).strip(),
                "btc_thesis": str(result.get("btc_thesis", "BTC is maintaining structure above local support as volume builds.")).strip(),
                "altcoin_summary": str(result.get("altcoin_summary", f"Momentum continues in selective altcoins: {gainers_summary}.")).strip(),
                "btc_support": support,
                "btc_resistance": resistance,
                "strategy_outlook": str(result.get("strategy_outlook", "Focus on high-volume setups with defined invalidation levels.")).strip(),
                "btc_indicators_1h": btc_indicators_1h,
                "btc_indicators_4h": btc_indicators_4h,
                "eth_indicators_1h": eth_indicators_1h,
                "eth_indicators_4h": eth_indicators_4h,
                "btc_dominance": overview_market_context.get("btc_dominance", {}),
                "fear_greed": overview_market_context.get("fear_greed", {}),
                "groq_model": model,
                "type": "market_overview",
            }
            print(f"[Groq] Market overview validated with {model}")
            return overview

        except Exception as exc:
            if _is_authentication_error(exc):
                raise RuntimeError("GROQ_API_KEY authentication failed") from exc

            failure = f"{model}: {type(exc).__name__}: {exc}"
            failures.append(failure)
            print(f"[Groq Warning] {failure}")

    summary = " | ".join(failures) if failures else "no candidate model was attempted"
    raise RuntimeError(f"All Groq market overview models failed: {summary}")


def generate_meme_post_with_groq(trending_context: str = "") -> dict[str, Any]:
    """Generate high-engagement crypto trader psychology / community culture meme post.

    ``trending_context`` optionally names the day's strongest mover so the
    humor reacts to today's market instead of floating in a vacuum; the
    context is informational only and the meme must not state fake positions.
    """

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found in GitHub Secrets.")

    btc_data = get_24h_data("BTCUSDT")
    btc_price = float(btc_data["price"])
    btc_change = float(btc_data["change_24h"])
    trending_line = (
        f"Today's hottest Binance mover: {trending_context.strip()}. "
        "If it fits naturally, let the joke nod to that move; never claim a position in it.\n"
        if str(trending_context or "").strip()
        else ""
    )

    system_prompt = """
You are a witty, highly relatable crypto trader on Binance Square known for viral trading psychology observations, memes, and community culture commentary.
Your posts highlight the funny, painful, and relatable realities of crypto trading (FOMO, leverage addiction, holding through dips, revenge trading vs discipline, 1-minute chart staring).
Generate an ultra-relatable, engaging, humorous post that sparks immense community laughter, likes, and comment agreement.
Return exactly one valid JSON object and no prose outside that JSON object.
""".strip()

    user_prompt = f"""
Current Market Context: Bitcoin at ${btc_price:,.0f} ({btc_change:+.1f}% 24H).
{trending_line}
Return exactly this JSON structure:
{{
  "title": "A short situation headline for explicitly fictional trading humor, not a personal trading diary",
  "content": "2-4 lines of witty, super-relatable trading humor / meme format (e.g. comparison, expectation vs reality, trader thoughts at 3 AM).",
  "lesson": "One short witty or real risk-management takeaway.",
  "question": "A fun, natural question for the community (e.g. 'Who else did this today? Be honest 👇')"
}}

Rules:
1. Make it genuine, relatable, and funny for real crypto futures and spot traders.
2. Avoid generic corporate speak.
3. This is fictional humor, not a personal diary. Never state or imply a real position, entry or P&L. Avoid claims about the author's actual day or account.
4. Return JSON only.
""".strip()

    client = Groq(api_key=api_key)
    candidates = _groq_model_candidates(api_key)
    failures: list[str] = []

    for model in candidates:
        print(f"[Groq] Generating meme post with model: {model}")
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                **_groq_chat_kwargs(model, 4096),
            )
            result = _parse_json_object(completion.choices[0].message.content)

            meme_data = {
                "title": str(result.get("title", "Crypto Trader Psychology 101:")).strip(),
                "content": str(result.get("content", "Me: 'I will strictly follow my 1:3 R:R trading plan today.'\nAlso me 5 minutes after seeing a 1M green candle: 'Market order 50x!'")).strip(),
                "lesson": str(result.get("lesson", "Discipline is what separates traders from gamblers.")).strip(),
                "question": str(result.get("question", "Be honest: Who relates to this today? 👇")).strip(),
                "groq_model": model,
                "type": "meme_community",
            }
            print(f"[Groq] Meme post validated with {model}")
            return meme_data

        except Exception as exc:
            if _is_authentication_error(exc):
                raise RuntimeError("GROQ_API_KEY authentication failed") from exc

            failure = f"{model}: {type(exc).__name__}: {exc}"
            failures.append(failure)
            print(f"[Groq Warning] {failure}")

    summary = " | ".join(failures) if failures else "no candidate model was attempted"
    raise RuntimeError(f"All Groq meme post models failed: {summary}")


def generate_explainer_with_groq(top_coins: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Generate a data-driven educational explainer (manual ``educational_explainer`` slot).

    Evergreen explainers are a proven reach-broadening pillar in the creator
    research. This bot's edge is verified market plumbing (MTF trend stacks,
    volume splits, funding/OI when available, liquidation zones), so the
    explainer teaches one of those concepts *using today's measured numbers*
    as the worked example. Nothing is invented: unavailable feeds must be
    labelled, and the concept is chosen from what the data actually shows.
    """

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found in GitHub Secrets.")

    btc_data = get_24h_data("BTCUSDT")
    eth_data = get_24h_data("ETHUSDT")
    btc_price = float(btc_data["price"])
    btc_change = float(btc_data["change_24h"])

    if not top_coins:
        try:
            top_coins = get_trending_coins(top_n=5, min_change=2.0, min_volume=3_000_000)
        except Exception:
            top_coins = []

    try:
        btc_klines_mtf = get_klines_multi_timeframe("BTCUSDT")
    except Exception as exc:
        print(f"[Explainer Warning] BTC multi-timeframe data unavailable: {exc}")
        btc_klines_mtf = {frame: [] for frame in ("5m", "15m", "1h", "4h", "1d", "1w")}

    btc_indicators_1h = calculate_all_indicators(btc_klines_mtf.get("1h", []))
    try:
        btc_funding = get_funding_rate("BTCUSDT")
    except Exception:
        btc_funding = None
    try:
        btc_oi = get_open_interest("BTCUSDT")
    except Exception:
        btc_oi = None

    try:
        context = get_market_context(btc_data, eth_data, top_coins or [])
    except Exception as exc:
        print(f"[Explainer Warning] Market context unavailable: {exc}")
        context = {}

    research = {
        "btc": {
            "price": btc_price,
            "change_24h": btc_change,
            "indicators_1h": btc_indicators_1h,
            "funding_rate": btc_funding,
            "open_interest": btc_oi,
        },
        "top_movers": [
            {"coin": str(c.get("coin", "?")).upper(), "change": c.get("change")}
            for c in (top_coins or [])[:5]
        ],
        "market_context": context,
    }
    research_json = json.dumps(research, ensure_ascii=False, separators=(",", ":"), default=str)

    system_prompt = """
You are a patient crypto market-structure educator on Binance Square. You teach one measurable concept per post using today's verified Binance data as the worked example. Plain English, zero hype, zero promises, no trade calls. Mark any unavailable feed as unavailable rather than guessing. Return exactly one valid JSON object, no prose outside JSON.
""".strip()

    user_prompt = f"""
Verified data snapshot (JSON):
{research_json}

Pick ONE concept that today's data illustrates well (examples: multi-timeframe trend alignment, RSI vs price divergence, volume confirmation on breakouts, funding-rate extremes, open-interest build-up, support/resistance flips, risk:reward thinking). Teach it through the measured numbers above.

Return exactly this JSON structure:
{{
  "title": "Short plain-English title, e.g. 'Reading a volume spike without fooling yourself'",
  "hook_line": "<=70 chars and at least 6 words: the curiosity gap the explainer resolves, anchored to one real number from the snapshot",
  "concept": "The concept name, 2-5 words",
  "points": ["3-5 short numbered teaching points, each <=200 chars, each citing a measured value from the snapshot where relevant"],
  "takeaway": "One sentence: the habit the reader should repeat",
  "question": "One genuine discussion question inviting readers to share how they use this concept"
}}

Rules:
1. Teach, never pitch: no entry/target/stop, no P&L, no leverage mention, no 'guaranteed'.
2. Every number must come from the snapshot; mark estimates as estimated.
3. If funding or open interest is null/unavailable in the snapshot, do not teach that concept today.
4. Return JSON only.
""".strip()

    client = Groq(api_key=api_key)
    candidates = _groq_model_candidates(api_key)
    failures: list[str] = []

    for model in candidates:
        print(f"[Groq] Generating educational explainer with model: {model}")
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.4,
                **_groq_chat_kwargs(model, 8192),
            )
            result = _parse_json_object(completion.choices[0].message.content)

            points_raw = result.get("points", [])
            points = (
                [" ".join(str(point).split()) for point in points_raw if str(point).strip()][:5]
                if isinstance(points_raw, list)
                else []
            )
            if len(points) < 3:
                points = [
                    f"BTC trades at ${btc_price:,.2f} ({btc_change:+.2f}% 24h); the 1H trend reading anchors the example.",
                    "A signal is stronger when several independent measured clues agree than when one is loud.",
                    "Check the same idea on a higher timeframe before trusting a lower-timeframe move.",
                ]

            explainer = {
                "title": _sanitize_hook_line(result.get("title", "Reading today's market structure"), max_chars=80),
                "hook_line": _sanitize_hook_line(result.get("hook_line", "")),
                "concept": _sanitize_hook_line(result.get("concept", "Market structure"), max_chars=40),
                "points": points,
                "takeaway": _sanitize_hook_line(
                    result.get("takeaway", "Verify the clue on your own chart before acting on it."),
                    max_chars=200,
                ),
                "question": _sanitize_hook_line(
                    result.get("question", "Which of these checks do you already use before entering a trade?"),
                    max_chars=160,
                ),
                "groq_model": model,
                "type": "educational_explainer",
            }
            print(f"[Groq] Educational explainer validated with {model}")
            return explainer

        except Exception as exc:
            if _is_authentication_error(exc):
                raise RuntimeError("GROQ_API_KEY authentication failed") from exc

            failure = f"{model}: {type(exc).__name__}: {exc}"
            failures.append(failure)
            print(f"[Groq Warning] {failure}")

    summary = " | ".join(failures) if failures else "no candidate model was attempted"
    raise RuntimeError(f"All Groq explainer models failed: {summary}")
