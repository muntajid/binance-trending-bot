"""Verified Binance public market-data access used by signals and monitoring."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import requests

BASE_URL = "https://data-api.binance.vision"
TIMEOUT = 15

INTERVAL_TO_MS = {
    "1m": 60_000,
    "3m": 3 * 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "2h": 2 * 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "6h": 6 * 60 * 60_000,
    "8h": 8 * 60 * 60_000,
    "12h": 12 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000,
}


class MarketDataError(Exception):
    """Raised when Binance data is unavailable or fails validation."""


def _request_json(
    endpoint: str,
    params: dict[str, Any],
    *,
    allow_empty: bool = False,
) -> Any:
    url = f"{BASE_URL}{endpoint}"

    try:
        response = requests.get(url, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MarketDataError(f"Binance request failed for {endpoint}: {exc}") from exc

    if not data and not allow_empty:
        raise MarketDataError("Binance returned empty data")

    return data


def _validate_interval(interval: str) -> str:
    interval = str(interval).strip()
    if interval not in INTERVAL_TO_MS:
        raise MarketDataError(f"Unsupported interval: {interval}")
    return interval


def _parse_kline_row(row: list[Any] | tuple[Any, ...]) -> dict[str, float | int]:
    if not isinstance(row, (list, tuple)) or len(row) < 7:
        raise MarketDataError("Invalid kline returned by Binance")

    try:
        candle: dict[str, float | int] = {
            "open_time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "close_time": int(row[6]),
        }
        # Binance kline field 9 is taker-buy base volume. Keep it optional so
        # older fixtures and nonstandard feeds remain backward compatible.
        if len(row) > 9:
            try:
                taker_buy_volume = float(row[9])
                if math.isfinite(taker_buy_volume) and 0 <= taker_buy_volume <= float(candle["volume"]):
                    candle["taker_buy_volume"] = taker_buy_volume
            except (TypeError, ValueError, OverflowError):
                pass
    except (TypeError, ValueError) as exc:
        raise MarketDataError(f"Invalid numeric value in Binance kline: {exc}") from exc

    open_price = float(candle["open"])
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])
    volume = float(candle["volume"])

    if min(open_price, high, low, close) <= 0 or volume < 0:
        raise MarketDataError("Invalid candle value detected")
    if low > high:
        raise MarketDataError("Candle low is greater than high")
    if high < max(open_price, close) or low > min(open_price, close):
        raise MarketDataError("Candle OHLC relationship is invalid")

    return candle


def get_24h_data(symbol: str) -> dict[str, Any]:
    symbol = str(symbol).upper().strip()
    if not symbol:
        raise MarketDataError("Symbol is required")

    data = _request_json(
        "/api/v3/ticker/24hr",
        {"symbol": symbol},
    )

    if not isinstance(data, dict):
        raise MarketDataError("Unexpected Binance ticker response")

    required = [
        "symbol",
        "lastPrice",
        "priceChangePercent",
        "quoteVolume",
        "closeTime",
    ]

    for field in required:
        if field not in data:
            raise MarketDataError(f"Missing Binance field: {field}")

    if data["symbol"] != symbol:
        raise MarketDataError(
            f"Symbol mismatch: expected {symbol}, received {data['symbol']}"
        )

    try:
        price = float(data["lastPrice"])
        change = float(data["priceChangePercent"])
        volume = float(data["quoteVolume"])
        close_time = int(data["closeTime"])
    except (TypeError, ValueError) as exc:
        raise MarketDataError(f"Invalid Binance ticker value: {exc}") from exc

    if price <= 0:
        raise MarketDataError(f"Invalid price: {price}")
    if volume < 0:
        raise MarketDataError(f"Invalid volume: {volume}")

    timestamp = datetime.fromtimestamp(
        close_time / 1000,
        tz=timezone.utc,
    ).isoformat()

    return {
        "symbol": symbol,
        "price": price,
        "change_24h": change,
        "quote_volume_24h": volume,
        "close_time": close_time,
        "timestamp_utc": timestamp,
        "source": BASE_URL,
        "verified": True,
    }


def get_klines(
    symbol: str,
    interval: str = "1h",
    limit: int = 120,
) -> list[dict[str, float | int]]:
    """Return the latest validated candles for a symbol."""

    symbol = str(symbol).upper().strip()
    interval = _validate_interval(interval)

    if not 1 <= int(limit) <= 1000:
        raise MarketDataError("Kline limit must be between 1 and 1000")

    data = _request_json(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": int(limit),
        },
    )

    if not isinstance(data, list):
        raise MarketDataError("Unexpected Binance kline response")

    candles = [_parse_kline_row(row) for row in data]
    if not candles:
        raise MarketDataError("No candle data returned")

    return candles


def get_klines_range(
    symbol: str,
    interval: str,
    start_time_ms: int,
    end_time_ms: int,
    *,
    max_pages: int = 50,
) -> list[dict[str, float | int]]:
    """Return all validated candles between two UTC millisecond timestamps.

    Binance limits one response to 1000 candles, so this function paginates.
    It is used by the monitor to detect a TP/SL touch that happened between
    GitHub Actions runs and then retraced before the next check.
    """

    symbol = str(symbol).upper().strip()
    interval = _validate_interval(interval)
    interval_ms = INTERVAL_TO_MS[interval]

    try:
        start = int(start_time_ms)
        end = int(end_time_ms)
        pages = int(max_pages)
    except (TypeError, ValueError) as exc:
        raise MarketDataError(f"Invalid kline-range argument: {exc}") from exc

    if not symbol:
        raise MarketDataError("Symbol is required")
    if start < 0 or end < 0 or start > end:
        raise MarketDataError("Invalid kline time range")
    if not 1 <= pages <= 100:
        raise MarketDataError("max_pages must be between 1 and 100")

    # Include the candle containing start_time.  The monitor intentionally
    # overlaps one candle between runs; state flags prevent duplicate posts.
    cursor = (start // interval_ms) * interval_ms
    candles_by_open_time: dict[int, dict[str, float | int]] = {}

    for _ in range(pages):
        data = _request_json(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end,
                "limit": 1000,
            },
            allow_empty=True,
        )

        if not data:
            break
        if not isinstance(data, list):
            raise MarketDataError("Unexpected Binance ranged-kline response")

        parsed = [_parse_kline_row(row) for row in data]
        for candle in parsed:
            open_time = int(candle["open_time"])
            if open_time <= end:
                candles_by_open_time[open_time] = candle

        last_open_time = int(parsed[-1]["open_time"])
        next_cursor = last_open_time + interval_ms

        if next_cursor <= cursor:
            raise MarketDataError("Binance kline pagination did not advance")
        if len(data) < 1000 or next_cursor > end:
            break

        cursor = next_cursor
    else:
        raise MarketDataError(
            f"Kline range exceeded {pages} pages for {symbol} {interval}"
        )

    return [candles_by_open_time[key] for key in sorted(candles_by_open_time)]


def get_ticker_24h_all() -> list[dict[str, Any]]:
    """Return all Binance Vision 24-hour ticker rows in one request."""
    data = _request_json("/api/v3/ticker/24hr", {})
    if not isinstance(data, list):
        raise MarketDataError("Unexpected Binance 24-hour ticker list")
    return [item for item in data if isinstance(item, dict)]


def get_orderbook(symbol: str, limit: int = 20) -> dict[str, Any]:
    """Return spot order-book depth and bid/ask notional imbalance."""
    empty: dict[str, Any] = {
        "bids": [],
        "asks": [],
        "bid_depth_usdt": 0.0,
        "ask_depth_usdt": 0.0,
        "imbalance_ratio": 1.0,
        "imbalance": "balanced",
        "available": False,
    }
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return empty
    try:
        depth_limit = max(5, min(int(limit), 5000))
        payload = _request_json(
            "/api/v3/depth",
            {"symbol": normalized, "limit": depth_limit},
        )
        if not isinstance(payload, dict):
            raise MarketDataError("Unexpected Binance order-book response")

        parsed_sides: dict[str, list[dict[str, float]]] = {"bids": [], "asks": []}
        for side in ("bids", "asks"):
            rows = payload.get(side, [])
            if not isinstance(rows, list):
                continue
            for row in rows:
                try:
                    price, quantity = float(row[0]), float(row[1])
                    if price <= 0 or quantity < 0 or not all(
                        math.isfinite(value) for value in (price, quantity)
                    ):
                        continue
                    parsed_sides[side].append(
                        {
                            "price": price,
                            "quantity": quantity,
                            "notional_usdt": price * quantity,
                        }
                    )
                except (TypeError, ValueError, IndexError, OverflowError):
                    continue

        bid_depth = sum(level["notional_usdt"] for level in parsed_sides["bids"])
        ask_depth = sum(level["notional_usdt"] for level in parsed_sides["asks"])
        ratio = bid_depth / ask_depth if ask_depth > 0 else (1.0 if bid_depth <= 0 else 1_000_000.0)
        if ratio >= 1.10:
            imbalance = "bid_heavy"
        elif ratio <= (1.0 / 1.10):
            imbalance = "ask_heavy"
        else:
            imbalance = "balanced"
        return {
            "bids": parsed_sides["bids"],
            "asks": parsed_sides["asks"],
            "bid_depth_usdt": bid_depth,
            "ask_depth_usdt": ask_depth,
            "imbalance_ratio": ratio,
            "imbalance": imbalance,
            "available": bool(parsed_sides["bids"] or parsed_sides["asks"]),
        }
    except Exception as exc:
        print(f"[Market Data Warning] Order book unavailable for {normalized}: {exc}")
        return {**empty, "error": str(exc)}


def get_klines_multi_timeframe(symbol: str) -> dict[str, list[dict[str, float | int]]]:
    """Fetch 100 candles for each analysis timeframe, isolating per-frame errors."""
    frames: dict[str, list[dict[str, float | int]]] = {}
    for interval in ("5m", "15m", "1h", "4h", "1d", "1w"):
        try:
            frames[interval] = get_klines(symbol, interval=interval, limit=100)
        except Exception as exc:
            print(
                f"[Market Data Warning] {symbol} {interval} klines unavailable: {exc}"
            )
            frames[interval] = []
    return frames


def get_btc_dominance_context() -> dict[str, Any]:
    """Fetch BTC dominance, or estimate a clearly-labelled Binance volume proxy."""
    try:
        response = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=10,
            headers={"accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        market_caps = data.get("market_cap_percentage", {})
        total_caps = data.get("total_market_cap", {})
        dominance = float(market_caps.get("btc"))
        total_mcap = float(total_caps.get("usd"))
        if dominance > 0 and total_mcap > 0:
            return {
                "btc_dominance_percent": dominance,
                "total_mcap_proxy": total_mcap,
                "source": "CoinGecko global market data",
                "estimated": False,
            }
    except Exception as exc:
        print(f"[Market Data Warning] CoinGecko dominance unavailable: {exc}")

    try:
        tickers = get_ticker_24h_all()
        usdt_tickers: list[tuple[str, float]] = []
        btc_volume = 0.0
        for item in tickers:
            symbol = str(item.get("symbol", "")).upper()
            if not symbol.endswith("USDT"):
                continue
            try:
                volume = float(item.get("quoteVolume", 0.0))
            except (TypeError, ValueError, OverflowError):
                continue
            if volume < 0:
                continue
            usdt_tickers.append((symbol, volume))
            if symbol == "BTCUSDT":
                btc_volume = volume
        top_twenty = sorted(usdt_tickers, key=lambda row: row[1], reverse=True)[:20]
        total_volume = sum(volume for _, volume in top_twenty)
        estimate = (btc_volume / total_volume * 100.0) if total_volume > 0 else None
        return {
            "btc_dominance_percent": estimate,
            "total_mcap_proxy": total_volume if total_volume > 0 else None,
            "btc_quote_volume_24h": btc_volume,
            "top20_quote_volume": total_volume,
            "source": "Binance top-20 USDT quote-volume proxy",
            "estimated": True,
        }
    except Exception as exc:
        print(f"[Market Data Warning] Binance BTC-dominance proxy unavailable: {exc}")
        return {
            "btc_dominance_percent": None,
            "total_mcap_proxy": None,
            "source": "unavailable",
            "estimated": True,
            "error": str(exc),
        }


def calculate_volume_analysis(
    klines_1h: list[dict[str, Any]], ticker_24h: dict[str, Any] | None
) -> dict[str, Any]:
    """Summarize directional volume, abnormal participation, and divergence."""
    neutral: dict[str, Any] = {
        "buying_volume": 0.0,
        "selling_volume": 0.0,
        "volume_spike": False,
        "breakout_volume_confirmed": False,
        "volume_divergence": "none",
        "average_volume": 0.0,
        "latest_volume": 0.0,
        "quote_volume_24h": None,
        "buy_sell_volume_source": "unavailable",
        "estimated": True,
    }
    try:
        candles: list[dict[str, float]] = []
        for row in klines_1h or []:
            try:
                taker_buy_volume = None
                if isinstance(row, dict):
                    candle = {
                        key: float(row[key])
                        for key in ("open", "high", "low", "close", "volume")
                    }
                    if row.get("taker_buy_volume") is not None:
                        taker_buy_volume = float(row["taker_buy_volume"])
                else:
                    candle = {
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                    }
                    if len(row) > 9:
                        taker_buy_volume = float(row[9])
                if not all(math.isfinite(value) for value in candle.values()):
                    continue
                if min(candle["open"], candle["high"], candle["low"], candle["close"]) <= 0 or candle["volume"] < 0:
                    continue
                if (
                    taker_buy_volume is not None
                    and math.isfinite(taker_buy_volume)
                    and 0 <= taker_buy_volume <= candle["volume"]
                ):
                    candle["taker_buy_volume"] = taker_buy_volume
                candles.append(candle)
            except (TypeError, ValueError, KeyError, IndexError, OverflowError):
                continue

        if not candles:
            return neutral
        buying = 0.0
        selling = 0.0
        taker_volume_candles = 0
        for row in candles:
            taker_buy = row.get("taker_buy_volume")
            if taker_buy is not None:
                buying += taker_buy
                selling += max(0.0, row["volume"] - taker_buy)
                taker_volume_candles += 1
            elif row["close"] >= row["open"]:
                buying += row["volume"]
            else:
                selling += row["volume"]
        latest_volume = candles[-1]["volume"]
        baseline = [row["volume"] for row in candles[-21:-1]]
        average_volume = sum(baseline) / len(baseline) if baseline else 0.0
        volume_spike = average_volume > 0 and latest_volume >= average_volume * 1.5

        prior = candles[-21:-1]
        breakout_up = bool(prior) and candles[-1]["close"] > max(row["high"] for row in prior)
        breakout_down = bool(prior) and candles[-1]["close"] < min(row["low"] for row in prior)
        breakout_confirmed = bool(volume_spike and (breakout_up or breakout_down))

        divergence = "none"
        if len(candles) >= 10:
            recent = candles[-5:]
            earlier = candles[-10:-5]
            recent_volume = sum(row["volume"] for row in recent) / len(recent)
            earlier_volume = sum(row["volume"] for row in earlier) / len(earlier)
            price_change = candles[-1]["close"] - candles[-6]["close"]
            if earlier_volume > 0 and recent_volume < earlier_volume * 0.8:
                if price_change > 0:
                    divergence = "bearish"
                elif price_change < 0:
                    divergence = "bullish"

        quote_volume = None
        if isinstance(ticker_24h, dict):
            raw_volume = ticker_24h.get(
                "quote_volume_24h", ticker_24h.get("quoteVolume")
            )
            try:
                parsed_volume = float(raw_volume)
                if parsed_volume >= 0 and math.isfinite(parsed_volume):
                    quote_volume = parsed_volume
            except (TypeError, ValueError, OverflowError):
                pass

        return {
            "buying_volume": buying,
            "selling_volume": selling,
            "volume_spike": bool(volume_spike),
            "breakout_volume_confirmed": breakout_confirmed,
            "volume_divergence": divergence,
            "average_volume": average_volume,
            "latest_volume": latest_volume,
            "quote_volume_24h": quote_volume,
            "buy_sell_volume_source": (
                "Binance taker-buy base-volume field"
                if taker_volume_candles == len(candles)
                else "mixed taker-buy/candle-direction estimate"
                if taker_volume_candles
                else "candle-direction estimate"
            ),
            "estimated": taker_volume_candles < len(candles),
        }
    except Exception as exc:
        print(f"[Market Data Warning] Volume analysis unavailable: {exc}")
        return neutral
