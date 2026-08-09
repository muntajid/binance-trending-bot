import random
from bot.market_data import get_24h_data


# ============================================
# HUMANIZED + ROTATING SIGNAL POST ENGINE
# ============================================

def format_signal_post(signal: dict) -> str:
    coin = signal["coin"]
    entry = float(signal["entry_price"])
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])
    sl = float(signal["sl"])
    change = float(signal.get("change", 0))

    # 🔥 Strong Rotating Hooks (Algorithm Friendly)
    hooks = [
        f"${coin} just made a strong structural shift. Momentum building? 🚀",
        f"Explosive 24H move on ${coin}. Is continuation next?",
        f"${coin} is gaining traction fast. Buyers stepping in.",
        f"Breakout structure forming on ${coin}. Watching closely 👀",
        f"${coin} showing expansion behavior after liquidity sweep."
    ]

    hook = random.choice(hooks)

    # 📊 Risk Calculations
    tp1_pct = ((tp1 - entry) / entry) * 100
    tp2_pct = ((tp2 - entry) / entry) * 100
    sl_pct = ((entry - sl) / entry) * 100

    # 🔄 Rotating Closing Engagement Lines
    engagement_lines = [
        "Bullish continuation or short-term pullback first? 👇",
        "Would you hold for TP2 or secure profits at TP1? 🤔",
        "Are you entering on momentum or waiting for retrace?",
        "Do you see further expansion from here?"
    ]

    engagement = random.choice(engagement_lines)

    # 🏷 Rotating Hashtags (Avoid Saturation)
    hashtag_sets = [
        f"${coin} #BinanceSquare #CryptoSignals #Altcoins",
        f"${coin} #SMC #PriceAction #Crypto",
        f"${coin} #TechnicalAnalysis #CryptoTrading",
        f"${coin} #AltcoinSeason #CryptoMarket"
    ]

    hashtags = random.choice(hashtag_sets)

    post = f"""{hook}

Up +{change:.1f}% in 24H and still holding structural strength.

📌 Entry: ${entry}
🎯 TP1: ${tp1} (+{tp1_pct:.1f}%)
🎯 TP2: ${tp2} (+{tp2_pct:.1f}%)
🛑 SL: ${sl} (-{sl_pct:.1f}%)

Risk remains defined below structure.

{engagement}

{hashtags}

Not financial advice. DYOR.
"""

    return post


# ============================================
# SUCCESS POST ENGINE (TP1 / TP2 HIT)
# ============================================

def format_success_post(signal: dict, hit: str, current_price: float) -> str:
    coin = signal["coin"]
    entry = float(signal["entry_price"])

    pnl = ((current_price - entry) / entry) * 100

    reactions = [
        "Momentum delivered exactly as anticipated ✅",
        "Structure played out perfectly 🔥",
        "Liquidity → Expansion → Target hit 🚀",
        "Clean execution on this setup 📈",
        "Smart money thesis validated ✅"
    ]

    reaction = random.choice(reactions)

    celebration_lines = [
        "Discipline always pays.",
        "Patience > Emotion.",
        "Structured trading wins.",
        "Momentum confirmed."
    ]

    celebration = random.choice(celebration_lines)

    return f"""🎉 Target Hit!

${coin} {hit} ✅

Entry: ${entry}
Current: ${current_price}
PnL: +{pnl:.2f}%

{reaction}
{celebration}

${coin} #TargetHit #Crypto

Not financial advice. DYOR.
"""


# ============================================
# VERIFIED LIVE PRICE FETCH
# ============================================

def get_current_price(coin: str) -> float:
    symbol = f"{coin.upper()}USDT"

    data = get_24h_data(symbol)

    if not data.get("verified"):
        raise RuntimeError(f"Market data not verified for {symbol}")

    price = float(data["price"])

    if price <= 0:
        raise RuntimeError(f"Invalid verified price for {symbol}: {price}")

    return price
