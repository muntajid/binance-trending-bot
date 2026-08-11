"""
Binance Futures Position Card Generator
Master copy of GENIUSUSDT card style.
"""

import os
import random
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# CONFIGURATION
# ============================================================

USERNAME = "muntajid"
REFERRAL_CODE = "768056928"

ASSETS_DIR = "assets"
PROFILE_PIC = os.path.join(ASSETS_DIR, "profile.png")
QR_CODE = os.path.join(ASSETS_DIR, "qr_code.png")
BINANCE_LOGO = os.path.join(ASSETS_DIR, "binance_logo.png")
WATERMARK = os.path.join(ASSETS_DIR, "diamond_watermark.png")

FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")
FONT_BOLD = os.path.join(FONTS_DIR, "Inter-Bold.ttf")
FONT_REGULAR = os.path.join(FONTS_DIR, "Inter-Regular.ttf")
FONT_MEDIUM = os.path.join(FONTS_DIR, "Inter-Medium.ttf")

# Colors (Binance Theme)
COLOR_BG = (0, 0, 0)
COLOR_WHITE = (255, 255, 255)
COLOR_GREEN = (14, 203, 129)
COLOR_RED = (246, 70, 93)
COLOR_GRAY = (132, 142, 156)
COLOR_YELLOW = (240, 185, 11)

# Card dimensions (matches Binance share card)
CARD_WIDTH = 948
CARD_HEIGHT = 1264


def get_font(path, size):
    """Load font with fallback."""
    try:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def format_price(price):
    """Format price nicely (remove trailing zeros)."""
    formatted = f"{price:.7f}".rstrip("0").rstrip(".")
    if "." not in formatted:
        formatted += ".00"
    return formatted


def calculate_pnl(entry_price, close_price, position_size, leverage, direction="LONG"):
    """Calculate PNL and ROI."""
    if direction.upper() == "LONG":
        price_change_pct = (close_price - entry_price) / entry_price
    else:
        price_change_pct = (entry_price - close_price) / entry_price
    
    pnl_usdt = price_change_pct * position_size
    roi_percent = price_change_pct * leverage * 100
    
    return round(pnl_usdt, 2), round(roi_percent, 2)


def generate_position_card(
    coin: str,
    entry_price: float,
    close_price: float,
    direction: str = "LONG",
    leverage: int = None,
    position_size: float = None,
    save_path: str = "charts/position_card.png",
):
    """
    Generate Binance Futures position card (master copy of GENIUSUSDT).
    """
    
    # Random settings
    if leverage is None:
        leverage = random.randint(15, 20)
    
    if position_size is None:
        position_size = round(random.uniform(1000, 3000), 2)
    
    # Calculate PNL
    pnl_usdt, roi_percent = calculate_pnl(
        entry_price, close_price, position_size, leverage, direction
    )
    
    pnl_color = COLOR_GREEN if pnl_usdt >= 0 else COLOR_RED
    direction_color = COLOR_GREEN if direction.upper() == "LONG" else COLOR_RED
    
    # Create card
    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), COLOR_BG)
    draw = ImageDraw.Draw(card)
    
    # ============================================================
    # 1. WATERMARK (Diamond background - right side)
    # ============================================================
    try:
        if os.path.exists(WATERMARK):
            wm = Image.open(WATERMARK).convert("RGBA")
            wm_size = 800
            wm = wm.resize((wm_size, wm_size), Image.LANCZOS)
            wm_x = CARD_WIDTH - wm_size + 200
            wm_y = 100
            card.paste(wm, (wm_x, wm_y), wm)
    except Exception as e:
        print(f"[Card] Watermark skipped: {e}")
    
    # ============================================================
    # 2. HEADER (Profile + Username + Timestamp)
    # ============================================================
    header_y = 80
    profile_size = 90
    
    try:
        if os.path.exists(PROFILE_PIC):
            profile = Image.open(PROFILE_PIC).convert("RGBA")
            profile = profile.resize((profile_size, profile_size), Image.LANCZOS)
            
            # Circular mask
            mask = Image.new("L", (profile_size, profile_size), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, profile_size, profile_size), fill=255)
            
            card.paste(profile, (60, header_y), mask)
        else:
            # Fallback: yellow circle
            draw.ellipse(
                (60, header_y, 60 + profile_size, header_y + profile_size),
                fill=COLOR_YELLOW,
            )
    except Exception as e:
        print(f"[Card] Profile pic error: {e}")
    
    # Username
    font_username = get_font(FONT_BOLD, 38)
    draw.text((170, header_y + 5), USERNAME, font=font_username, fill=COLOR_WHITE)
    
    # Timestamp
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    font_timestamp = get_font(FONT_REGULAR, 26)
    draw.text((170, header_y + 55), timestamp, font=font_timestamp, fill=COLOR_GRAY)
    
    # ============================================================
    # 3. COIN INFO
    # ============================================================
    coin_y = 400
    font_coin = get_font(FONT_BOLD, 52)
    coin_text = f"{coin.upper()}USDT Perpetual"
    draw.text((60, coin_y), coin_text, font=font_coin, fill=COLOR_WHITE)
    
    # Direction | Leverage
    dir_y = coin_y + 70
    font_direction = get_font(FONT_MEDIUM, 34)
    
    direction_text = direction.upper().capitalize()
    draw.text((60, dir_y), direction_text, font=font_direction, fill=direction_color)
    
    dir_bbox = draw.textbbox((60, dir_y), direction_text, font=font_direction)
    sep_x = dir_bbox[2] + 20
    draw.text((sep_x, dir_y), "|", font=font_direction, fill=COLOR_GRAY)
    
    lev_text = f"{leverage}x"
    draw.text((sep_x + 30, dir_y), lev_text, font=font_direction, fill=COLOR_WHITE)
    
    # ============================================================
    # 4. BIG PNL
    # ============================================================
    pnl_y = 570
    font_pnl_big = get_font(FONT_BOLD, 110)
    
    pnl_sign = "+" if pnl_usdt >= 0 else ""
    pnl_text = f"{pnl_sign}{pnl_usdt:,.2f}"
    draw.text((60, pnl_y), pnl_text, font=font_pnl_big, fill=pnl_color)
    
    pnl_bbox = draw.textbbox((60, pnl_y), pnl_text, font=font_pnl_big)
    usdt_x = pnl_bbox[2] + 25
    usdt_y = pnl_y + 35
    font_usdt = get_font(FONT_MEDIUM, 44)
    draw.text((usdt_x, usdt_y), "USDT", font=font_usdt, fill=COLOR_GRAY)
    
    # ============================================================
    # 5. ROI %
    # ============================================================
    roi_y = pnl_y + 130
    font_roi = get_font(FONT_BOLD, 56)
    roi_sign = "+" if roi_percent >= 0 else ""
    roi_text = f"{roi_sign}{roi_percent:,.2f}%"
    draw.text((60, roi_y), roi_text, font=font_roi, fill=pnl_color)
    
    # ============================================================
    # 6. ENTRY & CLOSE PRICES
    # ============================================================
    prices_y = 830
    font_label = get_font(FONT_REGULAR, 28)
    font_price = get_font(FONT_BOLD, 42)
    
    # Entry Price
    draw.text((60, prices_y), "Entry Price", font=font_label, fill=COLOR_GRAY)
    draw.text(
        (60, prices_y + 45),
        format_price(entry_price),
        font=font_price,
        fill=COLOR_WHITE,
    )
    
    # Average Close Price
    right_x = 500
    draw.text((right_x, prices_y), "Average Close Price", font=font_label, fill=COLOR_GRAY)
    draw.text(
        (right_x, prices_y + 45),
        format_price(close_price),
        font=font_price,
        fill=COLOR_WHITE,
    )
    
    # ============================================================
    # 7. DIVIDER LINE
    # ============================================================
    footer_line_y = 1000
    draw.line(
        [(60, footer_line_y), (CARD_WIDTH - 60, footer_line_y)],
        fill=(40, 40, 40),
        width=1,
    )
    
    # ============================================================
    # 8. BINANCE LOGO (bottom left)
    # ============================================================
    logo_y = 1050
    try:
        if os.path.exists(BINANCE_LOGO):
            logo = Image.open(BINANCE_LOGO).convert("RGBA")
            logo_h = 90
            aspect = logo.width / logo.height
            logo_w = int(logo_h * aspect)
            logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
            card.paste(logo, (60, logo_y), logo)
        else:
            font_logo = get_font(FONT_BOLD, 32)
            draw.text((60, logo_y), "◆ BINANCE", font=font_logo, fill=COLOR_YELLOW)
            draw.text((60, logo_y + 40), "FUTURES", font=font_logo, fill=COLOR_WHITE)
    except Exception as e:
        print(f"[Card] Logo skipped: {e}")
    
    # ============================================================
    # 9. REFERRAL CODE
    # ============================================================
    ref_y = 1180
    font_ref_label = get_font(FONT_REGULAR, 26)
    font_ref_code = get_font(FONT_BOLD, 28)
    
    draw.text((60, ref_y), "Referral Code", font=font_ref_label, fill=COLOR_WHITE)
    ref_label_bbox = draw.textbbox((60, ref_y), "Referral Code", font=font_ref_label)
    draw.text(
        (ref_label_bbox[2] + 15, ref_y - 2),
        REFERRAL_CODE,
        font=font_ref_code,
        fill=COLOR_WHITE,
    )
    
    # ============================================================
    # 10. QR CODE (bottom right)
    # ============================================================
    try:
        if os.path.exists(QR_CODE):
            qr = Image.open(QR_CODE).convert("RGB")
            qr_size = 180
            qr = qr.resize((qr_size, qr_size), Image.LANCZOS)
            card.paste(qr, (CARD_WIDTH - qr_size - 60, 1050))
    except Exception as e:
        print(f"[Card] QR skipped: {e}")
    
    # ============================================================
    # SAVE
    # ============================================================
    output_dir = os.path.dirname(save_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    
    card.save(save_path, "PNG", quality=95)
    
    print(f"[Card] ✅ Saved: {save_path}")
    print(f"[Card] Coin: {coin} | Size: ${position_size} | Lev: {leverage}x")
    print(f"[Card] PNL: {pnl_usdt} USDT | ROI: {roi_percent}%")
    
    return save_path, {
        "position_size": position_size,
        "leverage": leverage,
        "pnl_usdt": pnl_usdt,
        "roi_percent": roi_percent,
    }


# ============================================================
# TEST FUNCTION
# ============================================================

if __name__ == "__main__":
    # Test with GENIUSUSDT sample (from your master copy)
    generate_position_card(
        coin="GENIUS",
        entry_price=0.6448343,
        close_price=0.4504418,
        direction="SHORT",
        leverage=50,
        position_size=250,
        save_path="charts/test_card.png",
    )
