import os
import json
import requests

from groq import Groq


BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"


def get_live_price(coin: str) -> float:
    """
    Fetch the current Binance spot price.

    No random/fallback price is ever returned.
    If Binance cannot provide a valid price,
    the function raises an error.
    """

    symbol = f"{coin.upper()}USDT"

    response = requests.get(
        BINANCE_TICKER_URL,
        params={"symbol": symbol},
        timeout=15,
    )

    response.raise_for_status()

    data = response.json()

    if "lastPrice" not in data:
        raise RuntimeError(
            f"Binance did not return lastPrice for {symbol}"
        )

    price = float(data["lastPrice"])

    if price <= 0:
        raise RuntimeError(
            f"Invalid Binance price for {symbol}: {price}"
        )

    print(
        f"[Live Price] {symbol} = ${price}"
    )

    return price


def get_trending_coins(
    top_n=30,
    min_change=5.0,
    min_volume=5_000_000,
):
    """
    Get real trending Binance USDT pairs.

    If Binance fails, stop instead of returning
    fabricated/random market data.
    """

    response = requests.get(
        BINANCE_TICKER_URL,
        timeout=15,
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(
            "Invalid response received from Binance."
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

            change = float(
                item["priceChangePercent"]
            )

            volume = float(
                item["quoteVolume"]
            )

            price = float(
                item["lastPrice"]
            )

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

        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            continue

    filtered.sort(
        key=lambda x: x["change"],
        reverse=True,
    )

    if not filtered:
        raise RuntimeError(
            "Binance returned no valid trending "
            "USDT pairs matching the filters."
        )

    return filtered[:top_n]


def generate_signal_with_groq(
    coin: str,
    price: float,
    change: float,
    trending_bonus: str,
) -> dict:

    api_key = os.getenv(
        "GROQ_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not found in GitHub Secrets."
        )

    # Refresh the Binance price immediately before
    # sending market data to the analysis model.
    live_price = get_live_price(coin)

    print(
        f"[Price Validation] "
        f"{coin}: supplied=${price}, "
        f"live=${live_price}"
    )

    client = Groq(
        api_key=api_key
    )

    system_prompt = """
You are an educational crypto market-analysis assistant.

Use only the supplied Binance market data.
Do not invent or fabricate market prices.
Do not claim certainty about future price movements.

Return valid JSON only.
"""

    user_prompt = f"""
Coin: {coin}

Verified Binance live price:
${live_price}

24h change:
{change:.2f}%

This is an educational market-analysis signal.

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
  "confidence": 88,
  "smc_logic": "Educational explanation based on observable market structure.",
  "smc_logic_short": "Short market-structure annotation."
}}

IMPORTANT:

1. entry_price MUST equal {live_price}.
2. Never invent the current price.
3. Never replace the supplied current price with another price.
4. Clearly treat TP/SL and future direction as analysis,
   not guaranteed outcomes.
5. Return JSON only.
"""

    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        temperature=0.2,
        max_tokens=900,
        response_format={
            "type": "json_object"
        },
    )

    result = json.loads(
        completion.choices[0]
        .message.content
    )

    if not isinstance(result, dict):
        raise RuntimeError(
            "Groq returned invalid JSON data."
        )

    # The Binance live price remains authoritative.
    result["coin"] = coin
    result["entry_price"] = live_price
    result["change"] = change

    return result
