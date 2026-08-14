"""Verified Binance public market-data access used by signals and monitoring."""

from __future__ import annotations

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
