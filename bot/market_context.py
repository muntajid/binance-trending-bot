"""Graceful macro, sentiment, and activity context for signal analysis.

This module does not claim to provide exchange-flow, social, or on-chain truth.
Where those feeds are unavailable, values are clearly marked as proxies and all
external requests are optional.
"""

from __future__ import annotations

import math
from typing import Any

import requests

from bot.futures_data import get_open_interest
from bot.indicators import calculate_all_indicators
from bot.market_data import (
    get_btc_dominance_context,
    get_klines_multi_timeframe,
    get_orderbook,
)

FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _market_price(data: Any) -> float:
    if isinstance(data, dict):
        for key in ("price", "lastPrice", "close"):
            value = _number(data.get(key), 0.0)
            if value > 0:
                return value
    return 0.0


def _market_change(data: Any) -> float:
    if isinstance(data, dict):
        for key in ("change_24h", "priceChangePercent", "change"):
            if key in data:
                return _number(data.get(key), 0.0)
    return 0.0


def _fear_greed(
    btc_change: float, eth_change: float, btc_volume: float = 0.0
) -> dict[str, Any]:
    try:
        response = requests.get(FEAR_GREED_URL, timeout=10)
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        if rows and isinstance(rows[0], dict):
            value = int(rows[0].get("value"))
            if 0 <= value <= 100:
                return {
                    "value": value,
                    "classification": str(rows[0].get("value_classification", "Unknown")),
                    "estimated": False,
                    "source": "alternative.me Fear & Greed Index",
                }
    except Exception as exc:
        print(f"[Market Context Warning] Fear & Greed feed unavailable: {exc}")

    # A deliberately coarse change-plus-turnover proxy, not a substitute for
    # the published index. Absolute turnover is only a weak activity signal.
    volume_boost = min(
        5.0,
        max(0.0, math.log10(1.0 + max(btc_volume, 0.0) / 1_000_000_000.0) * 2.0),
    )
    volume_direction = 1.0 if btc_change >= 0 else -1.0
    estimate = int(
        round(
            max(
                0.0,
                min(100.0, 50.0 + 2.0 * btc_change + eth_change + volume_direction * volume_boost),
            )
        )
    )
    if estimate <= 24:
        label = "Extreme Fear"
    elif estimate <= 44:
        label = "Fear"
    elif estimate <= 55:
        label = "Neutral"
    elif estimate <= 74:
        label = "Greed"
    else:
        label = "Extreme Greed"
    return {
        "value": estimate,
        "classification": label,
        "estimated": True,
        "source": "BTC/ETH 24h price-change plus BTC turnover proxy",
    }


def _bitcoin_trend(
    btc_data: Any,
) -> tuple[str, dict[str, Any]]:
    supplied_frames = btc_data.get("klines_mtf") if isinstance(btc_data, dict) else None
    try:
        frames = supplied_frames if isinstance(supplied_frames, dict) else get_klines_multi_timeframe("BTCUSDT")
    except Exception as exc:
        print(f"[Market Context Warning] BTC trend klines unavailable: {exc}")
        frames = {}

    details: dict[str, Any] = {}
    labels: list[str] = []
    for interval in ("1h", "4h"):
        candles = frames.get(interval, []) if isinstance(frames, dict) else []
        indicators = calculate_all_indicators(candles)
        close = _number(indicators.get("close"))
        ema20 = _number(indicators.get("ema_20"))
        ema50 = _number(indicators.get("ema_50"))
        structure = str(indicators.get("market_structure", "insufficient data"))
        if close > 0 and ema20 > 0 and ema50 > 0:
            if close > ema20 > ema50:
                label = "bullish"
            elif close < ema20 < ema50:
                label = "bearish"
            else:
                label = "mixed"
        elif "bullish" in structure.lower():
            label = "bullish"
        elif "bearish" in structure.lower():
            label = "bearish"
        else:
            label = "unavailable"
        labels.append(label)
        details[interval] = {
            "trend": label,
            "close": close,
            "ema_20": ema20,
            "ema_50": ema50,
            "market_structure": structure,
        }

    available = [label for label in labels if label != "unavailable"]
    if len(available) == 2 and available[0] == available[1]:
        overall = f"{available[0]} alignment on 1H and 4H"
    elif available:
        overall = f"mixed 1H/4H alignment ({', '.join(labels)})"
    else:
        overall = "unavailable; BTC price action not verified on higher timeframes"
    return overall, details


def get_market_context(
    btc_data: dict[str, Any] | None,
    eth_data: dict[str, Any] | None,
    trending_coins: list[dict[str, Any]] | None,
    coin_symbol: str | None = None,
) -> dict[str, Any]:
    """Aggregate optional market context without making it a pipeline dependency."""
    btc_data = btc_data if isinstance(btc_data, dict) else {}
    eth_data = eth_data if isinstance(eth_data, dict) else {}
    trending = trending_coins if isinstance(trending_coins, list) else []
    btc_price = _market_price(btc_data)
    eth_price = _market_price(eth_data)
    btc_change = _market_change(btc_data)
    eth_change = _market_change(eth_data)
    btc_volume = _number(
        btc_data.get("quote_volume_24h", btc_data.get("quoteVolume")), 0.0
    )

    try:
        dominance = get_btc_dominance_context()
    except Exception as exc:
        print(f"[Market Context Warning] BTC dominance unavailable: {exc}")
        dominance = {
            "btc_dominance_percent": None,
            "total_mcap_proxy": None,
            "source": "unavailable",
            "estimated": True,
        }

    try:
        bitcoin_trend, bitcoin_trend_detail = _bitcoin_trend(btc_data)
    except Exception as exc:
        print(f"[Market Context Warning] BTC trend unavailable: {exc}")
        bitcoin_trend = "unavailable"
        bitcoin_trend_detail = {}

    alt_changes: list[float] = []
    for item in trending:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("coin", item.get("symbol", ""))).upper()
        if symbol in {"BTC", "BTCUSDT", "ETH", "ETHUSDT"}:
            continue
        value = item.get("change", item.get("priceChangePercent"))
        try:
            change = float(value)
            if math.isfinite(change):
                alt_changes.append(change)
        except (TypeError, ValueError, OverflowError):
            continue
    avg_alt_change = sum(alt_changes) / len(alt_changes) if alt_changes else 0.0
    relative = avg_alt_change - btc_change
    if not alt_changes:
        breadth = "unavailable"
    elif relative > 1.0:
        breadth = "altcoins outperforming BTC"
    elif relative < -1.0:
        breadth = "BTC outperforming altcoins"
    else:
        breadth = "broad market moving similarly"
    correlation_context = {
        "average_alt_change_pct": avg_alt_change if alt_changes else None,
        "btc_change_pct": btc_change,
        "relative_performance_pct": relative if alt_changes else None,
        "breadth": breadth,
        "estimated": True,
        "method": "cross-sectional 24h return comparison; not a statistical correlation",
    }

    fear_greed = _fear_greed(btc_change, eth_change, btc_volume)
    if btc_change > 1.0 and eth_change > 0.0:
        social = "estimated positive tone from BTC/ETH 24h price action; social feeds not queried"
    elif btc_change < -1.0 and eth_change < 0.0:
        social = "estimated cautious tone from BTC/ETH 24h price action; social feeds not queried"
    else:
        social = "estimated mixed tone from BTC/ETH 24h price action; social feeds not queried"

    risk_context = (
        f"BTC ${btc_price:,.2f} ({btc_change:+.2f}%) and ETH ${eth_price:,.2f} "
        f"({eth_change:+.2f}%) over 24h; macro releases and rates were not fetched"
    )
    macro_factors = {
        "fed_rate_context": f"No live Fed-rate feed. Interpret policy risk conditionally alongside {risk_context}.",
        "cpi_context": f"No live CPI/release feed; do not assert an inflation catalyst ({risk_context}).",
        "dxy_proxy": f"DXY was not fetched; use BTC/ETH relative momentum only as a risk-appetite proxy ({risk_context}).",
        "treasury_context": f"Treasury yields were not fetched; avoid asserting a yield catalyst ({risk_context}).",
    }

    try:
        btc_orderbook = get_orderbook("BTCUSDT", limit=20)
    except Exception as exc:
        print(f"[Market Context Warning] BTC order book unavailable: {exc}")
        btc_orderbook = {"imbalance": "unavailable", "bid_depth_usdt": 0.0, "ask_depth_usdt": 0.0}
    try:
        btc_oi = get_open_interest("BTCUSDT")
    except Exception as exc:
        print(f"[Market Context Warning] BTC open interest unavailable: {exc}")
        btc_oi = None

    if btc_change > 0 and btc_volume > 0:
        exchange_flow = "positive-price/turnover risk-on proxy; not actual wallet flows"
    elif btc_change < 0 and btc_volume > 0:
        exchange_flow = "negative-price/turnover distribution-risk proxy; not actual wallet flows"
    else:
        exchange_flow = "neutral or insufficient exchange-volume proxy; not actual wallet flows"
    oi_value = _number(btc_oi.get("open_interest_usdt")) if isinstance(btc_oi, dict) else 0.0
    whale_activity = (
        f"large-order-book proxy is {btc_orderbook.get('imbalance', 'unavailable')} "
        f"(bid depth ${_number(btc_orderbook.get('bid_depth_usdt')):,.0f}, "
        f"ask depth ${_number(btc_orderbook.get('ask_depth_usdt')):,.0f}); not verified whale flow"
    )
    active_addresses = (
        f"not available on-chain; Binance BTC 24h quote turnover ${btc_volume:,.0f} "
        f"and OI notional ${oi_value:,.0f} are activity proxies"
    )
    on_chain_proxy = {
        "exchange_flow_bias": exchange_flow,
        "whale_activity": whale_activity,
        "active_addresses_proxy": active_addresses,
        "estimated": True,
    }

    symbol = str(coin_symbol or "").upper().strip()
    fundamental_notes = (
        f"For {symbol}, token unlocks, vesting, listings, and delistings are not verified by this data feed; "
        "Groq must explicitly flag event risk as unverified rather than claim a confirmed event."
        if symbol
        else "Token unlocks, vesting, listings, and delistings are not verified by this data feed; "
        "Groq should flag event risk as unverified rather than claim a confirmed event."
    )

    return {
        "btc_price": btc_price or None,
        "btc_change_24h": btc_change,
        "eth_price": eth_price or None,
        "eth_change_24h": eth_change,
        "bitcoin_trend": bitcoin_trend,
        "bitcoin_trend_detail": bitcoin_trend_detail,
        "btc_dominance": dominance,
        "total_mcap_proxy": dominance.get("total_mcap_proxy"),
        "eth_btc_ratio": (eth_price / btc_price) if eth_price > 0 and btc_price > 0 else None,
        "altcoin_btc_correlation": correlation_context,
        "fear_greed": fear_greed,
        "social_sentiment": social,
        "macro_factors": macro_factors,
        "on_chain_proxy": on_chain_proxy,
        "fundamental_notes": fundamental_notes,
    }
