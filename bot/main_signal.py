import os
import json
import datetime

from bot.signal_generator import (
    get_trending_coins,
    generate_signal_with_groq,
    get_live_price,
)

from bot.chart_generator import generate_both_charts
from poster import format_signal_post


POSTED_FILE = "data/posted_coins.json"
ACTIVE_FILE = "data/active_trades.json"
LATEST_POST_FILE = "data/latest_post.txt"
LATEST_SIGNAL_FILE = "data/latest_signal.json"
CHART_DIR = "charts"


def can_post(coin, hours=24):
    if not os.path.exists(POSTED_FILE):
        return True

    try:
        with open(POSTED_FILE, "r", encoding="utf-8") as f:
            posted = json.load(f)
    except (json.JSONDecodeError, OSError):
        return True

    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)

    for entry in posted:
        if entry.get("coin") != coin:
            continue

        try:
            posted_time = datetime.datetime.fromisoformat(
                entry["time"]
            )
        except (KeyError, ValueError, TypeError):
            continue

        if posted_time > cutoff:
            return False

    return True


def record_posted(coin):
    os.makedirs("data", exist_ok=True)

    posted = []

    if os.path.exists(POSTED_FILE):
        try:
            with open(POSTED_FILE, "r", encoding="utf-8") as f:
                posted = json.load(f)
        except (json.JSONDecodeError, OSError):
            posted = []

    posted.append(
        {
            "coin": coin,
            "time": datetime.datetime.now().isoformat(),
        }
    )

    posted = posted[-100:]

    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(posted, f, indent=2)


def save_active(signal):
    os.makedirs("data", exist_ok=True)

    trades = []

    if os.path.exists(ACTIVE_FILE):
        try:
            with open(ACTIVE_FILE, "r", encoding="utf-8") as f:
                trades = json.load(f)
        except (json.JSONDecodeError, OSError):
            trades = []

    trades.append(
        {
            **signal,
            "created_at": datetime.datetime.now().isoformat(),
            "status": "ACTIVE",
        }
    )

    with open(ACTIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2)


def validate_chart(path):
    """
    Make sure the generated chart actually exists
    and is not an empty/broken file.
    """
    if not path:
        return False

    if not os.path.isfile(path):
        return False

    try:
        return os.path.getsize(path) > 1000
    except OSError:
        return False


def run():
    print(
        f"[Signal] Starting at "
        f"{datetime.datetime.now().isoformat()}"
    )

    os.makedirs("data", exist_ok=True)
    os.makedirs(CHART_DIR, exist_ok=True)

    # -------------------------------------------------
    # 1. Get real Binance trending data
    # -------------------------------------------------

    trending = get_trending_coins(
        top_n=30,
        min_change=5.0,
        min_volume=5_000_000,
    )

    if not trending:
        raise RuntimeError(
            "No valid trending coins returned by Binance."
        )

    print(
        "[Trending] Top 5:",
        [
            (
                c["coin"],
                round(float(c["change"]), 2),
                c["price"],
            )
            for c in trending[:5]
        ],
    )

    # -------------------------------------------------
    # 2. Select a coin that has not been posted recently
    # -------------------------------------------------

    selected = None

    for coin_data in trending:
        coin = coin_data["coin"]

        if can_post(coin, 24):
            selected = coin_data
            break

    if selected is None:
        raise RuntimeError(
            "All eligible trending coins were posted "
            "within the last 24 hours. "
            "No duplicate post will be forced."
        )

    coin = selected["coin"]
    change = float(selected["change"])

    # -------------------------------------------------
    # 3. Refresh LIVE Binance price
    # -------------------------------------------------

    live_price = get_live_price(coin)

    old_price = float(selected["price"])

    print(
        f"[Price] {coin}: "
        f"scanner=${old_price} "
        f"live=${live_price}"
    )

    # The live price is now the authoritative price.
    price = live_price

    # -------------------------------------------------
    # 4. Choose another trending coin as context
    # -------------------------------------------------

    trending_bonus = "BTC"

    for coin_data in trending:
        if coin_data["coin"] != coin:
            trending_bonus = coin_data["coin"]
            break

    print(
        f"[Selected] {coin} "
        f"+{change:.2f}% "
        f"@ ${price} "
        f"| Context {trending_bonus}"
    )

    # -------------------------------------------------
    # 5. Generate signal using LIVE price
    # -------------------------------------------------

    signal = generate_signal_with_groq(
        coin=coin,
        price=price,
        change=change,
        trending_bonus=trending_bonus,
    )

    # -------------------------------------------------
    # 6. Hard validation of returned signal
    # -------------------------------------------------

    if not isinstance(signal, dict):
        raise RuntimeError(
            "Signal generator returned invalid data."
        )

    signal_coin = str(
        signal.get("coin", "")
    ).upper()

    if signal_coin != coin.upper():
        raise RuntimeError(
            f"Coin mismatch: "
            f"expected {coin}, got {signal_coin}"
        )

    # Force the authoritative live price.
    signal["coin"] = coin
    signal["entry_price"] = price
    signal["change"] = change

    print(
        "[Signal] Validated:",
        json.dumps(signal, indent=2),
    )

    # -------------------------------------------------
    # 7. Generate BOTH chart images
    # -------------------------------------------------

    print("[Chart] Generating charts...")

    try:
        p1, p2 = generate_both_charts(
            coin,
            signal,
            out_dir=CHART_DIR,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Chart generation failed: {exc}"
        ) from exc

    # -------------------------------------------------
    # 8. Verify chart files really exist
    # -------------------------------------------------

    charts = [p1, p2]

    invalid_charts = [
        path
        for path in charts
        if not validate_chart(path)
    ]

    if invalid_charts:
        raise RuntimeError(
            "Chart generation completed, but one or more "
            f"chart files are missing/invalid: {invalid_charts}"
        )

    print(
        "[Chart] Verified:",
        charts,
    )

    # -------------------------------------------------
    # 9. Create Square post text
    # -------------------------------------------------

    post = format_signal_post(signal)

    if not post or not post.strip():
        raise RuntimeError(
            "Generated post text is empty."
        )

    # -------------------------------------------------
    # 10. Save output only after all validation passes
    # -------------------------------------------------

    with open(
        LATEST_POST_FILE,
        "w",
        encoding="utf-8",
    ) as f:
        f.write(post)

    with open(
        LATEST_SIGNAL_FILE,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            signal,
            f,
            indent=2,
        )

    print("[Post] Text generated successfully.")
    print(post)

    # -------------------------------------------------
    # 11. Save active signal
    # -------------------------------------------------

    save_active(signal)

    # -------------------------------------------------
    # 12. Mark coin as posted
    # -------------------------------------------------

    record_posted(coin)

    print(
        f"[Done] {coin} signal and charts are ready."
    )


if __name__ == "__main__":
    run()
