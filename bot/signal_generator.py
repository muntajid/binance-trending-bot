"""Verified Binance market scanning and resilient Groq signal generation."""

from __future__ import annotations

import json
import os
from typing import Any

import requests
from groq import Groq

from bot.market_data import BASE_URL, get_24h_data

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
DEFAULT_GROQ_MODELS = (
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
)


def get_live_price(coin: str) -> float:
    """Get the authoritative live price from the verified market-data layer."""

    symbol = f"{str(coin).upper()}USDT"
    data = get_24h_data(symbol)

    if not data.get("verified"):
        raise RuntimeError(f"Market data not verified for {symbol}")

    price = float(data["price"])
    if price <= 0:
        raise RuntimeError(f"Invalid verified price for {symbol}: {price}")

    print(f"[Live Price - VERIFIED] {symbol} = ${price}")
    return price


def get_trending_coins(
    top_n: int = 30,
    min_change: float = 5.0,
    min_volume: float = 5_000_000,
) -> list[dict[str, Any]]:
    """Return positive-momentum USDT markets from verified Binance data."""

    response = requests.get(
        f"{BASE_URL}/api/v3/ticker/24hr",
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError("Invalid ticker list from Binance vision API.")

    excluded_symbols = {
        "USDCUSDT",
        "FDUSDUSDT",
        "TUSDUSDT",
        "DAIUSDT",
        "BUSDUSDT",
        "USDPUSDT",
    }
    leveraged_suffixes = (
        "UPUSDT",
        "DOWNUSDT",
        "BEARUSDT",
        "BULLUSDT",
    )
    filtered: list[dict[str, Any]] = []

    for item in data:
        try:
            symbol = str(item["symbol"]).upper()
            if not symbol.endswith("USDT"):
                continue
            if symbol in excluded_symbols:
                continue
            if any(marker in symbol for marker in leveraged_suffixes):
                continue

            change = float(item["priceChangePercent"])
            volume = float(item["quoteVolume"])
            price = float(item["lastPrice"])

            if price <= 0 or change < min_change or volume < min_volume:
                continue

            filtered.append(
                {
                    "coin": symbol[:-4],
                    "change": change,
                    "volume": volume,
                    "price": price,
                }
            )
        except (KeyError, TypeError, ValueError):
            continue

    filtered.sort(key=lambda item: item["change"], reverse=True)
    if not filtered:
        raise RuntimeError("No valid trending coins found from verified API.")

    return filtered[:top_n]


def _available_groq_models(api_key: str) -> set[str] | None:
    """Return models available to this API key, or None on a transient failure."""

    try:
        response = requests.get(
            GROQ_MODELS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=20,
        )

        if response.status_code in {401, 403}:
            raise RuntimeError(
                "GROQ_API_KEY was rejected while checking available models"
            )

        response.raise_for_status()
        payload = response.json()
        items = payload.get("data", []) if isinstance(payload, dict) else []
        models = {
            str(item.get("id", "")).strip()
            for item in items
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
        if not models:
            raise RuntimeError("Groq returned an empty model list")

        print(f"[Groq] Account exposes {len(models)} active model(s)")
        return models

    except RuntimeError:
        raise
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        print(
            f"[Groq Warning] Could not query the model list ({exc}); "
            "trying configured production fallbacks"
        )
        return None


def _groq_model_candidates(api_key: str) -> list[str]:
    """Build an account-aware, non-repeating production model preference list."""

    configured = os.getenv("GROQ_MODEL", "").strip()
    preferred = [configured, *DEFAULT_GROQ_MODELS]
    unique_preferred: list[str] = []
    for model in preferred:
        if model and model not in unique_preferred:
            unique_preferred.append(model)

    available = _available_groq_models(api_key)
    if available is None:
        return unique_preferred

    candidates = [model for model in unique_preferred if model in available]
    if not candidates:
        visible = ", ".join(sorted(available))
        raise RuntimeError(
            "None of the supported Groq signal models are available to this "
            f"account. Available model IDs: {visible}"
        )

    skipped = [model for model in unique_preferred if model not in available]
    if skipped:
        print("[Groq] Unavailable candidates skipped:", ", ".join(skipped))

    return candidates


def _parse_json_object(raw_content: Any) -> dict[str, Any]:
    """Parse a model response defensively while still requiring one JSON object."""

    text = str(raw_content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    value = json.loads(text)
    if not isinstance(value, dict):
        raise RuntimeError("Groq returned JSON that is not an object")
    return value


def _normalize_signal(
    result: dict[str, Any],
    *,
    coin: str,
    live_price: float,
    change: float,
    trending_bonus: str,
    model: str,
) -> dict[str, Any]:
    """Apply authoritative values and reject impossible TP/SL ordering."""

    direction = str(result.get("direction", "LONG")).upper().strip()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"Unsupported signal direction: {direction}")

    try:
        tp1 = float(result["tp1"])
        tp2 = float(result["tp2"])
        stop = float(result["sl"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Groq signal is missing numeric TP1, TP2, or SL") from exc

    if min(live_price, tp1, tp2, stop) <= 0:
        raise ValueError("Signal prices must all be greater than zero")

    if direction == "LONG" and not (stop < live_price < tp1 < tp2):
        raise ValueError("Invalid LONG levels: expected SL < Entry < TP1 < TP2")
    if direction == "SHORT" and not (tp2 < tp1 < live_price < stop):
        raise ValueError("Invalid SHORT levels: expected TP2 < TP1 < Entry < SL")

    normalized = {
        **result,
        "coin": str(coin).upper(),
        "trending_bonus": str(trending_bonus).upper(),
        "direction": direction,
        "entry": "CMP",
        "entry_price": live_price,
        "tp1": tp1,
        "tp2": tp2,
        "sl": stop,
        "leverage": "15x",
        "risk": str(result.get("risk", "Medium Risk")),
        "confidence": int(result.get("confidence", 80)),
        "smc_logic": str(
            result.get("smc_logic", "Momentum setup with defined risk levels.")
        ),
        "smc_logic_short": str(
            result.get("smc_logic_short", "Momentum setup.")
        ),
        "change": change,
        "groq_model": model,
    }
    return normalized


def _is_authentication_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "invalid api key",
            "authentication",
            "unauthorized",
            "status code: 401",
            "error code: 401",
        )
    )


def generate_signal_with_groq(
    coin: str,
    price: float,
    change: float,
    trending_bonus: str,
) -> dict[str, Any]:
    """Generate one validated signal with live model discovery and fallback."""

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found in GitHub Secrets.")

    supplied_price = float(price)
    if supplied_price <= 0:
        raise ValueError("Supplied signal price must be greater than zero")

    # Refresh the authoritative Binance price immediately before model use.
    live_price = get_live_price(coin)
    difference_percent = abs(live_price - supplied_price) / supplied_price * 100
    if difference_percent > 0.5:
        raise RuntimeError(
            f"Price mismatch detected for {coin}: "
            f"supplied={supplied_price}, verified={live_price}"
        )

    system_prompt = """
You generate structured crypto market-analysis setups from supplied verified data.
Use only the verified Binance price supplied by the user.
Never claim that an exchange order was executed.
Return exactly one valid JSON object and no prose outside that JSON object.
""".strip()

    user_prompt = f"""
Coin: {str(coin).upper()}
Verified Binance live price: {live_price}
24-hour change: {float(change):.2f}%
Context coin: {str(trending_bonus).upper()}

Return exactly this JSON structure:
{{
  "coin": "{str(coin).upper()}",
  "trending_bonus": "{str(trending_bonus).upper()}",
  "direction": "LONG",
  "entry": "CMP",
  "entry_price": {live_price},
  "tp1": <numeric analysis level above entry>,
  "tp2": <numeric analysis level above TP1>,
  "sl": <numeric analysis level below entry>,
  "leverage": "15x",
  "risk": "Medium Risk",
  "confidence": 80,
  "smc_logic": "Concise market explanation.",
  "smc_logic_short": "Short chart annotation."
}}

Rules:
1. entry_price must equal {live_price} exactly.
2. For LONG, enforce SL < entry_price < TP1 < TP2.
3. Use numeric values for entry_price, TP1, TP2, and SL.
4. Do not invent a different current market price.
5. Return JSON only.
""".strip()

    client = Groq(api_key=api_key)
    candidates = _groq_model_candidates(api_key)
    failures: list[str] = []

    for model in candidates:
        print(f"[Groq] Generating signal with model: {model}")
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=800,
                response_format={"type": "json_object"},
            )
            result = _parse_json_object(
                completion.choices[0].message.content
            )
            normalized = _normalize_signal(
                result,
                coin=coin,
                live_price=live_price,
                change=float(change),
                trending_bonus=trending_bonus,
                model=model,
            )
            print(f"[Groq] Signal validated with {model}")
            return normalized

        except Exception as exc:
            if _is_authentication_error(exc):
                raise RuntimeError("GROQ_API_KEY authentication failed") from exc

            failure = f"{model}: {type(exc).__name__}: {exc}"
            failures.append(failure)
            print(f"[Groq Warning] {failure}")

    summary = " | ".join(failures) if failures else "no candidate model was attempted"
    raise RuntimeError(f"All Groq signal models failed: {summary}")
