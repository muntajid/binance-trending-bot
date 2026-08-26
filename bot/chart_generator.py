"""Verified Binance candlestick chart generator with Smart Money annotations."""

from __future__ import annotations

import os
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

from bot.market_data import get_klines


def fetch_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 120) -> pd.DataFrame:
    """Fetch candle data ONLY from verified market_data layer."""

    klines = get_klines(
        symbol=symbol.upper(),
        interval=interval,
        limit=limit,
    )

    if not klines or len(klines) < 60:
        raise RuntimeError(
            f"Insufficient verified candle data for {symbol} {interval}"
        )

    df = pd.DataFrame(klines)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)

    df = df[["open", "high", "low", "close", "volume"]].astype(float)

    if df.isnull().any().any():
        raise RuntimeError(f"Invalid/null candle data received for {symbol}")

    if (df["high"] < df["low"]).any():
        raise RuntimeError(f"Invalid OHLC data received for {symbol}")

    if (df["close"] <= 0).any():
        raise RuntimeError(f"Invalid close price received for {symbol}")

    return df


def _format_chart_price(value: float) -> str:
    """Format price cleanly for chart annotations."""
    val = float(value)
    if val >= 1000:
        return f"{val:.2f}"
    if val >= 1:
        return f"{val:.4f}".rstrip("0").rstrip(".")
    return f"{val:.6f}".rstrip("0").rstrip(".")


def generate_trending_chart(
    coin: str,
    signal: dict,
    timeframe: str = "1h",
    save_path: str = "chart.png",
) -> str:
    """Generate professional candlestick chart with clear Entry, TP1, TP2, and SL annotations."""

    symbol = f"{coin.upper()}USDT"
    interval = timeframe.lower()

    print(f"[Chart] Using VERIFIED data: {symbol} {interval}")

    df = fetch_klines(
        symbol=symbol,
        interval=interval,
        limit=120,
    )

    plot_df = df.tail(80).copy()
    if len(plot_df) < 50:
        raise RuntimeError(f"Not enough candles available for {symbol} {interval}")

    plot_df["EMA21"] = plot_df["close"].ewm(span=21, adjust=False).mean()
    plot_df["EMA50"] = plot_df["close"].ewm(span=50, adjust=False).mean()

    market_colors = mpf.make_marketcolors(
        up="#0ecb81",
        down="#f6465d",
        edge="inherit",
        wick="inherit",
        volume={"up": "#0ecb8180", "down": "#f6465d80"},
    )

    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=market_colors,
        facecolor="#12151c",
        figcolor="#0b0e14",
        edgecolor="#1e2329",
        gridcolor="#1e2329",
        gridstyle=":",
        y_on_right=True,
    )

    addplots = [
        mpf.make_addplot(plot_df["EMA21"], color="#f0b90b", width=1.4),
        mpf.make_addplot(plot_df["EMA50"], color="#2962ff", width=1.4),
    ]

    title_text = f"{coin.upper()}/USDT • {timeframe.upper()} Smart Money Setup"

    fig, axlist = mpf.plot(
        plot_df[["open", "high", "low", "close", "volume"]],
        type="candle",
        style=style,
        addplot=addplots,
        volume=True,
        figsize=(12, 6.8),
        title=dict(title=title_text, color="#f0b90b", fontsize=14, fontweight="bold"),
        returnfig=True,
        tight_layout=True,
    )

    ax = axlist[0]

    try:
        entry = float(signal["entry_price"])
        tp1 = float(signal["tp1"])
        tp2 = float(signal["tp2"])
        sl = float(signal["sl"])
        direction = str(signal.get("direction", "LONG")).upper().strip()
    except (KeyError, TypeError, ValueError) as exc:
        plt.close(fig)
        raise RuntimeError(f"Invalid signal price data: {exc}") from exc

    if direction == "LONG":
        tp1_pct = ((tp1 - entry) / entry) * 100.0
        tp2_pct = ((tp2 - entry) / entry) * 100.0
        sl_pct = ((entry - sl) / entry) * 100.0
    else:
        tp1_pct = ((entry - tp1) / entry) * 100.0
        tp2_pct = ((entry - tp2) / entry) * 100.0
        sl_pct = ((sl - entry) / entry) * 100.0

    # Expand y-limits to ensure all lines, wicks, and title fit with clean breathing room
    current_ymin, current_ymax = ax.get_ylim()
    all_prices = [entry, tp1, tp2, sl, current_ymin, current_ymax]
    price_span = max(all_prices) - min(all_prices)
    padding = price_span * 0.08
    new_ymin = min(all_prices) - padding
    new_ymax = max(all_prices) + padding
    ax.set_ylim(new_ymin, new_ymax)

    entry_str = _format_chart_price(entry)
    tp1_str = _format_chart_price(tp1)
    tp2_str = _format_chart_price(tp2)
    sl_str = _format_chart_price(sl)

    # Draw Target 2
    ax.axhline(tp2, color="#0ecb81", linestyle="--", linewidth=1.5, alpha=0.9)
    ax.text(
        0.015,
        tp2,
        f" TP2: ${tp2_str} (+{tp2_pct:.1f}%)",
        transform=ax.get_yaxis_transform(),
        color="#0ecb81",
        fontsize=9.5,
        fontweight="bold",
        va="center",
        bbox=dict(
            boxstyle="round,pad=0.25",
            facecolor="#12151c",
            edgecolor="#0ecb81",
            alpha=0.9,
        ),
    )

    # Draw Target 1
    ax.axhline(tp1, color="#26a69a", linestyle="--", linewidth=1.5, alpha=0.9)
    ax.text(
        0.015,
        tp1,
        f" TP1: ${tp1_str} (+{tp1_pct:.1f}%)",
        transform=ax.get_yaxis_transform(),
        color="#26a69a",
        fontsize=9.5,
        fontweight="bold",
        va="center",
        bbox=dict(
            boxstyle="round,pad=0.25",
            facecolor="#12151c",
            edgecolor="#26a69a",
            alpha=0.9,
        ),
    )

    # Draw Entry
    ax.axhline(entry, color="#f0b90b", linestyle="-", linewidth=1.8, alpha=0.9)
    ax.text(
        0.015,
        entry,
        f" ENTRY (CMP): ${entry_str}",
        transform=ax.get_yaxis_transform(),
        color="#f0b90b",
        fontsize=9.5,
        fontweight="bold",
        va="center",
        bbox=dict(
            boxstyle="round,pad=0.25",
            facecolor="#12151c",
            edgecolor="#f0b90b",
            alpha=0.9,
        ),
    )

    # Draw Stop Loss
    ax.axhline(sl, color="#f6465d", linestyle="--", linewidth=1.5, alpha=0.9)
    ax.text(
        0.015,
        sl,
        f" STOP LOSS: ${sl_str} (-{sl_pct:.1f}%)",
        transform=ax.get_yaxis_transform(),
        color="#f6465d",
        fontsize=9.5,
        fontweight="bold",
        va="center",
        bbox=dict(
            boxstyle="round,pad=0.25",
            facecolor="#12151c",
            edgecolor="#f6465d",
            alpha=0.9,
        ),
    )

    # Indicator Legend Badge
    ax.text(
        0.985,
        0.96,
        " EMA 21 (Yellow)  |  EMA 50 (Blue) ",
        transform=ax.transAxes,
        color="#eaecef",
        fontsize=8.5,
        fontweight="medium",
        ha="right",
        va="top",
        bbox=dict(
            boxstyle="round,pad=0.25",
            facecolor="#1e2329",
            edgecolor="#474d57",
            alpha=0.85,
        ),
    )

    output_dir = os.path.dirname(save_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    fig.savefig(
        save_path,
        dpi=150,
        bbox_inches="tight",
        facecolor="#0b0e14",
    )
    plt.close(fig)

    if not os.path.isfile(save_path) or os.path.getsize(save_path) < 1000:
        raise RuntimeError(f"Chart file invalid or not created: {save_path}")

    print(f"[Chart] Saved: {save_path}")
    return save_path


def generate_btc_market_chart(
    save_path: str = "charts/BTC_1H.png",
) -> str:
    """Generate high-quality 1H BTC chart for Macro / Market Overview posts."""

    print("[Chart] Generating BTC 1H Market Overview chart...")
    df = fetch_klines(symbol="BTCUSDT", interval="1h", limit=120)
    plot_df = df.tail(80).copy()

    plot_df["EMA21"] = plot_df["close"].ewm(span=21, adjust=False).mean()
    plot_df["EMA50"] = plot_df["close"].ewm(span=50, adjust=False).mean()

    market_colors = mpf.make_marketcolors(
        up="#0ecb81",
        down="#f6465d",
        edge="inherit",
        wick="inherit",
        volume={"up": "#0ecb8180", "down": "#f6465d80"},
    )

    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=market_colors,
        facecolor="#12151c",
        figcolor="#0b0e14",
        edgecolor="#1e2329",
        gridcolor="#1e2329",
        gridstyle=":",
        y_on_right=True,
    )

    addplots = [
        mpf.make_addplot(plot_df["EMA21"], color="#f0b90b", width=1.4),
        mpf.make_addplot(plot_df["EMA50"], color="#2962ff", width=1.4),
    ]

    fig, axlist = mpf.plot(
        plot_df[["open", "high", "low", "close", "volume"]],
        type="candle",
        style=style,
        addplot=addplots,
        volume=True,
        figsize=(12, 6.8),
        title=dict(title="BTC/USDT • 1H Macro Market Structure", color="#f0b90b", fontsize=14, fontweight="bold"),
        returnfig=True,
        tight_layout=True,
    )

    ax = axlist[0]
    last_price = float(plot_df["close"].iloc[-1])
    price_str = _format_chart_price(last_price)

    # Expand top margin so title has clean clearance
    current_ymin, current_ymax = ax.get_ylim()
    span = current_ymax - current_ymin
    ax.set_ylim(current_ymin - span * 0.04, current_ymax + span * 0.12)

    ax.axhline(last_price, color="#f0b90b", linestyle="-", linewidth=1.5, alpha=0.9)
    ax.text(
        0.015,
        last_price,
        f" BTC LIVE: ${price_str}",
        transform=ax.get_yaxis_transform(),
        color="#f0b90b",
        fontsize=9.5,
        fontweight="bold",
        va="center",
        bbox=dict(
            boxstyle="round,pad=0.25",
            facecolor="#12151c",
            edgecolor="#f0b90b",
            alpha=0.9,
        ),
    )

    ax.text(
        0.985,
        0.96,
        " EMA 21 (Yellow)  |  EMA 50 (Blue) ",
        transform=ax.transAxes,
        color="#eaecef",
        fontsize=8.5,
        fontweight="medium",
        ha="right",
        va="top",
        bbox=dict(
            boxstyle="round,pad=0.25",
            facecolor="#1e2329",
            edgecolor="#474d57",
            alpha=0.85,
        ),
    )

    output_dir = os.path.dirname(save_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    fig.savefig(
        save_path,
        dpi=150,
        bbox_inches="tight",
        facecolor="#0b0e14",
    )
    plt.close(fig)

    if not os.path.isfile(save_path) or os.path.getsize(save_path) < 1000:
        raise RuntimeError(f"BTC chart invalid: {save_path}")

    print(f"[Chart] Saved BTC chart: {save_path}")
    return save_path


def generate_single_chart(
    coin: str,
    signal: dict,
    out_dir: str = "charts",
) -> str:
    """Generate the single authoritative 1H chart for the signal (per benchmark recommendation)."""

    os.makedirs(out_dir, exist_ok=True)
    coin = coin.upper()
    chart_path = os.path.join(out_dir, f"{coin}_1H.png")

    generate_trending_chart(
        coin=coin,
        signal=signal,
        timeframe="1h",
        save_path=chart_path,
    )
    return chart_path


def generate_both_charts(
    coin: str,
    signal: dict,
    out_dir: str = "charts",
) -> tuple[str, str]:
    """Kept for backward compatibility; generates 1H and 4H charts."""

    os.makedirs(out_dir, exist_ok=True)
    coin = coin.upper()

    p1 = os.path.join(out_dir, f"{coin}_1H.png")
    p2 = os.path.join(out_dir, f"{coin}_4H.png")

    generate_trending_chart(coin=coin, signal=signal, timeframe="1h", save_path=p1)
    generate_trending_chart(coin=coin, signal=signal, timeframe="4h", save_path=p2)

    return p1, p2
