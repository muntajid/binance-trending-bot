import os, requests, json

SQUARE_API_URL = os.getenv("SQUARE_API_URL", "https://www.binance.com/bapi/square/api/v1/post/create")

def publish_to_binance_square(content: str, image_paths: list = None):
    token = os.getenv("BINANCE_SQUARE_TOKEN") or os.getenv("BINANCE_API_KEY")
    if not token:
        print("[Publish] No token found, saving locally only")
        with open("data/publish_log.txt","a",encoding="utf-8") as f:
            f.write(content+"\n---\n")
        return {"mock": True}
    headers = {
    "X-MBX-APIKEY": token,
    "Content-Type": "application/json"
}
    payload = {"content": content, "images": image_paths or []}
    try:
        r = requests.post(SQUARE_API_URL, headers=headers, json=payload, timeout=15)
        print(f"[Binance Publish] Status {r.status_code}: {r.text[:500]}")
        return r.json() if r.headers.get("content-type","").startswith("application/json") else {"status": r.status_code, "text": r.text}
    except Exception as e:
        print(f"[Publish Error] {e}")
        with open("data/publish_log.txt","a",encoding="utf-8") as f:
            f.write(content+"\n---\n")
        return {"error": str(e)}

if __name__ == "__main__":
    with open("data/latest_post.txt", encoding="utf-8") as f:
        content = f.read()
    import glob
    charts = glob.glob("charts/*.png")
    print(publish_to_binance_square(content, charts))
