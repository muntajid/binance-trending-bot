import requests
from datetime import datetime, timezone

BASE_URL = "https://data-api.binance.vision"
TIMEOUT = 15


class MarketDataError(Exception):
    pass


def _get(endpoint, params):
    url = f"{BASE_URL}{endpoint}"

    response = requests.get(
        url,
        params=params,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    if not data:
        raise MarketDataError("Binance returned empty data")

    return data


def get_24h_data(symbol):
    symbol = symbol.upper()

    data = _get(
        "/api/v3/ticker/24hr",
        {"symbol": symbol},
    )

    required = [
        "symbol",
        "lastPrice",
        "priceChangePercent",
        "quoteVolume",
        "closeTime",
    ]

    for field in required:
        if field not in data:
            raise MarketDataError(
                f"Missing Binance field: {field}"
            )

    if data["symbol"] != symbol:
        raise MarketDataError(
            f"Symbol mismatch: expected {symbol}, "
            f"received {data['symbol']}"
        )

    price = float(data["lastPrice"])
    change = float(data["priceChangePercent"])
    volume = float(data["quoteVolume"])
    close_time = int(data["closeTime"])

    if price <= 0:
        raise MarketDataError(
            f"Invalid price: {price}"
        )

    if volume < 0:
        raise MarketDataError(
            f"Invalid volume: {volume}"
        )

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


def get_klines(symbol, interval="1h", limit=120):
    symbol = symbol.upper()

    if interval not in {
        "1m", "3m", "5m", "15m",
        "30m", "1h", "2h", "4h",
        "6h", "8h", "12h", "1d",
    }:
        raise MarketDataError(
            f"Unsupported interval: {interval}"
        )

    if not 1 <= limit <= 1000:
        raise MarketDataError(
            "Kline limit must be between 1 and 1000"
        )

    data = _get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        },
    )

    candles = []

    for row in data:
        if len(row) < 6:
            raise MarketDataError(
                "Invalid kline returned by Binance"
            )

        candles.append({
            "open_time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "close_time": int(row[6]),
        })

    if not candles:
        raise MarketDataError(
            "No candle data returned"
        )

    for candle in candles:
        if (
            candle["open"] <= 0
            or candle["high"] <= 0
            or candle["low"] <= 0
            or candle["close"] <= 0
            or candle["volume"] < 0
        ):
            raise MarketDataError(
                "Invalid candle value detected"
            )

        if candle["low"] > candle["high"]:
            raise MarketDataError(
                "Candle low is greater than high"
            )

    return candles
