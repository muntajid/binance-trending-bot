from bot.monitor import check_trades_and_maybe_post
from bot.publish_binance import publish_to_binance_square


def publish(post, charts):
    print("=== AUTO PUBLISH SUCCESS POST ===")
    print(post)
    publish_to_binance_square(post, charts)


if __name__ == "__main__":
    check_trades_and_maybe_post(publish)
