def format_signal_post(signal: dict) -> str:
    coin = signal['coin']
    bonus = signal.get('trending_bonus', 'BTC')
    entry = signal['entry_price']
    tp1 = signal['tp1']; tp2 = signal['tp2']; sl = signal['sl']
    change = signal.get('change', 0)
    tp1_pct = ((tp1 - entry)/entry)*100
    tp2_pct = ((tp2 - entry)/entry)*100
    sl_pct = ((entry - sl)/entry)*100
    post = f"""🚀 ${coin} LONG Setup | SMC Liquidity Grab In Play

{coin} just swept sell-side liquidity and reclaimed the bullish Order Block on 4H. On the 1H, price formed a clear Fair Value Gap and confirmed BOS, showing strong buyer momentum building. With +{change:.1f}% in 24h, trend is clearly bullish.

📊 Entry: CMP (${entry}) | Leverage: 3x-5x | Risk: Medium
🎯 TP1: ${tp1} (+{tp1_pct:.1f}%) | TP2: ${tp2} (+{tp2_pct:.1f}%)
🛑 SL: ${sl} (-{sl_pct:.1f}%)
✅ Confluence: 1H + 4H | Order Block + FVG + BOS

🧠 {signal['smc_logic']}

Expecting continuation toward buy-side liquidity. Intraday momentum looks solid for expansion.

${coin} ${bonus} #Crypto #SMC #TradingSignals

Not financial advice. DYOR."""
    return post

def format_success_post(signal: dict, hit: str, current_price: float) -> str:
    coin = signal['coin']; entry = signal['entry_price']
    pnl = ((current_price - entry)/entry)*100
    return f"""🎉 Target Smashed 🚀

${coin} {hit} HIT! ✅

📈 Entry: ${entry} → {hit}: ${current_price} (+{pnl:.2f}%)
💰 SMC Liquidity Grab Played Perfect!

Momentum continuation as expected. Well done to holders! 🔥

${coin} #Crypto #TargetHit

Not financial advice. DYOR."""

def get_current_price(coin: str) -> float:
    import requests
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={coin}USDT"
    r = requests.get(url, timeout=5)
    return float(r.json()["price"])
