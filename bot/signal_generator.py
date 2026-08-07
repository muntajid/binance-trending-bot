import os
import json

from groq import Groq

from bot.market_data import get_24h_data


def get_live_price(coin: str) -> float:
    """
    Get authoritative live price from verified market_data layer.
    """

    symbol = f"{coin.upper()}USDT"

    data = get_24h_data(symbol)

    if not data.get("verified"):
        raise RuntimeError(
            f"Market data not verified for {symbol}"
        )

    price = float(data["price"])

    if price <= 0:
        raise RuntimeError(
            f"Invalid verified price for {symbol}: {price}"
        )

    print(f"[Live Price - VERIFIED] {symbol} = ${price}")

    return price


def get_trending_coins(
    top_n=30,
    min_change=5.0,
    min_volume=5_000_000,
):
    """
    Get trending coins using ONLY verified market_data layer.
    """

    # We fetch full ticker list via 24h endpoint logic.
    # Since market_data validates symbol-specific,
    # here we must call Binance vision ticker list endpoint manually.

    from market_data import BASE_URL
    import requests

    response = requests.get(
        f"{BASE_URL}/api/v3/ticker/24hr",
        timeout=15,
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(
            "Invalid ticker list from Binance vision API."
        )

    filtered = []

    exclude = {
        "USDCUSDT",
        "FDUSDUSDT",
        "TUSDUSDT",
        "DAIUSDT",
        "BUSDUSDT",
        "USDPUSDT",
    }

    for item in data:
        try:
            symbol = item["symbol"]

            if not symbol.endswith("USDT"):
                continue

            if symbol in exclude:
                continue

            if any(
                x in symbol
                for x in (
                    "UPUSDT",
                    "DOWNUSDT",
                    "BEARUSDT",
                    "BULLUSDT",
                )
            ):
                continue

            change = float(item["priceChangePercent"])
            volume = float(item["quoteVolume"])
            price = float(item["lastPrice"])

            if price <= 0:
                continue

            if change < min_change:
                continue

            if volume < min_volume:
                continue

            coin = symbol[:-4]

            filtered.append(
                {
                    "coin": coin,
                    "change": change,
                    "volume": volume,
                    "price": price,
                }
            )

        except (KeyError, TypeError, ValueError):
            continue

    filtered.sort(
        key=lambda x: x["change"],
        reverse=True,
    )

    if not filtered:
        raise RuntimeError(
            "No valid trending coins found from verified API."
        )

    return filtered[:top_n]


def generate_signal_with_groq(
    coin: str,
    price: float,
    change: float,
    trending_bonus: str,
) -> dict:

    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not found in GitHub Secrets."
        )

    # Use authoritative verified price only
    live_price = get_live_price(coin)

    if abs(live_price - price) / price * 100 > 0.5:
        raise RuntimeError(
            f"Price mismatch detected for {coin}: "
            f"supplied={price}, verified={live_price}"
        )

    client = Groq(api_key=api_key)

    system_prompt = """
You are an educational crypto market-analysis assistant.

Use only the supplied verified Binance market data.
Do not invent or fabricate market prices.
Return valid JSON only.
"""

    user_prompt = f"""
Coin: {coin}

Verified Binance live price:
${live_price}

24h change:
{change:.2f}%

Return JSON exactly in this structure:

{{
  "coin": "{coin}",
  "trending_bonus": "{trending_bonus}",
  "direction": "LONG",
  "entry": "CMP",
  "entry_price": {live_price},
  "tp1": <analysis level>,
  "tp2": <analysis level>,
  "sl": <analysis level>,
  "leverage": "3x-5x",
  "risk": "Medium Risk",
  "confidence": 80,
  "smc_logic": "Educational explanation.",
  "smc_logic_short": "Short annotation."
}}

IMPORTANT:
1. entry_price MUST equal {live_price}
2. Do not invent price
3. Return JSON only
"""

    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=800,
        response_format={"type": "json_object"},
    )

    result = json.loads(
        completion.choices[0].message.content
    )

    if not isinstance(result, dict):
        raise RuntimeError(
            "Groq returned invalid JSON."
        )

    # Authoritative enforcement
    result["coin"] = coin
    result["entry_price"] = live_price
    result["change"] = change

    return result
