"""Master Binance-style position-card generator.

The layout is built for a 948 x 1299 image and follows the clean master
reference supplied for the project.  All financial values are calculated from
explicit inputs; the production path never invents random leverage or size.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageDraw, ImageFont

from assets.generate_assets import generate_all_assets

# ---------------------------------------------------------------------------
# Identity and files
# ---------------------------------------------------------------------------

USERNAME = os.getenv("CARD_USERNAME", "muntajid")
REFERRAL_CODE = os.getenv("BINANCE_REFERRAL_CODE", "768056928")
CARD_TIMEZONE = os.getenv("CARD_TIMEZONE", "UTC")

ROOT_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"

PROFILE_PIC = ASSETS_DIR / "23077.jpeg"
QR_CODE = ASSETS_DIR / "IMG_20260811_163137.png"
BINANCE_LOGO = ASSETS_DIR / "binance_logo.png"
WATERMARK = ASSETS_DIR / "diamond_watermark.png"

FONT_BOLD = FONTS_DIR / "Inter_18pt-Bold.ttf"
FONT_REGULAR = FONTS_DIR / "Inter_18pt-Regular.ttf"
FONT_MEDIUM = FONTS_DIR / "Inter_18pt-Medium.ttf"
FONT_SEMIBOLD = FONTS_DIR / "Inter_18pt-SemiBold.ttf"

# ---------------------------------------------------------------------------
# Master canvas and colors
# ---------------------------------------------------------------------------

CARD_WIDTH = 948
CARD_HEIGHT = 1299

COLOR_BG = (0, 0, 0)
COLOR_WHITE = (255, 255, 255)
COLOR_GREEN = (46, 189, 133)
COLOR_RED = (246, 70, 93)
COLOR_GRAY = (132, 142, 156)
COLOR_DIVIDER = (45, 45, 45)


def get_font(path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a bundled font and fall back safely if the file is unavailable."""

    try:
        return ImageFont.truetype(str(path), size)
    except (OSError, ValueError):
        return ImageFont.load_default()


def fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    path: Path,
    preferred_size: int,
    max_width: int,
    minimum_size: int = 24,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Return the largest font that keeps ``text`` inside ``max_width``."""

    for size in range(preferred_size, minimum_size - 1, -1):
        font = get_font(path, size)
        box = draw.textbbox((0, 0), text, font=font, anchor="lt")
        if box[2] - box[0] <= max_width:
            return font

    return get_font(path, minimum_size)


def normalize_leverage(value: Any, default: int = 15) -> int:
    """Convert values such as 5, '5x', or '3x-5x' into one exact leverage."""

    if isinstance(value, bool):
        return default

    if isinstance(value, (int, float)):
        leverage = int(round(float(value)))
    else:
        numbers = re.findall(r"\d+(?:\.\d+)?", str(value or ""))
        leverage = int(round(float(numbers[-1]))) if numbers else default

    if not 1 <= leverage <= 125:
        raise ValueError(f"Leverage must be between 1x and 125x, got {leverage}x")

    return leverage


def format_price(price: float) -> str:
    """Format a market price without unnecessary trailing zeroes."""

    price = float(price)
    if price <= 0:
        raise ValueError("Price must be greater than zero")

    if price >= 1000:
        decimals = 2
    elif price >= 1:
        decimals = 6
    else:
        decimals = 8

    result = f"{price:.{decimals}f}".rstrip("0").rstrip(".")
    if "." not in result:
        result += ".00"
    return result


def calculate_pnl(
    entry_price: float,
    close_price: float,
    position_size: float,
    leverage: int,
    direction: str = "LONG",
) -> tuple[float, float]:
    """Calculate accurate USDT PnL and leveraged ROI.

    ``position_size`` is the USDT notional size, not margin.  Fees, funding and
    slippage are intentionally excluded because the bot does not currently
    store those values.
    """

    entry = float(entry_price)
    close = float(close_price)
    notional = float(position_size)
    leverage = normalize_leverage(leverage)
    direction = str(direction).upper().strip()

    if entry <= 0 or close <= 0:
        raise ValueError("Entry and close prices must be greater than zero")
    if notional <= 0:
        raise ValueError("Position size must be greater than zero")
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("Direction must be LONG or SHORT")

    if direction == "LONG":
        price_change = (close - entry) / entry
    else:
        price_change = (entry - close) / entry

    pnl_usdt = price_change * notional
    roi_percent = price_change * leverage * 100.0
    return round(pnl_usdt, 2), round(roi_percent, 2)


def _timezone() -> timezone | ZoneInfo:
    if CARD_TIMEZONE.upper() == "UTC":
        return timezone.utc

    try:
        return ZoneInfo(CARD_TIMEZONE)
    except ZoneInfoNotFoundError:
        print(f"[Card] Unknown timezone '{CARD_TIMEZONE}', falling back to UTC")
        return timezone.utc


def _timestamp_text(value: datetime | None = None) -> str:
    if value is None:
        value = datetime.now(_timezone())
    elif value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc).astimezone(_timezone())
    else:
        value = value.astimezone(_timezone())

    return value.strftime("%Y-%m-%d %H:%M:%S")


def _ensure_generated_assets() -> None:
    if not BINANCE_LOGO.exists() or not WATERMARK.exists():
        generate_all_assets()


def _paste_profile(card: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    x, y, size = 55, 79, 88

    try:
        profile = Image.open(PROFILE_PIC).convert("RGBA")
        profile = profile.resize((size, size), Image.Resampling.LANCZOS)

        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        card.paste(profile, (x, y), mask)
    except (OSError, ValueError) as exc:
        print(f"[Card] Profile fallback: {exc}")
        draw.ellipse((x, y, x + size, y + size), fill=(35, 42, 52))


def _prepare_qr(source: Path, final_size: int = 152) -> Image.Image:
    """Crop the QR itself, restore a clean quiet zone, and preserve sharpness."""

    image = Image.open(source).convert("L")

    # The source image has a dark screenshot-like margin.  White QR modules
    # identify the actual code area reliably without an OpenCV dependency.
    white_mask = image.point(lambda pixel: 255 if pixel >= 180 else 0)
    box = white_mask.getbbox()
    if box is None:
        raise ValueError("QR image does not contain a detectable code")

    left, top, right, bottom = box
    padding = 2
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(image.width, right + padding)
    bottom = min(image.height, bottom + padding)

    qr = image.crop((left, top, right, bottom))
    qr = qr.point(lambda pixel: 255 if pixel >= 128 else 0)

    quiet_zone = 8
    inner_size = final_size - (quiet_zone * 2)
    qr = qr.resize((inner_size, inner_size), Image.Resampling.NEAREST)

    result = Image.new("RGB", (final_size, final_size), COLOR_WHITE)
    result.paste(qr.convert("RGB"), (quiet_zone, quiet_zone))

    radius = 10
    rounded_mask = Image.new("L", result.size, 0)
    ImageDraw.Draw(rounded_mask).rounded_rectangle(
        (0, 0, final_size - 1, final_size - 1),
        radius=radius,
        fill=255,
    )

    output = Image.new("RGB", result.size, COLOR_BG)
    output.paste(result, (0, 0), rounded_mask)
    return output


def _paste_brand_assets(card: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    _ensure_generated_assets()

    try:
        watermark = Image.open(WATERMARK).convert("RGBA")
        watermark = watermark.resize((865, 865), Image.Resampling.LANCZOS)
        card.paste(watermark, (346, -163), watermark)
    except (OSError, ValueError) as exc:
        print(f"[Card] Watermark skipped: {exc}")

    try:
        logo = Image.open(BINANCE_LOGO).convert("RGBA")
        card.paste(logo, (45, 1114), logo)
    except (OSError, ValueError) as exc:
        print(f"[Card] Logo skipped: {exc}")

    try:
        qr = _prepare_qr(QR_CODE, final_size=152)
        card.paste(qr, (752, 1107))
    except (OSError, ValueError) as exc:
        print(f"[Card] QR skipped: {exc}")

    referral_font = get_font(FONT_REGULAR, 29)
    referral_code_font = get_font(FONT_SEMIBOLD, 29)
    label = "Referral Code"

    draw.text((87, 1216), label, font=referral_font, fill=COLOR_WHITE, anchor="lt")
    label_box = draw.textbbox((87, 1216), label, font=referral_font, anchor="lt")
    draw.text(
        (label_box[2] + 13, 1216),
        REFERRAL_CODE,
        font=referral_code_font,
        fill=COLOR_WHITE,
        anchor="lt",
    )


def generate_position_card(
    coin: str,
    entry_price: float,
    close_price: float,
    direction: str = "LONG",
    leverage: int | float | str = 15,
    position_size: float = 5000.0,
    save_path: str = "charts/position_card.png",
    timestamp: datetime | None = None,
) -> tuple[str, dict[str, float | int | str]]:
    """Generate one fully validated master position card."""

    coin = str(coin).upper().strip()
    direction = str(direction).upper().strip()
    exact_leverage = normalize_leverage(leverage)
    notional = float(position_size)

    if not coin or not re.fullmatch(r"[A-Z0-9]{1,20}", coin):
        raise ValueError(f"Invalid coin symbol: {coin!r}")
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("Direction must be LONG or SHORT")

    pnl_usdt, roi_percent = calculate_pnl(
        entry_price=entry_price,
        close_price=close_price,
        position_size=notional,
        leverage=exact_leverage,
        direction=direction,
    )

    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), COLOR_BG)
    draw = ImageDraw.Draw(card)

    # Watermark is deliberately placed before text.
    _paste_brand_assets(card, draw)

    # Header
    _paste_profile(card, draw)
    username_font = get_font(FONT_BOLD, 40)
    timestamp_font = get_font(FONT_REGULAR, 31)
    draw.text((165, 80), USERNAME, font=username_font, fill=COLOR_WHITE, anchor="lt")
    draw.text(
        (165, 143),
        _timestamp_text(timestamp),
        font=timestamp_font,
        fill=COLOR_WHITE,
        anchor="lt",
    )

    # Instrument and direction
    instrument = f"{coin}USDT Perpetual"
    instrument_font = fit_font(
        draw,
        instrument,
        FONT_BOLD,
        preferred_size=46,
        max_width=835,
        minimum_size=31,
    )
    draw.text((55, 388), instrument, font=instrument_font, fill=COLOR_WHITE, anchor="lt")

    direction_color = COLOR_GREEN if direction == "LONG" else COLOR_RED
    direction_font = get_font(FONT_REGULAR, 34)
    leverage_font = get_font(FONT_REGULAR, 34)

    direction_label = direction.capitalize()
    draw.text((55, 453), direction_label, font=direction_font, fill=direction_color, anchor="lt")
    direction_box = draw.textbbox((55, 453), direction_label, font=direction_font, anchor="lt")

    separator_x = direction_box[2] + 21
    draw.line((separator_x, 452, separator_x, 483), fill=COLOR_GRAY, width=3)
    draw.text(
        (separator_x + 25, 453),
        f"{exact_leverage}x",
        font=leverage_font,
        fill=COLOR_GRAY,
        anchor="lt",
    )

    # PnL and ROI
    pnl_color = COLOR_GREEN if pnl_usdt >= 0 else COLOR_RED
    pnl_font = get_font(FONT_BOLD, 82)
    unit_font = get_font(FONT_SEMIBOLD, 45)
    roi_font = get_font(FONT_BOLD, 47)

    pnl_sign = "+" if pnl_usdt >= 0 else ""
    pnl_text = f"{pnl_sign}{pnl_usdt:.2f}"
    draw.text((55, 552), pnl_text, font=pnl_font, fill=pnl_color, anchor="lt")
    pnl_box = draw.textbbox((55, 552), pnl_text, font=pnl_font, anchor="lt")
    draw.text(
        (pnl_box[2] + 28, 579),
        "USDT",
        font=unit_font,
        fill=COLOR_WHITE,
        anchor="lt",
    )

    roi_sign = "+" if roi_percent >= 0 else ""
    draw.text(
        (55, 663),
        f"{roi_sign}{roi_percent:.2f}%",
        font=roi_font,
        fill=pnl_color,
        anchor="lt",
    )

    # Prices
    label_font = get_font(FONT_REGULAR, 30)
    value_font = get_font(FONT_REGULAR, 42)

    draw.text((55, 776), "Entry Price", font=label_font, fill=COLOR_GRAY, anchor="lt")
    draw.text(
        (55, 825),
        format_price(float(entry_price)),
        font=value_font,
        fill=COLOR_WHITE,
        anchor="lt",
    )

    draw.text(
        (495, 776),
        "Average Close Price",
        font=label_font,
        fill=COLOR_GRAY,
        anchor="lt",
    )
    draw.text(
        (495, 825),
        format_price(float(close_price)),
        font=value_font,
        fill=COLOR_WHITE,
        anchor="lt",
    )

    # Footer divider
    draw.line((0, 1064, CARD_WIDTH, 1064), fill=COLOR_DIVIDER, width=1)

    output = Path(save_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    card.save(output, "PNG", optimize=True)

    if not output.is_file() or output.stat().st_size < 10_000:
        raise RuntimeError(f"Position card was not generated correctly: {output}")

    stats: dict[str, float | int | str] = {
        "coin": coin,
        "direction": direction,
        "position_size_usdt": round(notional, 2),
        "leverage": exact_leverage,
        "pnl_usdt": pnl_usdt,
        "roi_percent": roi_percent,
        "entry_price": float(entry_price),
        "close_price": float(close_price),
    }

    print(f"[Card] Saved master card: {output}")
    print(f"[Card] {coin} {direction} | PnL {pnl_usdt:+.2f} USDT | ROI {roi_percent:+.2f}%")
    return str(output), stats


if __name__ == "__main__":
    # Deterministic regression sample using the production display settings.
    # These values must produce +1507.31 USDT and +452.19% before
    # fees, funding and slippage.
    generate_position_card(
        coin="GENIUS",
        entry_price=0.6448343,
        close_price=0.4504418,
        direction="SHORT",
        leverage=15,
        position_size=5000,
        save_path="charts/test_card.png",
    )
