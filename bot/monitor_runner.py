import subprocess
from bot.monitor import check_trades_and_maybe_post


def publish(post, charts):
    print("=== AUTO PUBLISH SUCCESS POST ===")
    print(post)

    subprocess.run([
        "node",
        "/tmp/binance-skills-hub/skills/binance/square-post/scripts/post-text.mjs",
        "--text",
        post
    ], check=True)


if __name__ == "__main__":
    check_trades_and_maybe_post(publish)
