import sys

from bot.market_data import get_klines
from bot.chart_generator import fetch_klines

SYMBOL = "BTCUSDT"
INTERVAL = "1h"
LIMIT = 120
MAX_ALLOWED_PERCENT_DIFF = 0.5


def percent_difference(a, b):
    return abs(a - b) / ((a + b) / 2) * 100


def main():
    print("=== CHART vs VERIFIED DATA TEST ===")
    print(f"Symbol: {SYMBOL}")
    print()

    # ✅ Verified layer
    verified_klines = get_klines(
        SYMBOL,
        interval=INTERVAL,
        limit=LIMIT,
    )

    verified_close = float(
        verified_klines[-1]["close"]
    )

    print(f"Verified Close: {verified_close}")

    # 🔴 Chart layer
    chart_df = fetch_klines(
        symbol=SYMBOL,
        interval=INTERVAL,
        limit=LIMIT,
    )

    chart_close = float(
        chart_df["close"].iloc[-1]
    )

    print(f"Chart Close: {chart_close}")
    print()

    diff = percent_difference(
        verified_close,
        chart_close,
    )

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
