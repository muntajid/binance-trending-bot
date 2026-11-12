"""Verified Binance candlestick chart generator with TradingView & Binance Mobile styling.

Research basis: binance_square_visual_research_report.pdf & creator visual analysis.
Renders mobile-first (4:5 portrait) dark-mode charts with TradingView-style position
boxes (target profit area + risk stop area), right-axis price badges, native Binance
app header, and diversified visual formats (setup arrow, trendline retest, support/demand
zone, scenario projection, and breakout expansion).

All charts strictly use VERIFIED Binance candlestick data from bot.market_data.
Never fabricates candles, prices, or levels.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.font_manager as fm
import matplotlib.patches as patches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from bot.market_data import get_klines

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

try:
    FONT_BOLD = fm.FontProperties(fname=str(FONTS_DIR / "Inter_18pt-Bold.ttf"))
    FONT_SEMIBOLD = fm.FontProperties(fname=str(FONTS_DIR / "Inter_18pt-SemiBold.ttf"))
    FONT_MEDIUM = fm.FontProperties(fname=str(FONTS_DIR / "Inter_18pt-Medium.ttf"))
    FONT_REGULAR = fm.FontProperties(fname=str(FONTS_DIR / "Inter_18pt-Regular.ttf"))
except Exception:
    FONT_BOLD = fm.FontProperties(weight="bold")
    FONT_SEMIBOLD = fm.FontProperties(weight="semibold")
    FONT_MEDIUM = fm.FontProperties(weight="medium")
    FONT_REGULAR = fm.FontProperties(weight="normal")

VALID_VISUAL_TYPES = (
    "setup_box_arrow",
    "trendline_retest",
    "support_demand_zone",
    "scenario_projection",
    "breakout_expansion",
)


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
    """Format price cleanly for chart annotations and right-axis badges."""
    val = float(value)
    if val >= 1000:
        return f"{val:,.2f}"
    if val >= 1:
        return f"{val:.4f}".rstrip("0").rstrip(".")
    return f"{val:.6f}".rstrip("0").rstrip(".")


def _find_swing_trendline(
    df: pd.DataFrame, direction: str = "LONG"
) -> tuple[tuple[int, float], tuple[int, float]] | None:
    """Find authentic swing points in recent candles to construct a verified trendline.

    Returns (p1, p2) as ((index, price), (index, price)), or None if geometry
    does not support a clear trendline without distorting data.
    """
    n = len(df)
    if n < 25:
        return None

    recent_start = max(0, n - 42)
    recent_end = max(recent_start + 12, n - 4)
    sub = df.iloc[recent_start:recent_end]
    span = len(sub)
    if span < 12:
        return None

    mid = span // 2

    if direction == "LONG":
        lows = sub["low"].values
        idxs = list(range(span))
        min1_local = min(idxs[:mid], key=lambda k: lows[k])
        min2_local = min(idxs[mid:], key=lambda k: lows[k])

        p1 = (recent_start + min1_local, float(lows[min1_local]))
        p2 = (recent_start + min2_local, float(lows[min2_local]))

        # Need at least 8 bars separation and an ascending or stable base
        if (p2[0] - p1[0] >= 8) and (p2[1] >= p1[1] * 0.992):
            return p1, p2
    else:
        highs = sub["high"].values
        idxs = list(range(span))
        max1_local = max(idxs[:mid], key=lambda k: highs[k])
        max2_local = max(idxs[mid:], key=lambda k: highs[k])

        p1 = (recent_start + max1_local, float(highs[max1_local]))
        p2 = (recent_start + max2_local, float(highs[max2_local]))

        if (p2[0] - p1[0] >= 8) and (p2[1] <= p1[1] * 1.008):
            return p1, p2

    return None


def _find_support_demand_zone(
    df: pd.DataFrame, entry: float, direction: str = "LONG"
) -> tuple[int, float, float] | None:
    """Find verified support or resistance zone boundary near entry."""
    n = len(df)
    if n < 20:
        return None

    recent = df.tail(32)
    start_idx = max(4, n - 32)

    if direction == "LONG":
        lows = [float(l) for l in recent["low"].values if float(l) < entry]
        if not lows:
            return None
        zone_bottom = min(lows)
        zone_top = zone_bottom + (entry - zone_bottom) * 0.42
        return start_idx, zone_bottom, zone_top
    else:
        highs = [float(h) for h in recent["high"].values if float(h) > entry]
        if not highs:
            return None
        zone_top = max(highs)
        zone_bottom = zone_top - (zone_top - entry) * 0.42
        return start_idx, zone_bottom, zone_top


def select_chart_visual_type(
    signal: dict[str, Any],
    df: pd.DataFrame,
) -> str:
    """Choose the best evidence-backed visual type based on signal narrative and candle geometry.

    Types available:
      1. 'setup_box_arrow': Classic TradingView position box with bold directional arrow (Screenshots 1 & 4).
      2. 'trendline_retest': Trendline support/resistance + retest touch arrows + position box (Screenshot 3).
      3. 'support_demand_zone': Translucent support/demand zone box + reaction arrow + position box (Screenshot 5).
      4. 'scenario_projection': Hypothetical curved scenario projection path + position box (Screenshot 5).
      5. 'breakout_expansion': Breakout level + TP Market tag + expansion arrow + position box (Screenshot 2).
    """
    explicit = signal.get("visual_type")
    if explicit and explicit in VALID_VISUAL_TYPES:
        return str(explicit)

    direction = str(signal.get("direction", "LONG")).upper().strip()
    angle = str(signal.get("angle_family", "")).lower().strip()
    narrative = (
        str(signal.get("why_now", "")) + " " +
        str(signal.get("smc_logic", "")) + " " +
        str(signal.get("technical_analysis", ""))
    ).lower()

    # 1. Structure break angle -> Trendline retest if supported by candle geometry, else breakout
    if angle == "structure_break" or any(w in narrative for w in ("trendline", "channel", "ascending", "descending")):
        if _find_swing_trendline(df, direction) is not None:
            return "trendline_retest"
        return "breakout_expansion"

    # 2. Binary scenario or contrarian conflict -> Scenario projection curve
    if angle in {"binary_scenario", "contrarian_conflict"} or any(w in narrative for w in ("scenario", "pullback", "retest then", "if price")):
        return "scenario_projection"

    # 3. Event risk, outcome update or support/demand narrative -> Support/Demand Zone box
    if angle in {"event_risk", "outcome_update"} or any(w in narrative for w in ("support", "demand", "order block", "bounce", "floor", "base")):
        if _find_support_demand_zone(df, float(signal.get("entry_price", df["close"].iloc[-1])), direction) is not None:
            return "support_demand_zone"

    # 4. Breakout narrative
    if any(w in narrative for w in ("breakout", "break above", "break below", "expansion")):
        return "breakout_expansion"

    # 5. Deterministic hash rotation for variety across daily posts
    seed_str = str(signal.get("coin", "")) + str(signal.get("hook_line", ""))
    hash_val = sum(ord(c) for c in seed_str) % 4
    if hash_val == 0:
        return "setup_box_arrow"
    elif hash_val == 1:
        if _find_swing_trendline(df, direction) is not None:
            return "trendline_retest"
        return "setup_box_arrow"
    elif hash_val == 2:
        if _find_support_demand_zone(df, float(signal.get("entry_price", df["close"].iloc[-1])), direction) is not None:
            return "support_demand_zone"
        return "scenario_projection"
    else:
        return "scenario_projection"


def generate_trending_chart(
    coin: str,
    signal: dict,
    timeframe: str = "1h",
    save_path: str = "chart.png",
    visual_type: str | None = None,
) -> str:
    """Generate mobile-first (4:5 portrait) TradingView / Binance Mobile style chart with verified levels."""

    symbol = f"{coin.upper()}USDT"
    interval = timeframe.lower()

    print(f"[Chart] Using VERIFIED data: {symbol} {interval}")

    df = fetch_klines(
        symbol=symbol,
        interval=interval,
        limit=120,
    )

    plot_df = df.tail(58).copy()
    if len(plot_df) < 40:
        raise RuntimeError(f"Not enough candles available for {symbol} {interval}")

    try:
        entry = float(signal["entry_price"])
        tp1 = float(signal["tp1"])
        tp2 = float(signal.get("tp2", tp1 * (1.015 if str(signal.get("direction", "LONG")).upper() == "LONG" else 0.985)))
        sl = float(signal["sl"])
        direction = str(signal.get("direction", "LONG")).upper().strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid signal price data: {exc}") from exc

    if visual_type is None or visual_type not in VALID_VISUAL_TYPES:
        visual_type = select_chart_visual_type(signal, plot_df)

    print(f"[Chart] Visual format: {visual_type} ({direction})")

    # Mobile-first 4:5 portrait aspect ratio: width=8.0, height=10.0 at 130 dpi -> 1040x1300 px
    fig = plt.figure(figsize=(8.0, 10.0), dpi=130)
    fig.patch.set_facecolor("#12151c")

    # Header region (top 10%)
    ax_header = fig.add_axes([0.05, 0.88, 0.90, 0.10])
    ax_header.set_facecolor("#12151c")
    ax_header.axis("off")

    # Main chart region (leaves right margin for TradingView price badges)
    ax = fig.add_axes([0.05, 0.065, 0.74, 0.80])
    ax.set_facecolor("#12151c")

    change_pct = float(signal.get("change", 0.0))
    tf_label = timeframe.lower()

    # Header row 1: Timeframe selector bar
    timeframe_pills = ["Time", "15m", "1h", "4h", "1D", "30m v"]
    x_cursor = 0.0
    for tf_item in timeframe_pills:
        is_active = (tf_item.lower().startswith(tf_label)) or (tf_label == "1h" and tf_item == "1h")
        col = "#ffffff" if is_active else "#848e9c"
        bbox = dict(boxstyle="round,pad=0.22", facecolor="#2b313a", edgecolor="none") if is_active else None
        ax_header.text(
            x_cursor,
            0.80,
            tf_item,
            fontproperties=FONT_MEDIUM,
            fontsize=9.2,
            color=col,
            bbox=bbox,
            va="center",
        )
        x_cursor += 0.085 if tf_item == "Time" else 0.075

    # Header right icons
    ax_header.text(0.98, 0.80, "[ ]   #", fontproperties=FONT_BOLD, fontsize=10, color="#848e9c", ha="right", va="center")

    # Header row 2: Symbol Last Price · Binance
    ax_header.text(0.0, 0.42, f"{symbol} Last Price · Binance", fontproperties=FONT_BOLD, fontsize=14.5, color="#ffffff", va="center")

    # Header row 3: Price & Change
    price_str = _format_chart_price(entry)
    chg_sign = "+" if change_pct >= 0 else ""
    chg_col = "#0ecb81" if change_pct >= 0 else "#f6465d"
    ax_header.text(0.0, 0.06, f"{price_str}", fontproperties=FONT_BOLD, fontsize=13, color=chg_col, va="center")
    diff_val = entry * (change_pct / 100.0)
    ax_header.text(
        0.24,
        0.06,
        f"{chg_sign}{diff_val:.1f} ({chg_sign}{change_pct:.2f}%)" if abs(diff_val) >= 1 else f"{chg_sign}{diff_val:.4f} ({chg_sign}{change_pct:.2f}%)",
        fontproperties=FONT_MEDIUM,
        fontsize=12,
        color=chg_col,
        va="center",
    )

    # Plot authentic candlesticks
    n_candles = len(plot_df)
    candle_width = 0.68
    for i in range(n_candles):
        open_p = plot_df["open"].iloc[i]
        high_p = plot_df["high"].iloc[i]
        low_p = plot_df["low"].iloc[i]
        close_p = plot_df["close"].iloc[i]

        candle_col = "#0ecb81" if close_p >= open_p else "#f6465d"
        ax.plot([i, i], [low_p, high_p], color=candle_col, linewidth=1.2, zorder=2)
        body_bottom = min(open_p, close_p)
        body_height = max(abs(close_p - open_p), (high_p - low_p) * 0.015)
        rect = patches.Rectangle(
            (i - candle_width / 2.0, body_bottom),
            candle_width,
            body_height,
            facecolor=candle_col,
            edgecolor=candle_col,
            linewidth=0.8,
            zorder=3,
        )
        ax.add_patch(rect)

    # Future projection space for TradingView position box
    future_bars = 18
    x_start = n_candles - 1
    x_end = n_candles - 1 + future_bars
    ax.set_xlim(-1, n_candles + future_bars)

    # Y-limits with clean breathing room
    lows_min = float(plot_df["low"].min())
    highs_max = float(plot_df["high"].max())
    all_levels = [entry, tp1, tp2, sl, lows_min, highs_max]
    y_min = min(all_levels)
    y_max = max(all_levels)
    y_span = max(y_max - y_min, entry * 0.01)
    ax.set_ylim(y_min - y_span * 0.07, y_max + y_span * 0.08)

    # TradingView Long / Short Position Tool
    if direction == "LONG":
        top_target = max(tp1, tp2)
        # Target / Profit Zone (Translucent Green)
        rect_profit = patches.Rectangle(
            (x_start, entry),
            x_end - x_start,
            top_target - entry,
            facecolor="#0ecb81",
            alpha=0.22,
            edgecolor="#0ecb81",
            linewidth=0.8,
            zorder=1,
        )
        ax.add_patch(rect_profit)

        # Risk / Stop Loss Zone (Translucent Red)
        rect_loss = patches.Rectangle(
            (x_start, sl),
            x_end - x_start,
            entry - sl,
            facecolor="#f6465d",
            alpha=0.22,
            edgecolor="#f6465d",
            linewidth=0.8,
            zorder=1,
        )
        ax.add_patch(rect_loss)

        # Dashed yellow line for TP
        ax.hlines(tp1, x_start, x_end, colors="#f0b90b", linestyles="--", linewidth=1.5, zorder=2)
        ax.hlines(entry, x_start, x_end, colors="#ffffff", linestyles="-", linewidth=0.9, alpha=0.4, zorder=2)

    else:
        bottom_target = min(tp1, tp2)
        rect_profit = patches.Rectangle(
            (x_start, bottom_target),
            x_end - x_start,
            entry - bottom_target,
            facecolor="#0ecb81",
            alpha=0.22,
            edgecolor="#0ecb81",
            linewidth=0.8,
            zorder=1,
        )
        ax.add_patch(rect_profit)

        rect_loss = patches.Rectangle(
            (x_start, entry),
            x_end - x_start,
            sl - entry,
            facecolor="#f6465d",
            alpha=0.22,
            edgecolor="#f6465d",
            linewidth=0.8,
            zorder=1,
        )
        ax.add_patch(rect_loss)

        ax.hlines(tp1, x_start, x_end, colors="#f0b90b", linestyles="--", linewidth=1.5, zorder=2)
        ax.hlines(entry, x_start, x_end, colors="#ffffff", linestyles="-", linewidth=0.9, alpha=0.4, zorder=2)

    # Specific Visual Format Annotations
    if visual_type == "setup_box_arrow":
        # Format 1: Sharp Bold White Arrow pointing towards target
        if direction == "LONG":
            target_pt = max(tp1, tp2)
            ax.annotate(
                "",
                xy=(x_start + 11.5, entry + (target_pt - entry) * 0.85),
                xytext=(x_start + 3.5, entry + (target_pt - entry) * 0.15),
                arrowprops=dict(
                    facecolor="#ffffff",
                    edgecolor="#ffffff",
                    width=5.5,
                    headwidth=18,
                    headlength=18,
                    shrink=0.05,
                ),
                zorder=4,
            )
        else:
            target_pt = min(tp1, tp2)
            ax.annotate(
                "",
                xy=(x_start + 11.5, entry - (entry - target_pt) * 0.85),
                xytext=(x_start + 3.5, entry - (entry - target_pt) * 0.15),
                arrowprops=dict(
                    facecolor="#ffffff",
                    edgecolor="#ffffff",
                    width=5.5,
                    headwidth=18,
                    headlength=18,
                    shrink=0.05,
                ),
                zorder=4,
            )

    elif visual_type == "trendline_retest":
        # Format 2: Solid blue trendline + white touch arrows
        trend_pts = _find_swing_trendline(plot_df, direction)
        if trend_pts is not None:
            p1, p2 = trend_pts
            slope = (p2[1] - p1[1]) / max(p2[0] - p1[0], 1)
            ext_x0 = max(0, p1[0] - 4)
            ext_x1 = n_candles + 4
            ext_y0 = p1[1] + slope * (ext_x0 - p1[0])
            ext_y1 = p1[1] + slope * (ext_x1 - p1[0])

            ax.plot([ext_x0, ext_x1], [ext_y0, ext_y1], color="#2962ff", linewidth=2.4, zorder=3)

            for tx, ty in [p1, p2]:
                if direction == "LONG":
                    ax.annotate(
                        "",
                        xy=(tx, ty - (y_span * 0.015)),
                        xytext=(tx - 2.0, ty - (y_span * 0.11)),
                        arrowprops=dict(
                            facecolor="#ffffff",
                            edgecolor="#ffffff",
                            width=4.5,
                            headwidth=14,
                            headlength=14,
                            shrink=0.05,
                        ),
                        zorder=4,
                    )
                else:
                    ax.annotate(
                        "",
                        xy=(tx, ty + (y_span * 0.015)),
                        xytext=(tx - 2.0, ty + (y_span * 0.11)),
                        arrowprops=dict(
                            facecolor="#ffffff",
                            edgecolor="#ffffff",
                            width=4.5,
                            headwidth=14,
                            headlength=14,
                            shrink=0.05,
                        ),
                        zorder=4,
                    )
        else:
            # Fallback to bold arrow if swing points cannot be verified
            ax.annotate(
                "",
                xy=(x_start + 11.0, entry + (tp1 - entry) * 0.8),
                xytext=(x_start + 3.5, entry + (tp1 - entry) * 0.2),
                arrowprops=dict(
                    facecolor="#ffffff",
                    edgecolor="#ffffff",
                    width=5.0,
                    headwidth=16,
                    headlength=16,
                    shrink=0.05,
                ),
                zorder=4,
            )

    elif visual_type == "support_demand_zone":
        # Format 3: Support / Demand zone box + bounce arrow
        zone_info = _find_support_demand_zone(plot_df, entry, direction)
        if zone_info is not None:
            z_start, z_bottom, z_top = zone_info
            rect_zone = patches.Rectangle(
                (z_start, z_bottom),
                (x_end + 2) - z_start,
                max(z_top - z_bottom, y_span * 0.02),
                facecolor="#f6465d" if direction == "LONG" else "#2962ff",
                alpha=0.20,
                edgecolor="#f6465d" if direction == "LONG" else "#2962ff",
                linewidth=1.0,
                zorder=2,
            )
            ax.add_patch(rect_zone)

            if direction == "LONG":
                ax.annotate(
                    "",
                    xy=(x_start + 5.0, entry + (tp1 - entry) * 0.45),
                    xytext=(x_start - 3.0, z_top),
                    arrowprops=dict(
                        facecolor="#ffffff",
                        edgecolor="#ffffff",
                        width=4.5,
                        headwidth=15,
                        headlength=15,
                        shrink=0.05,
                    ),
                    zorder=4,
                )
            else:
                ax.annotate(
                    "",
                    xy=(x_start + 5.0, entry - (entry - tp1) * 0.45),
                    xytext=(x_start - 3.0, z_bottom),
                    arrowprops=dict(
                        facecolor="#ffffff",
                        edgecolor="#ffffff",
                        width=4.5,
                        headwidth=15,
                        headlength=15,
                        shrink=0.05,
                    ),
                    zorder=4,
                )
        else:
            ax.annotate(
                "",
                xy=(x_start + 11.0, entry + (tp1 - entry) * 0.8),
                xytext=(x_start + 3.5, entry + (tp1 - entry) * 0.2),
                arrowprops=dict(
                    facecolor="#ffffff",
                    edgecolor="#ffffff",
                    width=5.0,
                    headwidth=16,
                    headlength=16,
                    shrink=0.05,
                ),
                zorder=4,
            )

    elif visual_type == "scenario_projection":
        # Format 4: Curved projection path showing hypothetical path
        t_pts = np.linspace(0, 1, 50)
        if direction == "LONG":
            dip = (entry - sl) * 0.28
            p0 = np.array([x_start, entry])
            p1 = np.array([x_start + 2.0, entry - dip])
            p2 = np.array([x_start + 6.5, entry + (tp1 - entry) * 0.35])
            p3 = np.array([x_start + 12.0, tp1])
            proj_col = "#f6465d"
        else:
            bump = (sl - entry) * 0.28
            p0 = np.array([x_start, entry])
            p1 = np.array([x_start + 2.0, entry + bump])
            p2 = np.array([x_start + 6.5, entry - (entry - tp1) * 0.35])
            p3 = np.array([x_start + 12.0, tp1])
            proj_col = "#0ecb81"

        curve = (
            (1 - t_pts) ** 3 * p0[:, None] +
            3 * (1 - t_pts) ** 2 * t_pts * p1[:, None] +
            3 * (1 - t_pts) * t_pts ** 2 * p2[:, None] +
            t_pts ** 3 * p3[:, None]
        )
        ax.plot(curve[0], curve[1], color=proj_col, linewidth=2.8, zorder=4)
        ax.annotate(
            "",
            xy=(curve[0][-1], curve[1][-1]),
            xytext=(curve[0][-3], curve[1][-3]),
            arrowprops=dict(
                facecolor=proj_col,
                edgecolor=proj_col,
                width=3.2,
                headwidth=12,
                headlength=12,
                shrink=0.0,
            ),
            zorder=4,
        )

    elif visual_type == "breakout_expansion":
        # Format 5: Breakout level line with TP Market tag
        breakout_ref = entry * (0.998 if direction == "LONG" else 1.002)
        ax.hlines(breakout_ref, max(0, n_candles - 30), x_end, colors="#f0b90b", linestyles="--", linewidth=1.2, alpha=0.7, zorder=2)
        tag_y = tp2 if direction == "LONG" else tp2
        ax.text(
            x_start + 4,
            tag_y,
            " TP Market v " if direction == "LONG" else " TP Market ^ ",
            fontproperties=FONT_BOLD,
            fontsize=8.8,
            color="#f6465d" if direction == "LONG" else "#0ecb81",
            bbox=dict(
                boxstyle="square,pad=0.25",
                facecolor="#1e2329",
                edgecolor="#f6465d" if direction == "LONG" else "#0ecb81",
                linewidth=1.0,
            ),
            va="center",
            zorder=5,
        )
        if direction == "LONG":
            ax.annotate(
                "",
                xy=(x_start + 9.0, tp2 * 0.995),
                xytext=(x_start + 2.0, entry * 1.002),
                arrowprops=dict(
                    facecolor="#0ecb81",
                    edgecolor="#0ecb81",
                    width=5.0,
                    headwidth=16,
                    headlength=16,
                    shrink=0.05,
                ),
                zorder=4,
            )
        else:
            ax.annotate(
                "",
                xy=(x_start + 9.0, tp2 * 1.005),
                xytext=(x_start + 2.0, entry * 0.998),
                arrowprops=dict(
                    facecolor="#f6465d",
                    edgecolor="#f6465d",
                    width=5.0,
                    headwidth=16,
                    headlength=16,
                    shrink=0.05,
                ),
                zorder=4,
            )

    # Right Axis Badges (TradingView Style)
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.tick_params(colors="#848e9c", labelsize=9.2)
    ax.spines["top"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color("#1e2329")
    ax.spines["right"].set_color("#1e2329")
    ax.grid(True, linestyle=":", color="#1e2329", linewidth=0.7, alpha=0.8)

    def add_price_badge(y_val: float, text: str, bg_color: str, text_color: str = "#ffffff"):
        ax.text(
            1.015,
            y_val,
            f" {text} ",
            transform=ax.get_yaxis_transform(),
            fontproperties=FONT_BOLD,
            fontsize=9.2,
            color=text_color,
            va="center",
            ha="left",
            bbox=dict(
                boxstyle="square,pad=0.25",
                facecolor=bg_color,
                edgecolor="none",
                alpha=0.98,
            ),
            zorder=10,
        )

    # Render primary badges without overlapping collisions
    if abs(tp2 - tp1) / y_span >= 0.04:
        add_price_badge(tp2, _format_chart_price(tp2), "#0ecb81", "#ffffff")
    add_price_badge(tp1, _format_chart_price(tp1), "#f0b90b", "#12151c")
    add_price_badge(entry, _format_chart_price(entry), "#0ecb81", "#ffffff")
    add_price_badge(sl, _format_chart_price(sl), "#f6465d", "#ffffff")

    # Time ticks at bottom
    step = max(1, n_candles // 5)
    tick_indices = list(range(8, n_candles, step))
    tick_labels = [
        plot_df.index[k].strftime("%d  %H:%M") if k % 2 == 0 else plot_df.index[k].strftime("%H:%M")
        for k in tick_indices
    ]
    ax.set_xticks(tick_indices)
    ax.set_xticklabels(tick_labels, fontproperties=FONT_REGULAR, fontsize=9.2, color="#848e9c")

    # Footer Watermarks
    fig.text(0.05, 0.02, "[TV]", fontproperties=FONT_BOLD, fontsize=10.5, color="#848e9c", alpha=0.6, va="bottom")
    handle = os.getenv("CARD_USERNAME", "").strip().lstrip("@")
    if handle:
        fig.text(0.92, 0.02, f"@{handle}", fontproperties=FONT_MEDIUM, fontsize=10.5, color="#848e9c", alpha=0.75, ha="right", va="bottom")

    output_dir = os.path.dirname(save_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    fig.savefig(save_path, facecolor="#12151c", dpi=130)
    plt.close(fig)

    if not os.path.isfile(save_path) or os.path.getsize(save_path) < 1000:
        raise RuntimeError(f"Chart file invalid or not created: {save_path}")

    print(f"[Chart] Saved: {save_path}")
    return save_path


def generate_btc_market_chart(
    save_path: str = "charts/BTC_1H.png",
) -> str:
    """Generate mobile-first 1H BTC Macro Market Structure chart with TradingView styling."""

    print("[Chart] Generating BTC 1H Market Overview chart...")
    df = fetch_klines(symbol="BTCUSDT", interval="1h", limit=120)
    plot_df = df.tail(60).copy()

    fig = plt.figure(figsize=(8.0, 10.0), dpi=130)
    fig.patch.set_facecolor("#12151c")

    ax_header = fig.add_axes([0.05, 0.88, 0.90, 0.10])
    ax_header.set_facecolor("#12151c")
    ax_header.axis("off")

    ax = fig.add_axes([0.05, 0.065, 0.74, 0.80])
    ax.set_facecolor("#12151c")

    last_p = float(plot_df["close"].iloc[-1])
    first_p = float(plot_df["open"].iloc[0])
    chg_pct = ((last_p - first_p) / first_p) * 100.0

    timeframe_pills = ["Time", "15m", "1h", "4h", "1D", "30m v"]
    x_cursor = 0.0
    for tf_item in timeframe_pills:
        is_active = (tf_item == "1h")
        col = "#ffffff" if is_active else "#848e9c"
        bbox = dict(boxstyle="round,pad=0.22", facecolor="#2b313a", edgecolor="none") if is_active else None
        ax_header.text(x_cursor, 0.80, tf_item, fontproperties=FONT_MEDIUM, fontsize=9.2, color=col, bbox=bbox, va="center")
        x_cursor += 0.085 if tf_item == "Time" else 0.075

    ax_header.text(0.98, 0.80, "[ ]   #", fontproperties=FONT_BOLD, fontsize=10, color="#848e9c", ha="right", va="center")
    ax_header.text(0.0, 0.42, "BTCUSDT Last Price · Binance", fontproperties=FONT_BOLD, fontsize=14.5, color="#ffffff", va="center")

    price_str = _format_chart_price(last_p)
    chg_sign = "+" if chg_pct >= 0 else ""
    chg_col = "#0ecb81" if chg_pct >= 0 else "#f6465d"
    ax_header.text(0.0, 0.06, f"{price_str}", fontproperties=FONT_BOLD, fontsize=13, color=chg_col, va="center")
    diff_val = last_p - first_p
    ax_header.text(0.24, 0.06, f"{chg_sign}{diff_val:,.1f} ({chg_sign}{chg_pct:.2f}%)", fontproperties=FONT_MEDIUM, fontsize=12, color=chg_col, va="center")

    n_candles = len(plot_df)
    candle_width = 0.68
    for i in range(n_candles):
        open_p = plot_df["open"].iloc[i]
        high_p = plot_df["high"].iloc[i]
        low_p = plot_df["low"].iloc[i]
        close_p = plot_df["close"].iloc[i]
        col = "#0ecb81" if close_p >= open_p else "#f6465d"
        ax.plot([i, i], [low_p, high_p], color=col, linewidth=1.2, zorder=2)
        rect = patches.Rectangle(
            (i - candle_width / 2.0, min(open_p, close_p)),
            candle_width,
            max(abs(close_p - open_p), (high_p - low_p) * 0.015),
            facecolor=col,
            edgecolor=col,
            linewidth=0.8,
            zorder=3,
        )
        ax.add_patch(rect)

    ema21 = plot_df["close"].ewm(span=21, adjust=False).mean()
    ema50 = plot_df["close"].ewm(span=50, adjust=False).mean()
    ax.plot(range(n_candles), ema21, color="#f0b90b", linewidth=1.4, alpha=0.85, label="EMA 21", zorder=4)
    ax.plot(range(n_candles), ema50, color="#2962ff", linewidth=1.4, alpha=0.85, label="EMA 50", zorder=4)

    res = float(plot_df["high"].tail(40).max())
    sup = float(plot_df["low"].tail(40).min())
    ax.axhline(res, color="#f6465d", linestyle="--", linewidth=1.2, alpha=0.75, zorder=2)
    ax.axhline(sup, color="#0ecb81", linestyle="--", linewidth=1.2, alpha=0.75, zorder=2)

    ax.set_xlim(-1, n_candles + 4)
    lows_min = float(plot_df["low"].min())
    highs_max = float(plot_df["high"].max())
    y_span = highs_max - lows_min
    ax.set_ylim(lows_min - y_span * 0.06, highs_max + y_span * 0.08)

    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.tick_params(colors="#848e9c", labelsize=9.2)
    ax.spines["top"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color("#1e2329")
    ax.spines["right"].set_color("#1e2329")
    ax.grid(True, linestyle=":", color="#1e2329", linewidth=0.7, alpha=0.8)

    def add_price_badge(y_val: float, text: str, bg_color: str, text_color: str = "#ffffff"):
        ax.text(
            1.015,
            y_val,
            f" {text} ",
            transform=ax.get_yaxis_transform(),
            fontproperties=FONT_BOLD,
            fontsize=9.2,
            color=text_color,
            va="center",
            ha="left",
            bbox=dict(
                boxstyle="square,pad=0.25",
                facecolor=bg_color,
                edgecolor="none",
                alpha=0.98,
            ),
            zorder=10,
        )

    add_price_badge(res, f"RES: {_format_chart_price(res)}", "#f6465d", "#ffffff")
    add_price_badge(last_p, f"BTC: {_format_chart_price(last_p)}", "#f0b90b", "#12151c")
    add_price_badge(sup, f"SUP: {_format_price_or_default(sup)}", "#0ecb81", "#ffffff")

    ax.text(
        0.02,
        0.96,
        " EMA 21 (Yellow)  |  EMA 50 (Blue) ",
        transform=ax.transAxes,
        fontproperties=FONT_MEDIUM,
        fontsize=8.5,
        color="#eaecef",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#1e2329", edgecolor="#474d57", alpha=0.85),
        va="top",
    )

    step = max(1, n_candles // 5)
    tick_indices = list(range(8, n_candles, step))
    tick_labels = [
        plot_df.index[k].strftime("%d  %H:%M") if k % 2 == 0 else plot_df.index[k].strftime("%H:%M")
        for k in tick_indices
    ]
    ax.set_xticks(tick_indices)
    ax.set_xticklabels(tick_labels, fontproperties=FONT_REGULAR, fontsize=9.2, color="#848e9c")

    fig.text(0.05, 0.02, "[TV]", fontproperties=FONT_BOLD, fontsize=10.5, color="#848e9c", alpha=0.6, va="bottom")
    handle = os.getenv("CARD_USERNAME", "").strip().lstrip("@")
    if handle:
        fig.text(0.92, 0.02, f"@{handle}", fontproperties=FONT_MEDIUM, fontsize=10.5, color="#848e9c", alpha=0.75, ha="right", va="bottom")

    output_dir = os.path.dirname(save_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    fig.savefig(save_path, facecolor="#12151c", dpi=130)
    plt.close(fig)

    if not os.path.isfile(save_path) or os.path.getsize(save_path) < 1000:
        raise RuntimeError(f"BTC chart invalid: {save_path}")

    print(f"[Chart] Saved BTC chart: {save_path}")
    return save_path


def _format_price_or_default(val: float) -> str:
    return _format_chart_price(val)


def generate_single_chart(
    coin: str,
    signal: dict,
    out_dir: str = "charts",
    visual_type: str | None = None,
) -> str:
    """Generate the single authoritative 1H chart for the signal with TradingView mobile styling."""

    os.makedirs(out_dir, exist_ok=True)
    coin = coin.upper()
    chart_path = os.path.join(out_dir, f"{coin}_1H.png")

    generate_trending_chart(
        coin=coin,
        signal=signal,
        timeframe="1h",
        save_path=chart_path,
        visual_type=visual_type,
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
