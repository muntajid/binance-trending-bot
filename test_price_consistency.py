import sys
from datetime import datetime, timezone

from bot.market_data import get_24h_data, get_klines

SYMBOL = "BTCUSDT"
INTERVAL = "1h"
MAX_ALLOWED_PERCENT_DIFF = 0.5  # 0.5%

def percent_difference(a, b):
    return abs(a - b) / ((a + b) / 2) * 100

def main():
    print("=== PRICE CONSISTENCY TEST ===")
    print(f"Symbol: {SYMBOL}")
    print()

    # Get verified ticker data
    ticker = get_24h_data(SYMBOL)

    if not ticker.get("verified"):
        print("ERROR: Ticker data not verified.")
        sys.exit(1)

    ticker_price = float(ticker["price"])
    print(f"Ticker Price: {ticker_price}")

    # Get latest kline data
    klines = get_klines(SYMBOL, interval=INTERVAL, limit=2)

    if not klines:
        print("ERROR: No kline data returned.")
        sys.exit(1)

    latest_candle = klines[-1]
    candle_close = float(latest_candle["close"])

    candle_time = datetime.fromtimestamp(
        latest_candle["close_time"] / 1000,
        tz=timezone.utc
    )

    print(f"Latest 1H Candle Close: {candle_close}")
    print(f"Candle Close Time (UTC): {candle_time.isoformat()}")
    print()

    diff = percent_difference(ticker_price, candle_close)

    print(f"Percent Difference: {diff:.6f}%")
    print(f"Max Allowed: {MAX_ALLOWED_PERCENT_DIFF}%")
    print()

    if diff > MAX_ALLOWED_PERCENT_DIFF:
        print("STATUS: FAILED ❌")
        sys.exit(1)

    print("STATUS: CONSISTENT ✅")
    sys.exit(0)

if __name__ == "__main__":
    main()
