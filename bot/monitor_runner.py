"""GitHub Actions entry point for the live trade monitor."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bot.monitor import check_trades_and_maybe_post

SKILLS_ROOT = Path("/tmp/binance-skills-hub")
POST_TEXT_SCRIPT = (
    SKILLS_ROOT
    / "skills"
    / "binance"
    / "square-post"
    / "scripts"
    / "post-text.mjs"
)
POST_IMAGE_SCRIPT = (
    SKILLS_ROOT
    / "skills"
    / "binance"
    / "square-post"
    / "scripts"
    / "post-image.mjs"
)


def publish(post: str, images: list[str]) -> None:
    """Publish a text or image post through Binance's official helper script."""

    text = str(post).strip()
    if not text:
        raise ValueError("Cannot publish an empty Binance Square post")

    valid_images = [str(Path(path).resolve()) for path in images if Path(path).is_file()]

    print("=== AUTO-PUBLISH TARGET-HIT POST ===")
    print(text)

    if valid_images:
        if not POST_IMAGE_SCRIPT.is_file():
            raise FileNotFoundError(f"Image posting script not found: {POST_IMAGE_SCRIPT}")

        command = [
            "node",
            str(POST_IMAGE_SCRIPT),
            "--text",
            text,
            "--images",
            ",".join(valid_images),
        ]
        print(f"[Publish] Sending image post with {len(valid_images)} image(s)")
    else:
        if not POST_TEXT_SCRIPT.is_file():
            raise FileNotFoundError(f"Text posting script not found: {POST_TEXT_SCRIPT}")

        command = [
            "node",
            str(POST_TEXT_SCRIPT),
            "--text",
            text,
        ]
        print("[Publish] No valid image supplied; sending text-only post")

    subprocess.run(command, check=True)
    print("[Publish] Binance Square command completed successfully")


if __name__ == "__main__":
    check_trades_and_maybe_post(publish)
