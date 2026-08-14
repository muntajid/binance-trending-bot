"""Patch the downloaded official Square helper for robust UTF-8 transport.

Some Square clients displayed raw UTF-8 emoji bytes as Windows-1252 mojibake.
This patch keeps the official posting workflow but makes the JSON request body
ASCII-safe with JSON Unicode escapes and declares UTF-8 explicitly.
"""

from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_LIB_PATH = Path(
    "/tmp/binance-skills-hub/skills/binance/square-post/scripts/lib.mjs"
)

ASCII_SAFE_FUNCTION = r'''
function asciiSafeJson(value) {
  return JSON.stringify(value).replace(/[\u007f-\uffff]/g, (character) => {
    return `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`;
  });
}

'''


def patch_square_library(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Square helper library not found: {path}")

    text = path.read_text(encoding="utf-8")

    if "function asciiSafeJson(value)" not in text:
        marker = "export async function api("
        if marker not in text:
            raise RuntimeError(
                "Official Square helper changed: api() marker was not found"
            )
        text = text.replace(marker, ASCII_SAFE_FUNCTION + marker, 1)

    text = text.replace(
        '"Content-Type": "application/json",',
        '"Content-Type": "application/json; charset=utf-8",',
    )
    text = text.replace(
        "body: JSON.stringify(body),",
        "body: asciiSafeJson(body),",
    )

    required_fragments = (
        "function asciiSafeJson(value)",
        '"Content-Type": "application/json; charset=utf-8"',
        "body: asciiSafeJson(body)",
    )
    missing = [fragment for fragment in required_fragments if fragment not in text]
    if missing:
        raise RuntimeError(f"UTF-8 patch validation failed; missing: {missing}")

    path.write_text(text, encoding="utf-8")
    print(f"[UTF-8 Patch] Applied and validated: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_LIB_PATH),
        help="Path to the downloaded square-post scripts/lib.mjs file",
    )
    args = parser.parse_args()
    patch_square_library(Path(args.path))


if __name__ == "__main__":
    main()
