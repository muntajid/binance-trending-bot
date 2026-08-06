import os, json, random, datetime
from signal_generator import get_trending_coins, generate_signal_with_groq
from chart_generator import generate_both_charts
from poster import format_signal_post

POSTED_FILE="data/posted_coins.json"
ACTIVE_FILE="data/active_trades.json"

def can_post(coin, hours=24):
    if not os.path.exists(POSTED_FILE): return True
    with open(POSTED_FILE) as f: posted=json.load(f)
    cutoff=datetime.datetime.now() - datetime.timedelta(hours=hours)
    for e in posted:
        if e["coin"]==coin:
            if datetime.datetime.fromisoformat(e["time"]) > cutoff:
                return False
    return True

def record_posted(coin):
    os.makedirs("data", exist_ok=True)
    posted=[]
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE) as f: posted=json.load(f)
    posted.append({"coin": coin, "time": datetime.datetime.now().isoformat()})
    posted=posted[-100:]
    with open(POSTED_FILE,"w") as f: json.dump(posted,f,indent=2)

def save_active(signal):
    os.makedirs("data", exist_ok=True)
    trades=[]
    if os.path.exists(ACTIVE_FILE):
        with open(ACTIVE_FILE) as f: trades=json.load(f)
    trades.append({**signal, "created_at": datetime.datetime.now().isoformat(), "status":"ACTIVE"})
    with open(ACTIVE_FILE,"w") as f: json.dump(trades,f,indent=2)

def run():
    print(f"[Signal] Starting at {datetime.datetime.now()}")
    trending=get_trending_coins(top_n=30, min_change=5.0, min_volume=5000000)
    print(f"[Trending] Top 5: {[(c['coin'], round(c['change'],1)) for c in trending[:5]]}")
    selected=None; trending_bonus="BTC"
    for c in trending:
        if can_post(c["coin"], 24):
            selected=c; break
    if not selected:
        selected=random.choice(trending)
        print(f"[Duplicate] All trending posted, forcing {selected['coin']}")
    for c in trending:
        if c["coin"] != selected["coin"]:
            trending_bonus=c["coin"]
            break
    coin=selected["coin"]; price=selected["price"]; change=selected["change"]
    print(f"[Selected] {coin} +{change:.1f}% @ ${price} | Bonus ${trending_bonus}")
    signal=generate_signal_with_groq(coin, price, change, trending_bonus)
    print(f"[Groq] {json.dumps(signal, indent=2)}")
    try:
        p1,p2=generate_both_charts(coin, signal, out_dir="charts")
        charts=[p1,p2]
    except Exception as e:
        print(f"[Chart Error] {e}")
        charts=[]
    post=format_signal_post(signal)
    os.makedirs("data", exist_ok=True)
    with open("data/latest_post.txt","w",encoding="utf-8") as f: f.write(post)
    with open("data/latest_signal.json","w") as f: json.dump(signal,f,indent=2)
    print(post)
    record_posted(coin)
    save_active(signal)
    print("[Done] Signal ready")

if __name__=="__main__":
    run()
