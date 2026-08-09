import random
from bot.market_data import get_24h_data


def format_signal_post(signal: dict) -> str:
    coin = signal["coin"]
    entry = signal["entry_price"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    sl = signal["sl"]
    change = signal.get("change", 0)

    hooks = [
        f"Is ${coin} setting up for a clean continuation? 👀",
        f"Liquidity sweep complete on ${coin}? Here's what I see.",
        f"${coin} just printed an interesting structure shift.",
        f"Smart money appears active on ${coin}.",
        f"This ${coin} setup caught my attention 👇"
    ]

    hook = random.choice(hooks)

    tp1_pct = ((tp1 - entry) / entry) * 100
    tp2_pct = ((tp2 - entry) / entry) * 100
    sl_pct = ((entry - sl) / entry) * 100

    post = f"""{hook}

After a +{change:.1f}% move in the last 24H, market structure looks constructive.

📌 Entry: ${entry}
🎯 TP1: ${tp1} (+{tp1_pct:.1f}%)
🎯 TP2: ${tp2} (+{tp2_pct:.1f}%)
🛑 SL: ${sl} (-{sl_pct:.1f}%)

Structure suggests potential continuation if momentum sustains.

Do you expect continuation or a pullback first? 🤔

${coin} #Crypto #SMC #PriceAction

Not financial advice. DYOR.
"""

    return post
