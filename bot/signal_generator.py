"""Verified Binance market scanning and resilient Groq signal, macro overview, and meme generation."""

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
            "None of the supported Groq models are available to this "
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

    why_now = str(
        result.get(
            "why_now",
            f"Price broke above key 1H resistance with strong {change:+.1f}% 24H volume expansion.",
        )
    ).strip()

    invalidation = str(
        result.get(
            "invalidation",
            f"A 1H candle close below ${stop} invalidates this setup.",
        )
    ).strip()

    rapid_reason = str(
        result.get(
            "rapid_reason",
            f"1H momentum breakout above key resistance with volume.",
        )
    ).strip()

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
        "confidence": int(result.get("confidence", 85)),
        "why_now": why_now,
        "invalidation": invalidation,
        "rapid_reason": rapid_reason,
        "smc_logic": str(result.get("smc_logic", why_now)),
        "smc_logic_short": str(result.get("smc_logic_short", rapid_reason)),
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
    """Generate one validated signal with Why Now and Invalidation fields."""

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found in GitHub Secrets.")

    supplied_price = float(price)
    if supplied_price <= 0:
        raise ValueError("Supplied signal price must be greater than zero")

    live_price = get_live_price(coin)
    difference_percent = abs(live_price - supplied_price) / supplied_price * 100
    if difference_percent > 0.5:
        raise RuntimeError(
            f"Price mismatch detected for {coin}: "
            f"supplied={supplied_price}, verified={live_price}"
        )

    system_prompt = """
You are an elite crypto technical analyst and Smart Money Concepts (SMC) trader.
You generate structured, high-accuracy market-analysis setups using ONLY the verified Binance price supplied by the user.
Never claim that an exchange order was executed.
Provide high-value, insightful, and professional market analysis with clear Why Now and Invalidation reasons.
Return exactly one valid JSON object and no prose outside that JSON object.
""".strip()

    user_prompt = f"""
Coin: {str(coin).upper()}
Verified Binance live price: {live_price}
24-hour change: {float(change):.2f}%
Context market leader: {str(trending_bonus).upper()}

Return exactly this JSON structure:
{{
  "coin": "{str(coin).upper()}",
  "trending_bonus": "{str(trending_bonus).upper()}",
  "direction": "LONG",
  "entry": "CMP",
  "entry_price": {live_price},
  "tp1": <numeric analysis level above entry, e.g. 3-6% realistic profit>,
  "tp2": <numeric analysis level above TP1, e.g. 8-15% extended profit>,
  "sl": <numeric analysis level below entry, e.g. 2.5-4% tight invalidation>,
  "leverage": "15x",
  "risk": "Medium Risk",
  "confidence": 85,
  "why_now": "One verified setup-specific reason explaining what just happened (liquidity sweep, volume accumulation, or resistance flip).",
  "invalidation": "One clear reclaim/break/close level that invalidates this trade thesis.",
  "rapid_reason": "One concise sentence summarizing the 1H momentum trigger.",
  "smc_logic": "2-3 sentences of clear Smart Money technical reasoning.",
  "smc_logic_short": "Short chart summary annotation."
}}

Rules:
1. entry_price must equal {live_price} exactly.
2. For LONG, enforce SL < entry_price < TP1 < TP2.
3. Use numeric values for entry_price, TP1, TP2, and SL.
4. Do not invent a different current market price.
5. Provide realistic, professional SMC rationale that educates and builds trust with traders.
6. Return JSON only.
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
            result = _parse_json_object(completion.choices[0].message.content)
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


def generate_market_overview_with_groq(
    top_coins: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate verified Macro & Bitcoin Market Overview using Binance market data and Groq."""

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found in GitHub Secrets.")

    btc_data = get_24h_data("BTCUSDT")
    eth_data = get_24h_data("ETHUSDT")

    btc_price = float(btc_data["price"])
    btc_change = float(btc_data["change_24h"])
    btc_volume = float(btc_data.get("quote_volume_24h", 0))

    eth_price = float(eth_data["price"])
    eth_change = float(eth_data["change_24h"])

    if not top_coins:
        try:
            top_coins = get_trending_coins(top_n=5, min_change=2.0, min_volume=3_000_000)
        except Exception:
            top_coins = []

    gainers_summary = ", ".join(
        [f"{c['coin']} ({c['change']:+.1f}%)" for c in top_coins[:4]]
    ) if top_coins else "Altcoin momentum mixed"

    system_prompt = """
You are a top-tier institutional crypto macro and technical strategist on Binance Square.
You analyze Bitcoin structure, liquidity flows, dominance, and altcoin rotation from verified Binance data.
Provide crisp, insightful, and authoritative market intelligence for active traders.
Return exactly one valid JSON object and no prose outside that JSON object.
""".strip()

    user_prompt = f"""
Verified Binance Market Data:
• BTC/USDT: ${btc_price:,.2f} ({btc_change:+.2f}% 24H, 24H Vol: ${btc_volume:,.0f})
• ETH/USDT: ${eth_price:,.2f} ({eth_change:+.2f}% 24H)
• Top Momentum Gainers: {gainers_summary}

Return exactly this JSON structure:
{{
  "headline": "Punchy market headline summarizing BTC structure and current market pulse",
  "btc_price": {btc_price},
  "btc_change": {btc_change},
  "market_phase": "e.g. Bullish Expansion / Consolidation / Key Resistance Retest / Liquidity Hunt",
  "btc_thesis": "1-2 sentences on Bitcoin 1H/4H price action, structure holding, and liquidity zones.",
  "altcoin_summary": "1-2 sentences explaining altcoin capital rotation and momentum leaders.",
  "btc_support": <numeric nearby key support level below btc_price>,
  "btc_resistance": <numeric nearby key resistance level above btc_price>,
  "strategy_outlook": "One clear, actionable rule/insight for spot and futures traders today."
}}

Rules:
1. btc_price must equal {btc_price}.
2. btc_support < btc_price < btc_resistance.
3. Keep the tone professional, objective, and analytical.
4. Return JSON only.
""".strip()

    client = Groq(api_key=api_key)
    candidates = _groq_model_candidates(api_key)
    failures: list[str] = []

    for model in candidates:
        print(f"[Groq] Generating market overview with model: {model}")
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.25,
                max_tokens=900,
                response_format={"type": "json_object"},
            )
            result = _parse_json_object(completion.choices[0].message.content)

            support = float(result.get("btc_support", btc_price * 0.98))
            resistance = float(result.get("btc_resistance", btc_price * 1.02))
            if support >= btc_price:
                support = round(btc_price * 0.98, 2)
            if resistance <= btc_price:
                resistance = round(btc_price * 1.02, 2)

            overview = {
                "headline": str(result.get("headline", "Bitcoin Consolidates as Momentum Expands across Altcoins")).strip(),
                "btc_price": btc_price,
                "btc_change": btc_change,
                "eth_price": eth_price,
                "eth_change": eth_change,
                "market_phase": str(result.get("market_phase", "Consolidation Range")).strip(),
                "btc_thesis": str(result.get("btc_thesis", "BTC is maintaining structure above local support as volume builds.")).strip(),
                "altcoin_summary": str(result.get("altcoin_summary", f"Momentum continues in selective altcoins: {gainers_summary}.")).strip(),
                "btc_support": support,
                "btc_resistance": resistance,
                "strategy_outlook": str(result.get("strategy_outlook", "Focus on high-volume setups with defined invalidation levels.")).strip(),
                "groq_model": model,
                "type": "market_overview",
            }
            print(f"[Groq] Market overview validated with {model}")
            return overview

        except Exception as exc:
            if _is_authentication_error(exc):
                raise RuntimeError("GROQ_API_KEY authentication failed") from exc

            failure = f"{model}: {type(exc).__name__}: {exc}"
            failures.append(failure)
            print(f"[Groq Warning] {failure}")

    summary = " | ".join(failures) if failures else "no candidate model was attempted"
    raise RuntimeError(f"All Groq market overview models failed: {summary}")


def generate_meme_post_with_groq() -> dict[str, Any]:
    """Generate high-engagement crypto trader psychology / community culture meme post."""

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found in GitHub Secrets.")

    btc_data = get_24h_data("BTCUSDT")
    btc_price = float(btc_data["price"])
    btc_change = float(btc_data["change_24h"])

    system_prompt = """
You are a witty, highly relatable crypto trader on Binance Square known for viral trading psychology observations, memes, and community culture commentary.
Your posts highlight the funny, painful, and relatable realities of crypto trading (FOMO, leverage addiction, holding through dips, revenge trading vs discipline, 1-minute chart staring).
Generate an ultra-relatable, engaging, humorous post that sparks immense community laughter, likes, and comment agreement.
Return exactly one valid JSON object and no prose outside that JSON object.
""".strip()

    user_prompt = f"""
Current Market Context: Bitcoin at ${btc_price:,.0f} ({btc_change:+.1f}% 24H).

Return exactly this JSON structure:
{{
  "title": "Short punchy meme headline or situation title (e.g. 'Trader Diary:', 'My Portfolio when:', 'The 3 Stages of a Leverage Trader:')",
  "content": "2-4 lines of witty, super-relatable trading humor / meme format (e.g. comparison, expectation vs reality, trader thoughts at 3 AM).",
  "lesson": "One short witty or real risk-management takeaway.",
  "question": "A fun, natural question for the community (e.g. 'Who else did this today? Be honest 👇')"
}}

Rules:
1. Make it genuine, relatable, and funny for real crypto futures and spot traders.
2. Avoid generic corporate speak.
3. Return JSON only.
""".strip()

    client = Groq(api_key=api_key)
    candidates = _groq_model_candidates(api_key)
    failures: list[str] = []

    for model in candidates:
        print(f"[Groq] Generating meme post with model: {model}")
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=600,
                response_format={"type": "json_object"},
            )
            result = _parse_json_object(completion.choices[0].message.content)

            meme_data = {
                "title": str(result.get("title", "Crypto Trader Psychology 101:")).strip(),
                "content": str(result.get("content", "Me: 'I will strictly follow my 1:3 R:R trading plan today.'\nAlso me 5 minutes after seeing a 1M green candle: 'Market order 50x!'")).strip(),
                "lesson": str(result.get("lesson", "Discipline is what separates traders from gamblers.")).strip(),
                "question": str(result.get("question", "Be honest: Who relates to this today? 👇")).strip(),
                "groq_model": model,
                "type": "meme_community",
            }
            print(f"[Groq] Meme post validated with {model}")
            return meme_data

        except Exception as exc:
            if _is_authentication_error(exc):
                raise RuntimeError("GROQ_API_KEY authentication failed") from exc

            failure = f"{model}: {type(exc).__name__}: {exc}"
            failures.append(failure)
            print(f"[Groq Warning] {failure}")

    summary = " | ".join(failures) if failures else "no candidate model was attempted"
    raise RuntimeError(f"All Groq meme post models failed: {summary}")
