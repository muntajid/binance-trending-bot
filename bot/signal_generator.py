"""Verified Binance market scanning and resilient Groq signal, macro overview, and meme generation."""

from __future__ import annotations

import json
import math
import os
from typing import Any

import requests
from groq import Groq

from bot.indicators import calculate_all_indicators
from bot.market_context import get_market_context
from bot.market_data import (
    calculate_volume_analysis,
    get_24h_data,
    get_klines_multi_timeframe,
    get_orderbook,
    get_ticker_24h_all,
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


def get_live_price(coin: str) -> float:
    """Get the authoritative live price from the verified market-data layer."""

    symbol = f"{str(coin).upper()}USDT"
    data = get_24h_data(symbol)

    if not data.get("verified"):
        raise RuntimeError(f"Market data not verified for {symbol}")

    price = float(data["price"])
    if price <= 0:
        raise RuntimeError(f"Invalid verified price for {symbol}: {price}")

    print(f"[Live Price - VERIFIED] {symbol} = ${price}")
    return price


def get_trending_coins(
    top_n: int = 30,
    min_change: float = 5.0,
    min_volume: float = 5_000_000,
) -> list[dict[str, Any]]:
    """Return positive-momentum USDT markets from verified Binance data."""

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
            if symbol in excluded_symbols:
                continue
            if any(marker in symbol for marker in leveraged_suffixes):
                continue

            change = float(item["priceChangePercent"])
            volume = float(item["quoteVolume"])
            price = float(item["lastPrice"])

            if price <= 0 or change < min_change or volume < min_volume:
                continue

            filtered.append(
                {
                    "coin": symbol[:-4],
                    "change": change,
                    "volume": volume,
                    "price": price,
                }
            )
        except (KeyError, TypeError, ValueError):
            continue

    filtered.sort(key=lambda item: item["change"], reverse=True)
    if not filtered:
        raise RuntimeError("No valid trending coins found from verified API.")

    return filtered[:top_n]


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
) -> dict[str, Any]:
    """Apply authoritative values and reject impossible TP/SL ordering."""

    direction = str(result.get("direction", "LONG")).upper().strip()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"Unsupported signal direction: {direction}")

    try:
        tp1 = float(result["tp1"])
        tp2 = float(result["tp2"])
        stop = float(result["sl"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Groq signal is missing numeric TP1, TP2, or SL") from exc

    if min(live_price, tp1, tp2, stop) <= 0:
        raise ValueError("Signal prices must all be greater than zero")

    if direction == "LONG" and not (stop < live_price < tp1 < tp2):
        raise ValueError("Invalid LONG levels: expected SL < Entry < TP1 < TP2")
    if direction == "SHORT" and not (tp2 < tp1 < live_price < stop):
        raise ValueError("Invalid SHORT levels: expected TP2 < TP1 < Entry < SL")

    def safe_text(field: str, fallback: str) -> str:
        value = result.get(field)
        text = str(value).strip() if value is not None else ""
        return text or fallback

    why_now = safe_text(
        "why_now",
        f"Price broke above key 1H resistance with {change:+.1f}% 24H change; confirm with live volume.",
    )
    invalidation = safe_text(
        "invalidation", f"A 1H candle close beyond ${stop} invalidates this setup."
    )
    rapid_reason = safe_text(
        "rapid_reason", "1H trigger unavailable; wait for price confirmation."
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
        "leverage": "15x",
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
        "risk_management": safe_field(
            "risk_management",
            {
                "entry": live_price,
                "tp1": tp1,
                "tp2": tp2,
                "stop_loss": stop,
                "risk_reward_tp1": abs(tp1 - live_price) / abs(live_price - stop),
                "guidance": "Position size must be based on a predefined account-risk limit.",
            },
        ),
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
) -> dict[str, Any]:
    """Generate one validated signal with Why Now and Invalidation fields."""

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found in GitHub Secrets.")

    supplied_price = float(price)
    if supplied_price <= 0:
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
    research_json = json.dumps(
        institutional_research,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )

    system_prompt = """
You are an institutional crypto market analyst. Base the trade thesis on the verified Binance price and supplied spot, OHLCV, order-book, futures, and macro-context evidence. The 15 numbered observations are measurements, not guarantees; distinguish measured data from estimated proxies and explicitly say when a feed is unavailable. Never invent on-chain flows, token events, macro releases, order execution, or a current price. Never claim that an exchange order was executed. Select LONG or SHORT only when the evidence supports a defined-risk setup; otherwise use conservative confidence and clearly state the risk. Return exactly one valid JSON object and no prose outside that JSON object.
""".strip()

    user_prompt = f"""
Coin: {str(coin).upper()}
Verified Binance live price (Entry CMP): {live_price}
24-hour change: {float(change):.2f}%
Context market leader: {str(trending_bonus).upper()}

Fifteen-category institutional research snapshot (JSON):
{research_json}

Use only the verified live price above as Entry CMP. Analyze EMA20/50/200, RSI, MACD, VWAP, Bollinger Bands, Stoch RSI, volume-spike/breakout confirmation, futures open interest/funding/long-short ratio, order-book imbalance, liquidation heatmap zones, and 5m/15m/1H/4H/1D/1W trend alignment. Include BTC dominance/trend, ETH/BTC, altcoin-vs-BTC return breadth, fear/greed, estimated social/on-chain proxies, ATR volatility, and supplied macro context for Fed/CPI/DXY. If a feed is absent, say unavailable; never infer its direction. Mark every estimate/proxy as estimated. Do not treat a volume/dominance proxy as market capitalization, or a proxy as actual on-chain activity. Macro feeds not present in the snapshot (including live Fed, CPI, or DXY data) must be labelled unavailable, not guessed.

Return one valid JSON object with every required key below. Keep prose fields concise, put numeric/structured evidence in the matching objects, and use null/"unavailable" where the snapshot has no evidence:
{{
  "coin": "{str(coin).upper()}",
  "trending_bonus": "{str(trending_bonus).upper()}",
  "direction": "LONG or SHORT",
  "entry": "CMP",
  "entry_price": {live_price},
  "tp1": null,
  "tp2": null,
  "sl": null,
  "confidence": null,
  "rapid_reason": "Concise verified 1H trigger and main risk.",
  "why_now": "Evidence-based setup-specific trigger.",
  "invalidation": "One clear price/close level that invalidates the thesis.",
  "smc_logic": "2-3 concise evidence-based sentences.",
  "smc_logic_short": "Short factual chart annotation.",
  "technical_analysis": "Structure, trend, confluence, and opposing evidence.",
  "indicators_summary": "EMA, RSI, MACD, VWAP, BB, and Stoch RSI readout.",
  "volume_analysis": {{"buying_volume": null, "selling_volume": null, "volume_spike": false, "breakout_volume_confirmed": false, "volume_divergence": "unavailable", "estimated": null}},
  "futures_data": {{"open_interest": null, "funding_rate": null, "long_short_ratio": null, "futures_vs_spot_volume": null, "estimated": null}},
  "liquidity_analysis": {{"orderbook_imbalance": "unavailable", "bid_depth_usdt": null, "ask_depth_usdt": null, "estimated": null}},
  "liquidation_heatmap": {{"long_liquidation_zone": null, "short_liquidation_zone": null, "magnet_price": null, "clusters": [], "estimated": true}},
  "mtf_analysis": {{"5m": "unavailable", "15m": "unavailable", "1h": "unavailable", "4h": "unavailable", "1d": "unavailable", "1w": "unavailable"}},
  "market_context": {{"btc_dominance": "unavailable", "bitcoin_trend": "unavailable", "eth_btc_ratio": null, "altcoin_btc_correlation": "unavailable"}},
  "sentiment_onchain": {{"fear_greed": "unavailable", "social_sentiment": "estimated/unavailable", "on_chain_proxy": "estimated/unavailable"}},
  "volatility_assessment": {{"atr": null, "atr_percent": null, "bollinger_bandwidth": null, "estimated": false}},
  "risk_management": {{"risk_reward": "...", "position_sizing": "...", "invalidation_plan": "..."}},
  "validation_score": null,
  "macro_factors": {{"fed_rate_context": "unavailable", "cpi_context": "unavailable", "dxy_proxy": "unavailable", "treasury_context": "unavailable"}},
  "fundamental_note": "Review possible token unlock/listing/vesting/delisting risk; label remembered or unverified events as unconfirmed and give no unsupported dates.",
  "analysis_summary": "One concise institutional readout.",
  "risk_factors": ["Evidence-based risk", "Evidence-based risk"]
}}

Rules:
1. entry_price must equal {live_price} exactly; never use a chart close as CMP.
2. For LONG, enforce SL < entry_price < TP1 < TP2. For SHORT, enforce TP2 < TP1 < entry_price < SL.
3. Use numeric entry_price, TP1, TP2, and SL. Do not reverse or relax these inequalities.
4. Use supplied support/resistance and ATR when available; lower confidence if feeds are missing or timeframes conflict.
5. Do not invent a current price, macro release, confirmed token event, exchange flow, or whale transaction. Any remembered token-event concern must be labelled unverified and have no unsupported date.
6. Mark estimates as estimated and state which source/proxy supports them.
7. Return JSON only.
""".strip()

    client = Groq(api_key=api_key)
    candidates = _groq_model_candidates(api_key)
    failures: list[str] = []

    for model in candidates:
        print(f"[Groq] Generating signal with model: {model}")
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=1500,
                response_format={"type": "json_object"},
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
            )
            normalized["institutional_analysis"] = institutional_research
            normalized["analysis_summary"] = str(
                result.get("analysis_summary", "15-point analysis attached from available market data.")
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

        except Exception as exc:
            if _is_authentication_error(exc):
                raise RuntimeError("GROQ_API_KEY authentication failed") from exc

            failure = f"{model}: {type(exc).__name__}: {exc}"
            failures.append(failure)
            print(f"[Groq Warning] {failure}")

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

Use the supplied BTC and ETH 1H/4H RSI and EMA20/50/200 readings in the thesis whenever present. Explicitly incorporate BTC dominance and Fear & Greed in the market pulse; if either is estimated, label it estimated. Do not invent technical readings or macro data. Keep the existing overview output structure.

Return exactly this JSON structure:
{{
  "headline": "Punchy market headline summarizing BTC structure and current market pulse",
  "btc_price": {btc_price},
  "btc_change": {btc_change},
  "market_phase": "e.g. Bullish Expansion / Consolidation / Key Resistance Retest / Liquidity Hunt",
  "btc_thesis": "1-2 sentences on Bitcoin 1H/4H price action, structure holding, and liquidity zones.",
  "altcoin_summary": "1-2 sentences explaining altcoin capital rotation and momentum leaders.",
  "btc_support": <numeric nearby key support level below btc_price>,
  "btc_resistance": <numeric nearby key resistance level above btc_price>,
  "strategy_outlook": "One clear, actionable rule/insight for spot and futures traders today."
}}

Rules:
1. btc_price must equal {btc_price}.
2. btc_support < btc_price < btc_resistance.
3. Keep the tone professional, objective, and analytical.
4. Return JSON only.
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
                max_tokens=900,
                response_format={"type": "json_object"},
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


def generate_meme_post_with_groq() -> dict[str, Any]:
    """Generate high-engagement crypto trader psychology / community culture meme post."""

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found in GitHub Secrets.")

    btc_data = get_24h_data("BTCUSDT")
    btc_price = float(btc_data["price"])
    btc_change = float(btc_data["change_24h"])

    system_prompt = """
You are a witty, highly relatable crypto trader on Binance Square known for viral trading psychology observations, memes, and community culture commentary.
Your posts highlight the funny, painful, and relatable realities of crypto trading (FOMO, leverage addiction, holding through dips, revenge trading vs discipline, 1-minute chart staring).
Generate an ultra-relatable, engaging, humorous post that sparks immense community laughter, likes, and comment agreement.
Return exactly one valid JSON object and no prose outside that JSON object.
""".strip()

    user_prompt = f"""
Current Market Context: Bitcoin at ${btc_price:,.0f} ({btc_change:+.1f}% 24H).

Return exactly this JSON structure:
{{
  "title": "Short punchy meme headline or situation title (e.g. 'Trader Diary:', 'My Portfolio when:', 'The 3 Stages of a Leverage Trader:')",
  "content": "2-4 lines of witty, super-relatable trading humor / meme format (e.g. comparison, expectation vs reality, trader thoughts at 3 AM).",
  "lesson": "One short witty or real risk-management takeaway.",
  "question": "A fun, natural question for the community (e.g. 'Who else did this today? Be honest 👇')"
}}

Rules:
1. Make it genuine, relatable, and funny for real crypto futures and spot traders.
2. Avoid generic corporate speak.
3. Return JSON only.
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
                max_tokens=600,
                response_format={"type": "json_object"},
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
