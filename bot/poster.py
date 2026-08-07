from bot.market_data import get_24h_data


def format_signal_post(signal: dict) -> str:
    coin = signal["coin"]
    bonus = signal.get("trending_bonus", "BTC")
    entry = signal["entry_price"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    sl = signal["sl"]
    change = signal.get("change", 0)

    tp1_pct = ((tp1 - entry) / entry) * 100
    tp2_pct = ((tp2 - entry) / entry) * 100
    sl_pct = ((entry - sl) / entry) * 100

    return f"""🚀 ${coin} LONG Setup | SMC Liquidity Grab In Play

📊 Entry: CMP (${entry}) | Risk: Medium
🎯 TP1: ${tp1} (+{tp1_pct:.1f}%) | TP2: ${tp2} (+{tp2_pct:.1f}%)
🛑 SL: ${sl} (-{sl_pct:.1f}%)

🧠 {signal.get("smc_logic", "")}

${coin} ${bonus} #Crypto #SMC #TradingSignals

Not financial advice. DYOR."""
    

def format_success_post(signal: dict, hit: str, current_price: float) -> str:
    coin = signal["coin"]
    entry = signal["entry_price"]

    pnl = ((current_price - entry) / entry) * 100

    return f"""🎉 Target Hit!

${coin} {hit} ✅

Entry: ${entry}
Current: ${current_price}
PnL: +{pnl:.2f}%

#Crypto #TargetHit

Not financial advice. DYOR."""
    

def get_current_price(coin: str) -> float:
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
