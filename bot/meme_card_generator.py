"""Sleek dark-mode Meme & Community Culture Graphic Card Generator."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT_DIR = Path(__file__).resolve().parents[1]
FONTS_DIR = ROOT_DIR / "assets" / "fonts"


def generate_meme_card(
    title: str,
    content: str,
    lesson: str,
    save_path: str = "charts/MEME_CARD.png",
) -> str:
    """Generate high-engagement dark-mode meme graphic card for Binance Square."""

    width, height = 1080, 1080
    img = Image.new("RGB", (width, height), color="#0b0e14")
    draw = ImageDraw.Draw(img)

    # Outer card
    draw.rounded_rectangle(
        [(40, 40), (1040, 1040)],
        radius=36,
        fill="#12151c",
        outline="#2b313a",
        width=2,
    )

    font_badge = ImageFont.truetype(str(FONTS_DIR / "Inter_18pt-Bold.ttf"), 22)
    font_title = ImageFont.truetype(str(FONTS_DIR / "Inter_18pt-Bold.ttf"), 34)
    font_body = ImageFont.truetype(str(FONTS_DIR / "Inter_18pt-Medium.ttf"), 34)
    font_lesson_title = ImageFont.truetype(str(FONTS_DIR / "Inter_18pt-Bold.ttf"), 28)
    font_lesson_body = ImageFont.truetype(str(FONTS_DIR / "Inter_18pt-Regular.ttf"), 26)
    font_footer = ImageFont.truetype(str(FONTS_DIR / "Inter_18pt-SemiBold.ttf"), 24)

    # Top Gold Badge
    draw.rounded_rectangle([(80, 80), (450, 135)], radius=14, fill="#f0b90b")
    draw.text((100, 93), "TRADING PSYCHOLOGY 101", font=font_badge, fill="#000000")

    # Clean title
    clean_title = (
        title.replace("🎭", "")
        .replace("🔥", "")
        .replace("😂", "")
        .encode("ascii", "ignore")
        .decode("ascii")
        .strip()
    )
    draw.text((80, 165), clean_title, font=font_title, fill="#f0b90b")

    # Content box
    draw.rounded_rectangle(
        [(80, 235), (1000, 770)],
        radius=24,
        fill="#1a1f29",
        outline="#363d4a",
        width=1,
    )

    raw_lines = content.split("\n")
    wrapped_lines = []
    for rl in raw_lines:
        if not rl.strip():
            wrapped_lines.append("")
        else:
            clean_l = rl.encode("ascii", "ignore").decode("ascii").strip()
            wrapped_lines.extend(textwrap.wrap(clean_l, width=44))

    y = 275
    for line in wrapped_lines[:9]:
        if not line:
            y += 24
            continue

        color = "#eaecef"
        lower_line = line.lower()
        if any(w in lower_line for w in ["stage 3", "50x", "100x", "liquidated", "loss", "dump", "reality:"]):
            color = "#f6465d"
        elif any(w in lower_line for w in ["stage 1", "spot", "profit", "pump", "green", "expectation:"]):
            color = "#0ecb81"
        elif any(w in lower_line for w in ["stage 2", "me:", "doctor:", "market:"]):
            color = "#f0b90b"

        draw.text((115, y), line, font=font_body, fill=color)
        y += 50

    # Reality Check Box
    draw.rounded_rectangle(
        [(80, 805), (1000, 935)],
        radius=20,
        fill="#18202f",
        outline="#2962ff",
        width=2,
    )
    draw.text((115, 825), "Reality Check:", font=font_lesson_title, fill="#60a5fa")

    clean_lesson = lesson.encode("ascii", "ignore").decode("ascii").strip()
    lesson_wrapped = textwrap.wrap(clean_lesson, width=58)
    ly = 865
    for l_line in lesson_wrapped[:2]:
        draw.text((115, ly), l_line, font=font_lesson_body, fill="#eaecef")
        ly += 32

    # Footer
    draw.text((80, 975), "#BinanceSquare • #CryptoCommunity", font=font_footer, fill="#848e9c")
    draw.text((1000, 975), "$BTC $ETH", font=font_footer, fill="#f0b90b", anchor="ra")

    output_dir = os.path.dirname(save_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    img.save(save_path)
    print(f"[Meme Card] Saved: {save_path}")
    return save_path
