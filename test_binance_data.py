import requests

BASE_URL = "https://data-api.binance.vision"

def main():
    print("Testing Binance public market data...")

    response = requests.get(
        f"{BASE_URL}/api/v3/ticker/24hr",
        params={"symbol": "BTCUSDT"},
        timeout=15,
    )

    print("HTTP status:", response.status_code)

    response.raise_for_status()

    data = response.json()

    required = ["symbol", "lastPrice", "priceChangePercent"]

    for key in required:
        if key not in data:
            raise RuntimeError(
                f"Missing required field: {key}"
            )

    price = float(data["lastPrice"])
    change = float(data["priceChangePercent"])

    if price <= 0:
        raise RuntimeError(
            f"Invalid price returned: {price}"
        )

    print("Symbol:", data["symbol"])
    print("Verified price:", price)
    print("24h change:", change)
    print("STATUS: VERIFIED")


if __name__ == "__main__":
    main()
