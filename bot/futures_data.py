"""Optional Binance USDⓈ-M futures context with conservative fallbacks.

Futures endpoints can be unavailable, rate-limited, or restricted in some
runtimes.  These helpers therefore return ``None`` (or explicitly-estimated
values) rather than allowing optional data to break signal generation.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any

import requests

from bot.indicators import calculate_atr
from bot.market_data import BASE_URL, _parse_kline_row

BASE_FAPI = "https://fapi.binance.com"
TIMEOUT = 10


def _symbol(symbol: str) -> str:
    value = str(symbol or "").upper().strip()
    if not value:
        raise ValueError("Symbol is required")
    if not value.endswith("USDT"):
        value += "USDT"
    if not re.fullmatch(r"[A-Z0-9]{4,30}", value):
        raise ValueError(f"Invalid futures symbol: {symbol!r}")
    return value


def _get_json(endpoint: str, params: dict[str, Any]) -> Any | None:
    try:
        response = requests.get(
            f"{BASE_FAPI}{endpoint}", params=params, timeout=TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        print(f"[Futures Warning] {endpoint} unavailable: {exc}")
        return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _spot_ticker(symbol: str) -> dict[str, Any] | None:
    """Fetch minimal spot ticker fields using the same 10-second timeout."""
    try:
        response = requests.get(
            f"{BASE_URL}/api/v3/ticker/24hr",
            params={"symbol": symbol},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("symbol") != symbol:
            return None
        price = _number(payload.get("lastPrice"))
        if price is None or price <= 0:
            return None
        return {
            "price": price,
            "quote_volume_24h": _number(payload.get("quoteVolume")),
        }
    except Exception as exc:
        print(f"[Futures Warning] Spot ticker proxy unavailable for {symbol}: {exc}")
        return None


def _spot_klines(symbol: str, interval: str = "1h", limit: int = 50) -> list[dict[str, Any]]:
    """Fetch validated spot candles with a 10-second timeout."""
    try:
        response = requests.get(
            f"{BASE_URL}/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return []
        result = []
        for row in payload:
            try:
                result.append(_parse_kline_row(row))
            except Exception:
                continue
        return result
    except Exception as exc:
        print(f"[Futures Warning] Spot candles unavailable for {symbol}: {exc}")
        return []


def get_open_interest(symbol: str) -> dict[str, Any] | None:
    """Fetch contract open interest and a spot-price-derived USDT notional."""
    try:
        normalized = _symbol(symbol)
        payload = _get_json("/fapi/v1/openInterest", {"symbol": normalized})
        if not isinstance(payload, dict):
            return None
        amount = _number(payload.get("openInterest"))
        if amount is None or amount < 0:
            return None

        notional = _number(payload.get("openInterestValue"))
        estimated = notional is None
        if notional is None:
            ticker = _spot_ticker(normalized)
            if ticker is not None:
                notional = amount * float(ticker["price"])
        return {
            "open_interest": amount,
            "open_interest_usdt": notional,
            "estimated": estimated,
            "source": "Binance USDⓈ-M open-interest endpoint",
        }
    except Exception as exc:
        print(f"[Futures Warning] Open interest unavailable for {symbol}: {exc}")
        return None


def get_funding_rate(symbol: str) -> dict[str, Any] | None:
    """Fetch the latest funding rate, annualized simple-rate, and payer bias."""
    try:
        normalized = _symbol(symbol)
        payload = _get_json("/fapi/v1/premiumIndex", {"symbol": normalized})
        funding = _number(payload.get("lastFundingRate")) if isinstance(payload, dict) else None
        source = "premiumIndex"
        if funding is None:
            history = _get_json(
                "/fapi/v1/fundingRate", {"symbol": normalized, "limit": 1}
            )
            if isinstance(history, list) and history:
                funding = _number(history[-1].get("fundingRate")) if isinstance(history[-1], dict) else None
                source = "fundingRate"
        if funding is None:
            return None
        threshold = 0.00001
        if funding > threshold:
            bias = "long_pays_short"
        elif funding < -threshold:
            bias = "short_pays_long"
        else:
            bias = "neutral"
        return {
            "funding_rate": funding,
            "annualized": funding * 3.0 * 365.0 * 100.0,
            "bias": bias,
            "source": source,
        }
    except Exception as exc:
        print(f"[Futures Warning] Funding rate unavailable for {symbol}: {exc}")
        return None


def get_long_short_ratio(symbol: str) -> dict[str, Any] | None:
    """Fetch the latest global long/short account ratio for a perpetual pair."""
    try:
        normalized = _symbol(symbol)
        payload = _get_json(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": normalized, "period": "1h", "limit": 1},
        )
        if not isinstance(payload, list) or not payload or not isinstance(payload[-1], dict):
            return None
        ratio = _number(payload[-1].get("longShortRatio"))
        if ratio is None or ratio < 0:
            return None
        total = 1.0 + ratio
        return {
            "long_short_ratio": ratio,
            "long_pct": (ratio / total) * 100.0 if total else 50.0,
            "short_pct": (1.0 / total) * 100.0 if total else 50.0,
            "source": "Binance global account ratio",
        }
    except Exception as exc:
        print(f"[Futures Warning] Long/short ratio unavailable for {symbol}: {exc}")
        return None


def get_liquidation_estimate(symbol: str) -> dict[str, Any] | None:
    """Read force orders when exposed, otherwise estimate liquidation exposure.

    Binance may require authorization for the force-order history endpoint.  Any
    fallback is explicitly labelled as an estimate and is based on open
    interest, 24h turnover, and ATR-derived volatility rather than a live map.
    """
    try:
        normalized = _symbol(symbol)
        now_ms = int(time.time() * 1000)
        payload = _get_json(
            "/fapi/v1/allForceOrders",
            {
                "symbol": normalized,
                "startTime": now_ms - 24 * 60 * 60 * 1000,
                "endTime": now_ms,
                "limit": 100,
            },
        )
        if isinstance(payload, list):
            long_liquidations = 0.0
            short_liquidations = 0.0
            for order in payload:
                if not isinstance(order, dict):
                    continue
                price = _number(order.get("avgPrice")) or _number(order.get("price")) or 0.0
                quantity = (
                    _number(order.get("executedQty"))
                    or _number(order.get("origQty"))
                    or 0.0
                )
                notional = max(0.0, price * quantity)
                # Forced SELL closes longs; forced BUY closes shorts.
                if str(order.get("side", "")).upper() == "SELL":
                    long_liquidations += notional
                elif str(order.get("side", "")).upper() == "BUY":
                    short_liquidations += notional
            return {
                "long_liquidations_24h_usdt": long_liquidations,
                "short_liquidations_24h_usdt": short_liquidations,
                "estimated": False,
                "source": "Binance allForceOrders response",
            }

        open_interest = get_open_interest(normalized)
        ticker = _spot_ticker(normalized) or {}
        candles = _spot_klines(normalized, interval="1h", limit=50)
        atr = calculate_atr(candles)
        price = _number(ticker.get("price")) or (
            float(candles[-1]["close"]) if candles else 0.0
        )
        oi_usdt = _number(open_interest.get("open_interest_usdt")) if open_interest else None
        quote_volume = _number(ticker.get("quote_volume_24h")) or 0.0
        atr_fraction = atr / price if price > 0 else 0.0
        exposure_fraction = min(0.15, max(0.01, atr_fraction * 2.0))
        base_exposure = oi_usdt if oi_usdt is not None else quote_volume
        total_estimate = max(0.0, base_exposure * exposure_fraction)
        if total_estimate <= 0:
            return None
        ratio = get_long_short_ratio(normalized)
        long_pct = float(ratio["long_pct"]) / 100.0 if ratio else 0.5
        short_pct = float(ratio["short_pct"]) / 100.0 if ratio else 0.5
        return {
            "long_liquidations_24h_usdt": total_estimate * long_pct,
            "short_liquidations_24h_usdt": total_estimate * short_pct,
            "estimated": True,
            "source": "OI/24h volume x ATR-volatility proxy",
            "atr_percent": atr_fraction * 100.0,
        }
    except Exception as exc:
        print(f"[Futures Warning] Liquidation estimate unavailable for {symbol}: {exc}")
        return None


def get_futures_vs_spot_volume(symbol: str) -> dict[str, Any] | None:
    """Compare USDⓈ-M futures quote turnover with verified spot turnover."""
    try:
        normalized = _symbol(symbol)
        futures = _get_json("/fapi/v1/ticker/24hr", {"symbol": normalized})
        futures_volume = _number(futures.get("quoteVolume")) if isinstance(futures, dict) else None
        spot = _spot_ticker(normalized)
        spot_volume = _number(spot.get("quote_volume_24h")) if isinstance(spot, dict) else None
        if futures_volume is None or spot_volume is None or spot_volume < 0:
            return None
        ratio = futures_volume / spot_volume if spot_volume > 0 else None
        if ratio is None:
            bias = "spot_dominant"
        elif ratio > 1.10:
            bias = "futures_dominant"
        elif ratio < (1.0 / 1.10):
            bias = "spot_dominant"
        else:
            bias = "balanced"
        return {
            "futures_quote_volume": futures_volume,
            "spot_quote_volume": spot_volume,
            "futures_to_spot_ratio": ratio,
            "bias": bias,
        }
    except Exception as exc:
        print(f"[Futures Warning] Futures/spot volume unavailable for {symbol}: {exc}")
        return None


def get_liquidation_heatmap_levels(
    symbol: str,
    klines: list[dict[str, Any]],
    *,
    open_interest_data: dict[str, Any] | None = None,
    long_short_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate nearby liquidation/liquidity zones from swings, OI, and ATR."""
    try:
        candles: list[dict[str, float]] = []
        for row in klines or []:
            if not isinstance(row, dict):
                continue
            try:
                candle = {
                    key: float(row[key])
                    for key in ("high", "low", "close", "volume")
                }
                if not all(math.isfinite(value) for value in candle.values()):
                    continue
                if candle["high"] < candle["low"] or candle["close"] <= 0:
                    continue
                candles.append(candle)
            except (TypeError, ValueError, KeyError, OverflowError):
                continue
        if not candles:
            return {
                "long_liquidation_zone": None,
                "short_liquidation_zone": None,
                "magnet_price": None,
                "clusters": [],
                "estimated": True,
            }

        close = candles[-1]["close"]
        atr = calculate_atr(klines)
        if not math.isfinite(atr) or atr <= 0:
            recent_range = max(row["high"] for row in candles[-20:]) - min(
                row["low"] for row in candles[-20:]
            )
            atr = max(recent_range / max(min(len(candles), 20), 1), close * 0.005)

        oi = open_interest_data or get_open_interest(symbol)
        oi_usdt = _number(oi.get("open_interest_usdt")) if isinstance(oi, dict) else None
        estimated_notional = max(0.0, oi_usdt or 0.0) * 0.05
        ratio = long_short_data or get_long_short_ratio(symbol)
        long_share = (
            float(ratio.get("long_pct", 50.0)) / 100.0
            if isinstance(ratio, dict)
            else 0.5
        )
        short_share = (
            float(ratio.get("short_pct", 50.0)) / 100.0
            if isinstance(ratio, dict)
            else 0.5
        )

        long_zone = {
            "lower": max(close - 2.0 * atr, close * 0.5),
            "upper": max(close - atr, close * 0.5),
            "mid": max(close - 1.5 * atr, close * 0.5),
        }
        short_zone = {
            "lower": close + atr,
            "upper": close + 2.0 * atr,
            "mid": close + 1.5 * atr,
        }
        recent = candles[-50:]
        swing_lows, swing_highs = [], []
        for index in range(1, len(recent) - 1):
            if recent[index]["low"] <= recent[index - 1]["low"] and recent[index]["low"] <= recent[index + 1]["low"]:
                swing_lows.append(recent[index]["low"])
            if recent[index]["high"] >= recent[index - 1]["high"] and recent[index]["high"] >= recent[index + 1]["high"]:
                swing_highs.append(recent[index]["high"])
        support_candidates = [value for value in swing_lows if value < close]
        resistance_candidates = [value for value in swing_highs if value > close]
        long_price = max(support_candidates) if support_candidates else long_zone["mid"]
        short_price = min(resistance_candidates) if resistance_candidates else short_zone["mid"]
        magnet = min((long_price, short_price), key=lambda value: abs(value - close))
        clusters = [
            {
                "side": "long_liquidations",
                "price": long_price,
                "estimated_notional_usdt": estimated_notional * long_share,
                "estimated": True,
            },
            {
                "side": "short_liquidations",
                "price": short_price,
                "estimated_notional_usdt": estimated_notional * short_share,
                "estimated": True,
            },
        ]
        return {
            "long_liquidation_zone": long_zone,
            "short_liquidation_zone": short_zone,
            "magnet_price": magnet,
            "clusters": clusters,
            "estimated": True,
            "atr": atr,
        }
    except Exception as exc:
        print(f"[Futures Warning] Liquidation heatmap unavailable for {symbol}: {exc}")
        return {
            "long_liquidation_zone": None,
            "short_liquidation_zone": None,
            "magnet_price": None,
            "clusters": [],
            "estimated": True,
            "error": str(exc),
        }
