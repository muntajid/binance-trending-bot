from bot.market_data import get_24h_data, get_klines


def main():
    symbol = "BTCUSDT"

    print("=== BINANCE VERIFIED DATA TEST ===")

    ticker = get_24h_data(symbol)

    print("Symbol:", ticker["symbol"])
    print("Price:", ticker["price"])
    print("24h Change:", ticker["change_24h"])
    print("24h Volume:", ticker["quote_volume_24h"])
    print("Timestamp UTC:", ticker["timestamp_utc"])
    print("Source:", ticker["source"])
    print("Verified:", ticker["verified"])

    candles = get_klines(
        symbol,
        interval="1h",
        limit=5,
    )

    print("\n=== LAST 5 VERIFIED CANDLES ===")

    for candle in candles:
        print(
            candle["open_time"],
            candle["open"],
            candle["high"],
            candle["low"],
            candle["close"],
            candle["volume"],
        )

    if not ticker["verified"]:
        raise RuntimeError(
            "Market data verification failed"
        )

    print("\nSTATUS: VERIFIED")


if __name__ == "__main__":
    main()
