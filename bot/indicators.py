"""Dependency-free technical indicators derived from validated OHLCV candles.

All public helpers are deliberately defensive: malformed/empty input produces
neutral values or empty structures instead of stopping signal generation.
"""

from __future__ import annotations

from math import isfinite, sqrt
from typing import Any, Iterable


def _period(value: Any, default: int) -> int:
    try:
        result = int(value)
        return result if result > 0 else default
    except (TypeError, ValueError, OverflowError):
        return default


def _finite_values(values: Iterable[Any] | Any) -> list[float]:
    """Return finite floats from a sequence, ignoring malformed/NaN entries."""
    try:
        iterator = iter(values)
    except TypeError:
        return []

    result: list[float] = []
    try:
        for value in iterator:
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if isfinite(number):
                result.append(number)
    except Exception:
        return result
    return result


def _candle(candle: Any) -> dict[str, float] | None:
    """Read one OHLCV mapping or a raw Binance kline row safely."""
    try:
        if isinstance(candle, dict):
            opening = float(candle.get("open"))
            high = float(candle.get("high"))
            low = float(candle.get("low"))
            close = float(candle.get("close"))
            volume = float(candle.get("volume", 0.0))
        elif isinstance(candle, (list, tuple)) and len(candle) >= 6:
            opening, high, low, close, volume = map(float, candle[1:6])
        else:
            return None

        if not all(isfinite(value) for value in (opening, high, low, close, volume)):
            return None
        if min(opening, high, low, close) <= 0 or volume < 0:
            return None
        if low > high or high < max(opening, close) or low > min(opening, close):
            return None
        return {
            "open": opening,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    except (TypeError, ValueError, OverflowError, KeyError):
        return None


def _candles(klines: Iterable[Any] | Any) -> list[dict[str, float]]:
    try:
        return [parsed for row in klines if (parsed := _candle(row)) is not None]
    except (TypeError, Exception):
        return []


def _swing_points(
    candles: list[dict[str, float]], radius: int = 2
) -> tuple[list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    if len(candles) < 3:
        return highs, lows

    radius = max(1, min(radius, (len(candles) - 1) // 2))
    for index in range(radius, len(candles) - radius):
        window = candles[index - radius : index + radius + 1]
        high = candles[index]["high"]
        low = candles[index]["low"]
        if high >= max(item["high"] for item in window) and any(
            high > item["high"] for offset, item in enumerate(window) if offset != radius
        ):
            highs.append(high)
        if low <= min(item["low"] for item in window) and any(
            low < item["low"] for offset, item in enumerate(window) if offset != radius
        ):
            lows.append(low)
    return highs, lows


def calculate_ema(prices: Iterable[Any], period: int) -> list[float]:
    """Return an exponentially weighted moving-average series."""
    try:
        values = _finite_values(prices)
        if not values:
            return []
        length = _period(period, 1)
        multiplier = 2.0 / (length + 1.0)
        current = values[0]
        output = [current]
        for price in values[1:]:
            current = (price - current) * multiplier + current
            output.append(current)
        return output
    except Exception:
        return []


def calculate_rsi(prices: Iterable[Any], period: int = 14) -> float:
    """Return the latest RSI using a rolling average of gains and losses."""
    try:
        values = _finite_values(prices)
        if len(values) < 2:
            return 50.0
        length = _period(period, 14)
        changes = [values[index] - values[index - 1] for index in range(1, len(values))]
        window = changes[-length:]
        gains = sum(max(change, 0.0) for change in window) / len(window)
        losses = sum(max(-change, 0.0) for change in window) / len(window)
        if losses == 0:
            return 50.0 if gains == 0 else 100.0
        if gains == 0:
            return 0.0
        result = 100.0 - (100.0 / (1.0 + gains / losses))
        return min(100.0, max(0.0, result)) if isfinite(result) else 50.0
    except Exception:
        return 50.0


def calculate_macd(prices: Iterable[Any]) -> dict[str, float]:
    """Return the latest 12/26/9 MACD line, signal, and histogram."""
    neutral = {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
    try:
        values = _finite_values(prices)
        if not values:
            return neutral
        fast = calculate_ema(values, 12)
        slow = calculate_ema(values, 26)
        line = [fast_value - slow_value for fast_value, slow_value in zip(fast, slow)]
        signal_line = calculate_ema(line, 9)
        if not line or not signal_line:
            return neutral
        macd_value = line[-1]
        signal_value = signal_line[-1]
        histogram = macd_value - signal_value
        if not all(isfinite(value) for value in (macd_value, signal_value, histogram)):
            return neutral
        return {"macd": macd_value, "signal": signal_value, "histogram": histogram}
    except Exception:
        return neutral


def calculate_bollinger_bands(
    prices: Iterable[Any], period: int = 20, std: float = 2
) -> dict[str, float]:
    """Return the latest Bollinger bands and normalized price position."""
    neutral = {
        "upper": 0.0,
        "middle": 0.0,
        "lower": 0.0,
        "bandwidth": 0.0,
        "position": 0.5,
    }
    try:
        values = _finite_values(prices)
        if not values:
            return neutral
        length = _period(period, 20)
        deviations = float(std)
        if not isfinite(deviations) or deviations < 0:
            deviations = 2.0
        window = values[-length:]
        middle = sum(window) / len(window)
        variance = sum((value - middle) ** 2 for value in window) / len(window)
        width = deviations * sqrt(max(variance, 0.0))
        upper = middle + width
        lower = middle - width
        bandwidth = ((upper - lower) / abs(middle) * 100.0) if middle else 0.0
        position = (values[-1] - lower) / (upper - lower) if upper > lower else 0.5
        result = (upper, middle, lower, bandwidth, position)
        if not all(isfinite(value) for value in result):
            return neutral
        return {
            "upper": upper,
            "middle": middle,
            "lower": lower,
            "bandwidth": bandwidth,
            "position": position,
        }
    except Exception:
        return neutral


def calculate_stoch_rsi(
    rsi_values: Iterable[Any], period: int = 14
) -> dict[str, float]:
    """Return the latest stochastic RSI %K and smoothed %D (0-100)."""
    neutral = {"k": 50.0, "d": 50.0}
    try:
        values = _finite_values(rsi_values)
        if not values:
            return neutral
        length = _period(period, 14)
        raw_values: list[float] = []
        for index, value in enumerate(values):
            window = values[max(0, index - length + 1) : index + 1]
            low, high = min(window), max(window)
            raw = 50.0 if high == low else (value - low) / (high - low) * 100.0
            raw_values.append(min(100.0, max(0.0, raw)))
        k_value = raw_values[-1]
        d_window = raw_values[-3:]
        d_value = sum(d_window) / len(d_window)
        return {"k": k_value, "d": d_value}
    except Exception:
        return neutral


def calculate_vwap(klines: Iterable[Any]) -> float:
    """Return a volume-weighted typical price from OHLCV candles."""
    try:
        candles = _candles(klines)
        total_volume = sum(candle["volume"] for candle in candles)
        if total_volume <= 0:
            return 0.0
        weighted = sum(
            ((candle["high"] + candle["low"] + candle["close"]) / 3.0)
            * candle["volume"]
            for candle in candles
        )
        result = weighted / total_volume
        return result if isfinite(result) else 0.0
    except Exception:
        return 0.0


def detect_candlestick_patterns(klines: Iterable[Any]) -> list[str]:
    """Detect common reversal patterns on the most recent valid candles."""
    try:
        candles = _candles(klines)
        if not candles:
            return []
        patterns: list[str] = []
        current = candles[-1]
        body = abs(current["close"] - current["open"])
        candle_range = current["high"] - current["low"]
        upper_wick = current["high"] - max(current["open"], current["close"])
        lower_wick = min(current["open"], current["close"]) - current["low"]

        if candle_range > 0 and body <= candle_range * 0.10:
            patterns.append("doji")
        if (
            candle_range > 0
            and body > 0
            and body <= candle_range * 0.35
            and lower_wick >= body * 2.0
            and upper_wick <= body
        ):
            patterns.append("hammer")
        if (
            candle_range > 0
            and body > 0
            and body <= candle_range * 0.35
            and upper_wick >= body * 2.0
            and lower_wick <= body
        ):
            patterns.append("shooting_star")

        if len(candles) >= 2:
            previous = candles[-2]
            previous_bearish = previous["close"] < previous["open"]
            previous_bullish = previous["close"] > previous["open"]
            current_bullish = current["close"] > current["open"]
            current_bearish = current["close"] < current["open"]
            if (
                previous_bearish
                and current_bullish
                and current["open"] <= previous["close"]
                and current["close"] >= previous["open"]
            ):
                patterns.append("bullish_engulfing")
            if (
                previous_bullish
                and current_bearish
                and current["open"] >= previous["close"]
                and current["close"] <= previous["open"]
            ):
                patterns.append("bearish_engulfing")

        if len(candles) >= 3:
            first, middle, last = candles[-3:]
            first_body = abs(first["close"] - first["open"])
            middle_body = abs(middle["close"] - middle["open"])
            first_midpoint = (first["open"] + first["close"]) / 2.0
            if (
                first["close"] < first["open"]
                and middle_body <= first_body * 0.35
                and last["close"] > last["open"]
                and last["close"] >= first_midpoint
            ):
                patterns.append("morning_star")

        return list(dict.fromkeys(patterns))
    except Exception:
        return []


def find_support_resistance(
    klines: Iterable[Any], lookback: int = 50
) -> dict[str, Any]:
    """Find local swing levels and the nearest support/resistance to last close."""
    empty = {"support": None, "resistance": None, "levels": []}
    try:
        candles = _candles(klines)
        if not candles:
            return empty
        count = _period(lookback, 50)
        candles = candles[-count:]
        current = candles[-1]["close"]
        swing_highs, swing_lows = _swing_points(candles)
        raw_levels = swing_highs + swing_lows
        if not raw_levels:
            raw_levels = [
                max(candle["high"] for candle in candles),
                min(candle["low"] for candle in candles),
            ]

        # Merge nearby pivots into one practical zone/level.
        groups: list[list[float]] = []
        for value in sorted(raw_levels):
            if not groups or abs(value - (sum(groups[-1]) / len(groups[-1]))) / max(value, 1e-12) > 0.005:
                groups.append([value])
            else:
                groups[-1].append(value)
        levels = sorted(sum(group) / len(group) for group in groups)
        below = [level for level in levels if level < current]
        above = [level for level in levels if level > current]
        support = max(below) if below else min(candle["low"] for candle in candles)
        resistance = min(above) if above else max(candle["high"] for candle in candles)
        return {"support": support, "resistance": resistance, "levels": levels}
    except Exception:
        return empty


def detect_trendlines(klines: Iterable[Any]) -> dict[str, Any]:
    """Classify recent swing structure and basic higher/lower-high sequences."""
    neutral = {
        "trend": "sideways",
        "higher_highs": False,
        "higher_lows": False,
        "lower_highs": False,
        "lower_lows": False,
        "structure": "range",
    }
    try:
        candles = _candles(klines)
        if len(candles) < 2:
            return neutral
        candles = candles[-50:]
        swing_highs, swing_lows = _swing_points(candles)
        if len(swing_highs) < 2 and len(candles) >= 4:
            midpoint = len(candles) // 2
            swing_highs = [
                max(item["high"] for item in candles[:midpoint]),
                max(item["high"] for item in candles[midpoint:]),
            ]
        if len(swing_lows) < 2 and len(candles) >= 4:
            midpoint = len(candles) // 2
            swing_lows = [
                min(item["low"] for item in candles[:midpoint]),
                min(item["low"] for item in candles[midpoint:]),
            ]

        higher_highs = len(swing_highs) >= 2 and swing_highs[-1] > swing_highs[-2]
        lower_highs = len(swing_highs) >= 2 and swing_highs[-1] < swing_highs[-2]
        higher_lows = len(swing_lows) >= 2 and swing_lows[-1] > swing_lows[-2]
        lower_lows = len(swing_lows) >= 2 and swing_lows[-1] < swing_lows[-2]

        if higher_highs and higher_lows:
            trend, structure = "uptrend", "HH/HL"
        elif lower_highs and lower_lows:
            trend, structure = "downtrend", "LH/LL"
        elif higher_highs and lower_lows:
            trend, structure = "sideways", "HH/LL"
        elif lower_highs and higher_lows:
            trend, structure = "sideways", "LH/HL"
        else:
            closes = [candle["close"] for candle in candles]
            base = closes[0]
            change = ((closes[-1] - base) / base) if base else 0.0
            if change > 0.01:
                trend, structure = "uptrend", "higher closes"
            elif change < -0.01:
                trend, structure = "downtrend", "lower closes"
            else:
                trend, structure = "sideways", "range"

        return {
            "trend": trend,
            "higher_highs": bool(higher_highs),
            "higher_lows": bool(higher_lows),
            "lower_highs": bool(lower_highs),
            "lower_lows": bool(lower_lows),
            "structure": structure,
        }
    except Exception:
        return neutral


def detect_breakout_breakdown(
    klines: Iterable[Any], levels: dict[str, Any] | Iterable[Any]
) -> dict[str, Any]:
    """Detect a closing-price break beyond a supplied support/resistance level."""
    neutral = {"type": "none", "level": None, "confirmed": False}
    try:
        candles = _candles(klines)
        if not candles:
            return neutral
        support = resistance = None
        if isinstance(levels, dict):
            support = levels.get("support")
            resistance = levels.get("resistance")
            level_values = _finite_values(levels.get("levels", []))
        else:
            level_values = _finite_values(levels)
        current = candles[-1]["close"]
        previous = candles[-2]["close"] if len(candles) > 1 else current
        if resistance is None:
            candidates = [level for level in level_values if level > previous]
            resistance = min(candidates) if candidates else None
        if support is None:
            candidates = [level for level in level_values if level < previous]
            support = max(candidates) if candidates else None

        if resistance is not None:
            resistance = float(resistance)
            if resistance > 0 and current > resistance:
                return {"type": "breakout", "level": resistance, "confirmed": True}
        if support is not None:
            support = float(support)
            if support > 0 and current < support:
                return {"type": "breakdown", "level": support, "confirmed": True}
        return neutral
    except Exception:
        return neutral


def detect_market_structure(klines: Iterable[Any]) -> str:
    """Return a compact human-readable structure/trend description."""
    try:
        candles = _candles(klines)
        if not candles:
            return "insufficient data"
        levels = find_support_resistance(candles)
        breakout = detect_breakout_breakdown(candles, levels)
        if breakout["confirmed"]:
            side = "bullish" if breakout["type"] == "breakout" else "bearish"
            return f"BOS detected ({side})"
        structure = detect_trendlines(candles)
        if structure["trend"] == "uptrend":
            return "HH-HL bullish continuation"
        if structure["trend"] == "downtrend":
            return "LL-LH bearish continuation"
        if structure["structure"] in {"HH/LL", "LH/HL"}:
            return f"mixed structure ({structure['structure']})"
        return "sideways consolidation"
    except Exception:
        return "insufficient data"


def calculate_atr(klines: Iterable[Any], period: int = 14) -> float:
    """Return the simple-average Average True Range for the latest candles."""
    try:
        candles = _candles(klines)
        if not candles:
            return 0.0
        length = _period(period, 14)
        true_ranges: list[float] = []
        previous_close: float | None = None
        for candle in candles:
            if previous_close is None:
                true_range = candle["high"] - candle["low"]
            else:
                true_range = max(
                    candle["high"] - candle["low"],
                    abs(candle["high"] - previous_close),
                    abs(candle["low"] - previous_close),
                )
            true_ranges.append(true_range)
            previous_close = candle["close"]
        result = sum(true_ranges[-length:]) / len(true_ranges[-length:])
        return result if isfinite(result) and result >= 0 else 0.0
    except Exception:
        return 0.0


def calculate_all_indicators(klines: Iterable[Any]) -> dict[str, Any]:
    """Compute the complete stable indicator bundle used by signal analysis."""
    try:
        candles = _candles(klines)
        closes = [candle["close"] for candle in candles]
        ema_20 = calculate_ema(closes, 20)
        ema_50 = calculate_ema(closes, 50)
        ema_200 = calculate_ema(closes, 200)
        rsi_series = [calculate_rsi(closes[: index + 1]) for index in range(len(closes))]
        levels = find_support_resistance(candles)
        return {
            "close": closes[-1] if closes else 0.0,
            "ema_20": ema_20[-1] if ema_20 else 0.0,
            "ema_50": ema_50[-1] if ema_50 else 0.0,
            "ema_200": ema_200[-1] if ema_200 else 0.0,
            "rsi": calculate_rsi(closes),
            "macd": calculate_macd(closes),
            "bollinger_bands": calculate_bollinger_bands(closes),
            "stoch_rsi": calculate_stoch_rsi(rsi_series),
            "vwap": calculate_vwap(candles),
            "candlestick_patterns": detect_candlestick_patterns(candles),
            "support_resistance": levels,
            "trendlines": detect_trendlines(candles),
            "breakout_breakdown": detect_breakout_breakdown(candles, levels),
            "market_structure": detect_market_structure(candles),
            "atr": calculate_atr(candles),
        }
    except Exception:
        return {
            "close": 0.0,
            "ema_20": 0.0,
            "ema_50": 0.0,
            "ema_200": 0.0,
            "rsi": 50.0,
            "macd": {"macd": 0.0, "signal": 0.0, "histogram": 0.0},
            "bollinger_bands": {
                "upper": 0.0,
                "middle": 0.0,
                "lower": 0.0,
                "bandwidth": 0.0,
                "position": 0.5,
            },
            "stoch_rsi": {"k": 50.0, "d": 50.0},
            "vwap": 0.0,
            "candlestick_patterns": [],
            "support_resistance": {"support": None, "resistance": None, "levels": []},
            "trendlines": {
                "trend": "sideways",
                "higher_highs": False,
                "higher_lows": False,
                "lower_highs": False,
                "lower_lows": False,
                "structure": "range",
            },
            "breakout_breakdown": {"type": "none", "level": None, "confirmed": False},
            "market_structure": "insufficient data",
            "atr": 0.0,
        }
