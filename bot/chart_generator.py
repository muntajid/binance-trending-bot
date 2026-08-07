import os
import pandas as pd
import mplfinance as mpf
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from market_data import get_klines


def fetch_klines(symbol="BTCUSDT", interval="1h", limit=120):
    """
    Fetch candle data ONLY from verified market_data layer.
    No direct Binance API calls allowed.
    """

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

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
    )

    df.set_index("open_time", inplace=True)

    df = df[
        [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    ].astype(float)

    # Validation
    if df.isnull().any().any():
        raise RuntimeError(
            f"Invalid/null candle data received for {symbol}"
        )

    if (df["high"] < df["low"]).any():
        raise RuntimeError(
            f"Invalid OHLC data received for {symbol}"
        )

    if (df["close"] <= 0).any():
        raise RuntimeError(
            f"Invalid close price received for {symbol}"
        )

    return df


def generate_trending_chart(
    coin: str,
    signal: dict,
    timeframe: str = "1h",
    save_path: str = "chart.png",
):
    """
    Generate chart using ONLY verified Binance data.
    """

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
        raise RuntimeError(
            f"Not enough candles available for {symbol} {interval}"
        )

    # EMA indicators
    plot_df["EMA21"] = (
        plot_df["close"]
        .ewm(span=21, adjust=False)
        .mean()
    )

    plot_df["EMA50"] = (
        plot_df["close"]
        .ewm(span=50, adjust=False)
        .mean()
    )

    market_colors = mpf.make_marketcolors(
        up="#26a69a",
        down="#ef5350",
        edge="inherit",
        wick="inherit",
        volume="in",
    )

    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=market_colors,
        facecolor="#131722",
        figcolor="#131722",
        edgecolor="#2a2e39",
        gridcolor="#2a2e39",
        gridstyle="--",
        y_on_right=True,
    )

    addplots = [
        mpf.make_addplot(plot_df["EMA21"], color="#2962FF"),
        mpf.make_addplot(plot_df["EMA50"], color="#FF6D00"),
    ]

    title = f"{coin.upper()}/USDT • {timeframe.upper()}"

    fig, axlist = mpf.plot(
        plot_df[
            ["open", "high", "low", "close", "volume"]
        ],
        type="candle",
        style=style,
        addplot=addplots,
        volume=True,
        figsize=(12, 6.2),
        title=f"\n{title}",
        returnfig=True,
        tight_layout=True,
    )

    ax = axlist[0]

    # Validate signal prices
    try:
        entry = float(signal["entry_price"])
        tp1 = float(signal["tp1"])
        tp2 = float(signal["tp2"])
        sl = float(signal["sl"])
    except (KeyError, TypeError, ValueError) as exc:
        plt.close(fig)
        raise RuntimeError(
            f"Invalid signal price data: {exc}"
        ) from exc

    # Draw lines
    ax.axhline(entry, linestyle="--")
    ax.axhline(tp1, linestyle=":")
    ax.axhline(tp2, linestyle=":")
    ax.axhline(sl, linestyle="--")

    output_dir = os.path.dirname(save_path)

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    fig.savefig(
        save_path,
        dpi=150,
        bbox_inches="tight",
        facecolor="#131722",
    )

    plt.close(fig)

    if not os.path.isfile(save_path):
        raise RuntimeError(
            f"Chart file not created: {save_path}"
        )

    if os.path.getsize(save_path) < 1000:
        raise RuntimeError(
            f"Chart file invalid: {save_path}"
        )

    print(f"[Chart] Saved: {save_path}")

    return save_path


def generate_both_charts(
    coin: str,
    signal: dict,
    out_dir="charts",
):
    os.makedirs(out_dir, exist_ok=True)

    coin = coin.upper()

    p1 = os.path.join(out_dir, f"{coin}_1H.png")
    p2 = os.path.join(out_dir, f"{coin}_4H.png")

    generate_trending_chart(
        coin=coin,
        signal=signal,
        timeframe="1h",
        save_path=p1,
    )

    generate_trending_chart(
        coin=coin,
        signal=signal,
        timeframe="4h",
        save_path=p2,
    )

    return p1, p2
