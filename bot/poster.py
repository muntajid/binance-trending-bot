from bot.market_data import get_24h_data

def get_current_price(coin: str) -> float:
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

    return price
