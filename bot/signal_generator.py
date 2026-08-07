import os, json, random, requests
from groq import Groq

def get_trending_coins(top_n=30, min_change=5.0, min_volume=5000000):
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        filtered = []
        exclude = ["USDCUSDT","FDUSDUSDT","TUSDUSDT","DAIUSDT","BUSDUSDT","USDPUSDT"]
        for d in data:
            sym = d["symbol"]
            if not sym.endswith("USDT"): continue
            if sym in exclude: continue
            if "UPUSDT" in sym or "DOWNUSDT" in sym or "BEARUSDT" in sym or "BULLUSDT" in sym: continue
            try:
                change = float(d["priceChangePercent"])
                volume = float(d["quoteVolume"])
                if change >= min_change and volume >= min_volume:
                    coin = sym.replace("USDT","")
                    filtered.append({"coin": coin, "change": change, "volume": volume, "price": float(d["lastPrice"])})
            except: continue
        filtered.sort(key=lambda x: x["change"], reverse=True)
        return filtered[:top_n]
    except Exception as e:
        print(f"[Trending Error] {e}")
        fallback = ["BTC","ETH","SOL","PEPE","WIF","BONK","FLOKI","SHIB","DOGE","ENA","TIA","ARB","OP","SUI","AVAX"]
        return [{"coin": c, "change": random.uniform(5,40), "volume": 10000000, "price": random.uniform(0.5,500)} for c in fallback[:top_n]]

def generate_signal_with_groq(coin: str, price: float, change: float, trending_bonus: str) -> dict:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise Exception("GROQ_API_KEY not found in Secrets")
    client = Groq(api_key=api_key)
    system_prompt = """You are a professional SMC crypto trader. Write like a human trader, not an AI. No mention of AI. Use natural trader language. Provide a detailed but concise analysis (3-4 lines) using SMC concepts: Order Block, Fair Value Gap (FVG), Liquidity Sweep, Breaker Block, BOS/ChoCH. Entry is CMP. Leverage 3x-5x Medium Risk. Output MUST be valid JSON only."""
    user_prompt = f"""
Coin: {coin} | Current Price: ${price} | 24h Change: +{change:.1f}% (Trending Gainer)
Generate LONG signal. Trending coin, high attention.
Return JSON exactly:
{{
  "coin": "{coin}",
  "trending_bonus": "{trending_bonus}",
  "direction": "LONG",
  "entry": "CMP",
  "entry_price": {price},
  "tp1": <price ~4-6% above>,
  "tp2": <price ~8-12% above>,
  "sl": <price ~3-5% below>,
  "leverage": "3x-5x",
  "risk": "Medium Risk",
  "confidence": 88,
  "smc_logic": "3-4 line human trader style detailed SMC logic: liquidity sweep, order block reclaim on 4H, 1H FVG + BOS, expecting continuation to buy-side liquidity. Must be 250-350 characters, natural.",
  "smc_logic_short": "Short 1-line for chart annotation: e.g., Liquidity sweep + OB reclaim + FVG"
}}
Confidence 85-93. Prices realistic. smc_logic must be 250-350 chars.
"""
    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"system","content": system_prompt},{"role":"user","content": user_prompt}],
        temperature=0.75,
        max_tokens=900,
        response_format={"type":"json_object"}
    )
    data = json.loads(completion.choices[0].message.content)
    data["change"] = change
    return data
