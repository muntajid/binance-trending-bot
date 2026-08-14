"""Generate the local Binance-style assets used by the position card.

The generated files are deterministic, transparent PNG files.  No network
request is required, which keeps GitHub Actions repeatable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont

ASSETS_DIR = Path(__file__).resolve().parent
FONTS_DIR = ASSETS_DIR / "fonts"

BINANCE_YELLOW = (240, 185, 11, 255)
WHITE = (255, 255, 255, 255)

# Binance symbol polygons, normalized to the official 24 x 24 viewBox.
# Keeping the geometry normalized lets the same mark be rendered at any size.
SYMBOL_POLYGONS: tuple[tuple[tuple[float, float], ...], ...] = (
    (
        (11.9885, 0.0115),
        (19.3415, 7.3405),
        (16.6241, 10.0559),
        (11.9885, 5.4203),
        (7.3530, 10.0798),
        (4.6356, 7.3644),
    ),
    (
        (2.7164, 9.2836),
        (5.4088, 12.0000),
        (2.7174, 14.6924),
        (0.0000, 12.0000),
    ),
    (
        (11.9886, 9.2846),
        (14.7049, 11.9760),
        (11.9885, 14.6934),
        (9.2721, 12.0000),
    ),
    (
        (21.2606, 9.2836),
        (24.0000, 12.0000),
        (21.2846, 14.7164),
        (18.5682, 12.0000),
    ),
    (
        (16.6240, 13.9202),
        (19.3415, 16.6356),
        (11.9885, 23.9886),
        (4.6355, 16.6366),
        (7.3530, 13.9202),
        (11.9885, 18.5797),
    ),
)


def _font(filename: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = FONTS_DIR / filename
    try:
        return ImageFont.truetype(str(path), size)
    except (OSError, ValueError):
        return ImageFont.load_default()


def _scale_polygon(
    polygon: Sequence[tuple[float, float]],
    box: tuple[int, int, int, int],
) -> list[tuple[int, int]]:
    left, top, width, height = box
    return [
        (
            round(left + (x / 24.0) * width),
            round(top + (y / 24.0) * height),
        )
        for x, y in polygon
    ]


def _horizontal_gradient(
    size: tuple[int, int],
    left_color: tuple[int, int, int, int],
    right_color: tuple[int, int, int, int],
) -> Image.Image:
    width, height = size
    gradient = Image.new("RGBA", size)
    pixels = gradient.load()

    denominator = max(width - 1, 1)
    for x in range(width):
        ratio = x / denominator
        color = tuple(
            round(left_color[channel] * (1.0 - ratio) + right_color[channel] * ratio)
            for channel in range(4)
        )
        for y in range(height):
            pixels[x, y] = color

    return gradient


def draw_binance_symbol(
    image: Image.Image,
    box: tuple[int, int, int, int],
    colors: Iterable[
        tuple[
            tuple[int, int, int, int],
            tuple[int, int, int, int],
        ]
    ]
    | None = None,
) -> None:
    """Draw a scalable Binance-style geometric symbol into ``image``."""

    if image.mode != "RGBA":
        raise ValueError("draw_binance_symbol requires an RGBA image")

    left, top, width, height = box
    if width <= 0 or height <= 0:
        raise ValueError("Symbol width and height must be positive")

    if colors is None:
        colors = ((BINANCE_YELLOW, BINANCE_YELLOW),) * len(SYMBOL_POLYGONS)

    color_pairs = tuple(colors)
    if len(color_pairs) != len(SYMBOL_POLYGONS):
        raise ValueError("One color pair is required for each symbol polygon")

    for polygon, (left_color, right_color) in zip(SYMBOL_POLYGONS, color_pairs):
        mask = Image.new("L", image.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.polygon(_scale_polygon(polygon, box), fill=255)

        gradient = _horizontal_gradient(image.size, left_color, right_color)
        image.alpha_composite(Image.composite(gradient, Image.new("RGBA", image.size), mask))


def create_binance_logo() -> str:
    """Create the compact footer logo used by the master card."""

    canvas = Image.new("RGBA", (310, 92), (0, 0, 0, 0))
    draw_binance_symbol(canvas, (0, 4, 38, 38))

    draw = ImageDraw.Draw(canvas)
    font_binance = _font("Inter_18pt-Bold.ttf", 29)
    font_futures = _font("Inter_18pt-Regular.ttf", 43)

    draw.text((46, 0), "BINANCE", font=font_binance, fill=BINANCE_YELLOW)
    draw.text((46, 36), "FUTURES", font=font_futures, fill=WHITE)

    output = ASSETS_DIR / "binance_logo.png"
    canvas.save(output, "PNG", optimize=True)
    print(f"Created: {output}")
    return str(output)


def create_diamond_watermark() -> str:
    """Create the large low-contrast geometric watermark.

    The mark uses the same 865 x 865 geometry and tonal direction as the clean
    948 x 1299 master reference.  It is intentionally transparent outside the
    symbol so it can be positioned partly outside the card.
    """

    canvas = Image.new("RGBA", (865, 865), (0, 0, 0, 0))

    colors = (
        ((31, 32, 33, 255), (61, 62, 64, 255)),
        ((20, 21, 21, 255), (29, 30, 30, 255)),
        ((36, 37, 38, 255), (58, 59, 60, 255)),
        ((31, 32, 33, 255), (52, 53, 54, 255)),
        ((16, 17, 17, 255), (38, 39, 40, 255)),
    )

    draw_binance_symbol(canvas, (0, 0, 865, 865), colors=colors)

    output = ASSETS_DIR / "diamond_watermark.png"
    canvas.save(output, "PNG", optimize=True)
    print(f"Created: {output}")
    return str(output)


def generate_all_assets() -> tuple[str, str]:
    """Generate every derived asset and return their paths."""

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    logo_path = create_binance_logo()
    watermark_path = create_diamond_watermark()
    return logo_path, watermark_path


if __name__ == "__main__":
    print("Generating master position-card assets...")
    generate_all_assets()
    print("Done. All master assets are ready.")
