import json
import os
from datetime import datetime

from bot.poster import get_current_price, format_success_post

ACTIVE_FILE = "data/active_trades.json"
CLOSED_FILE = "data/closed_trades.json"


def load_active():
    if not os.path.exists(ACTIVE_FILE):
        return []
    with open(ACTIVE_FILE, "r") as f:
        return json.load(f)


def save_active(trades):
    os.makedirs("data", exist_ok=True)
    with open(ACTIVE_FILE, "w") as f:
        json.dump(trades, f, indent=2)


def save_closed(trade):
    os.makedirs("data", exist_ok=True)
    closed = []
    if os.path.exists(CLOSED_FILE):
        with open(CLOSED_FILE, "r") as f:
            closed = json.load(f)

    closed.append(trade)

    with open(CLOSED_FILE, "w") as f:
        json.dump(closed, f, indent=2)


def check_trades_and_maybe_post(publish_func):
    trades = load_active()

    if not trades:
        print("No active trades.")
        return

    remaining = []

    for t in trades:
        coin = t["coin"]

        try:
            price = get_current_price(coin)

            entry = t["entry_price"]
            tp1 = t["tp1"]
            tp2 = t["tp2"]
            sl = t["sl"]

            print(
                f"[Monitor] {coin}: Entry {entry} Now {price} "
                f"TP1 {tp1} TP2 {tp2} SL {sl}"
            )

            # 🔴 SL HIT → silent close
            if price <= sl:
                t["status"] = "SL_HIT"
                t["closed_price"] = price
                t["closed_at"] = datetime.utcnow().isoformat()
                save_closed(t)
                print(f"SL hit for {coin} → closed silently")
                continue  # remove from active

            # 🟢 TP2 HIT → close + post
            if price >= tp2:
                t["status"] = "TP2_HIT"
                t["closed_price"] = price
                t["closed_at"] = datetime.utcnow().isoformat()

                post = format_success_post(t, "TP2", price)
                publish_func(post, [])

                save_closed(t)
                print(f"TP2 hit for {coin} → closed")
                continue  # remove from active

            # 🟢 TP1 HIT → post once only
            if price >= tp1 and not t.get("tp1_hit"):
                t["tp1_hit"] = True

                post = format_success_post(t, "TP1", price)
                publish_func(post, [])

                print(f"TP1 hit for {coin}")

                remaining.append(t)
                continue

            # 🟡 Still active
            remaining.append(t)

        except Exception as e:
            print(f"[Monitor Error] {coin}: {e}")
            remaining.append(t)

    save_active(remaining)
