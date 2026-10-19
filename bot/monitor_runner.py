"""GitHub Actions entry point for the live trade monitor."""

from __future__ import annotations

import os

from bot.monitor import check_trades_and_maybe_post
from bot.poster import assert_clean_post_text
from bot.square_publisher import publish_to_square


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def publish(post: str, images: list[str]) -> None:
    """Publish a text or image post through the official Square helper.

    Media uploads are retried (Binance processes images asynchronously and the
    official poll window is short), and a text-only post is used as the
    fallback so a target-hit update is never lost to a slow CDN.
    """

    text = assert_clean_post_text(str(post).strip())

    print("=== AUTO-PUBLISH TARGET-HIT POST ===")
    print(text)

    outcome = publish_to_square(
        text,
        images,
        attempts=_int_env("SQUARE_PUBLISH_ATTEMPTS", 3),
        deadline_seconds=float(_int_env("SQUARE_PUBLISH_DEADLINE_SECONDS", 240)),
        fallback_to_text=os.getenv("SQUARE_TEXT_FALLBACK", "1") not in {"0", "false", "no"},
    )

    if outcome.degraded:
        print(
            f"[Publish] Published as {outcome.mode}-only post after retry/fallback "
            f"({outcome.attempts} attempt(s))"
        )
    else:
        print(f"[Publish] {outcome.detail}")


if __name__ == "__main__":
    check_trades_and_maybe_post(publish)
