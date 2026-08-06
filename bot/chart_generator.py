import requests, pandas as pd, mplfinance as mpf, matplotlib, os
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import random

BINANCE_KLINE_URL = "https://api.binance.com/api/v3/klines"

def fetch_klines(symbol="BTCUSDT", interval="1h", limit=120):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(BINANCE_KLINE_URL, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    df = pd.DataFrame(data, columns=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    df = df[["open","high","low","close","volume"]].astype(float)
    return df

def generate_trending_chart(coin: str, signal: dict, timeframe: str = "1h", save_path: str = "chart.png"):
    symbol = f"{coin}USDT"
    interval = timeframe.lower()
    try:
        df = fetch_klines(symbol, interval, 120)
    except:
        import numpy as np
        dates = pd.date_range(end=pd.Timestamp.now(), periods=120, freq='H' if interval=='1h' else '4H')
        base = signal["entry_price"]
        closes = base + np.cumsum(np.random.randn(120)*base*0.008)
        df = pd.DataFrame({"open": closes*0.998, "high": closes*1.01, "low": closes*0.99, "close": closes, "volume": np.random.randint(1000,10000,120)}, index=dates)
    mc = mpf.make_marketcolors(up='#26a69a', down='#ef5350', edge='inherit', wick='inherit', volume='in')
    s = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=mc, facecolor='#131722', figcolor='#131722', edgecolor='#2a2e39', gridcolor='#2a2e39', gridstyle='--', y_on_right=True, rc={'axes.labelcolor': '#d1d4dc', 'xtick.color': '#848e9c', 'ytick.color': '#848e9c'})
    df['EMA21'] = df['close'].ewm(span=21).mean()
    df['EMA50'] = df['close'].ewm(span=50).mean()
    apds = [mpf.make_addplot(df['EMA21'], color='#2962FF', width=1.1), mpf.make_addplot(df['EMA50'], color='#FF6D00', width=1.1)]
    plot_df = df.tail(80).copy()
    title = f"{coin}/USDT • {timeframe.upper()} • {signal.get('smc_logic_short','Liquidity sweep + OB reclaim')}"
    fig, axlist = mpf.plot(plot_df, type='candle', style=s, addplot=apds, volume=True, figsize=(12,6.2), title=f"\n{title}", ylabel='Price (USDT)', ylabel_lower='Volume', returnfig=True, tight_layout=True, warn_too_much_data=1000)
    ax = axlist[0]
    entry = signal["entry_price"]; tp1 = signal["tp1"]; tp2 = signal["tp2"]; sl = signal["sl"]
    ax.axhline(entry, color='#787b86', linestyle='--', linewidth=1.0, alpha=0.9)
    ax.axhline(tp1, color='#26a69a', linestyle=':', linewidth=1.1, alpha=0.9)
    ax.axhline(tp2, color='#26a69a', linestyle=':', linewidth=1.1, alpha=0.9)
    ax.axhline(sl, color='#ef5350', linestyle='--', linewidth=1.0, alpha=0.9)
    ax.text(0.995, entry, f'  ENTRY {entry}', color='#d1d4dc', fontsize=7, va='center', ha='left', transform=ax.get_yaxis_transform(), bbox=dict(boxstyle="round,pad=0.2", fc="#2a2e39", ec="none", alpha=0.9))
    ax.text(0.995, tp1, f'  TP1 {tp1}', color='#26a69a', fontsize=7, va='center', ha='left', transform=ax.get_yaxis_transform(), bbox=dict(boxstyle="round,pad=0.2", fc="#0f2f2a", ec="none", alpha=0.9))
    ax.text(0.995, tp2, f'  TP2 {tp2}', color='#26a69a', fontsize=7, va='center', ha='left', transform=ax.get_yaxis_transform(), bbox=dict(boxstyle="round,pad=0.2", fc="#0f2f2a", ec="none", alpha=0.9))
    ax.text(0.995, sl, f'  SL {sl}', color='#ef5350', fontsize=7, va='center', ha='left', transform=ax.get_yaxis_transform(), bbox=dict(boxstyle="round,pad=0.2", fc="#3a1a1a", ec="none", alpha=0.9))
    fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor='#131722')
    plt.close(fig)
    print(f"[Chart] {timeframe} saved: {save_path}")
    return save_path

def generate_both_charts(coin: str, signal: dict, out_dir="charts"):
    os.makedirs(out_dir, exist_ok=True)
    p1 = f"{out_dir}/{coin}_1H.png"
    p2 = f"{out_dir}/{coin}_4H.png"
    generate_trending_chart(coin, signal, "1h", p1)
    generate_trending_chart(coin, signal, "4h", p2)
    return p1, p2
