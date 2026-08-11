"""
Auto-generate Binance Logo and Diamond Watermark
Master copy quality - matches Binance Futures share card exactly.
"""

import os
from PIL import Image, ImageDraw, ImageFont

ASSETS_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")

# Colors
BINANCE_YELLOW = (240, 185, 11)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def create_binance_logo():
    """Create 'BINANCE FUTURES' logo - matches original Binance card."""
    width, height = 550, 140
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Load fonts
    try:
        font_binance = ImageFont.truetype(
            os.path.join(FONTS_DIR, "Inter_18pt-Bold.ttf"), 54
        )
        font_futures = ImageFont.truetype(
            os.path.join(FONTS_DIR, "Inter_18pt-Regular.ttf"), 46
        )
    except Exception as e:
        print(f"[Logo] Font load error: {e}")
        font_binance = ImageFont.load_default()
        font_futures = ImageFont.load_default()
    
    # Draw diamond icon (◆) - bigger and cleaner
    diamond_size = 48
    diamond_x = 5
    diamond_y = 15
    
    # Outer yellow diamond
    diamond_points = [
        (diamond_x + diamond_size // 2, diamond_y),
        (diamond_x + diamond_size, diamond_y + diamond_size // 2),
        (diamond_x + diamond_size // 2, diamond_y + diamond_size),
        (diamond_x, diamond_y + diamond_size // 2),
    ]
    draw.polygon(diamond_points, fill=BINANCE_YELLOW)
    
    # Inner black diamond (creates Binance logo look)
    inner_size = 20
    inner_x = diamond_x + (diamond_size - inner_size) // 2
    inner_y = diamond_y + (diamond_size - inner_size) // 2
    inner_points = [
        (inner_x + inner_size // 2, inner_y),
        (inner_x + inner_size, inner_y + inner_size // 2),
        (inner_x + inner_size // 2, inner_y + inner_size),
        (inner_x, inner_y + inner_size // 2),
    ]
    draw.polygon(inner_points, fill=BLACK)
    
    # "BINANCE" text - yellow
    text_x = diamond_x + diamond_size + 12
    draw.text((text_x, 0), "BINANCE", font=font_binance, fill=BINANCE_YELLOW)
    
    # "FUTURES" text - white, right below (tight stacking)
    draw.text((text_x, 68), "FUTURES", font=font_futures, fill=WHITE)
    
    save_path = os.path.join(ASSETS_DIR, "binance_logo.png")
    img.save(save_path, "PNG")
    print(f"✅ Created: {save_path}")


def create_diamond_watermark():
    """Create Binance-style watermark - single big diamond with subtle depth."""
    size = 1000
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    center = size // 2
    
    # Main big diamond (subtle gray)
    main_size = 850
    main_diamond = [
        (center, center - main_size // 2),
        (center + main_size // 2, center),
        (center, center + main_size // 2),
        (center - main_size // 2, center),
    ]
    draw.polygon(main_diamond, fill=(22, 22, 22, 255))
    
    # Inner diamond (very subtle depth)
    inner_size = 400
    inner_diamond = [
        (center, center - inner_size // 2),
        (center + inner_size // 2, center),
        (center, center + inner_size // 2),
        (center - inner_size // 2, center),
    ]
    draw.polygon(inner_diamond, fill=(28, 28, 28, 255))
    
    # Small center accent
    small_size = 150
    small_diamond = [
        (center, center - small_size // 2),
        (center + small_size // 2, center),
        (center, center + small_size // 2),
        (center - small_size // 2, center),
    ]
    draw.polygon(small_diamond, fill=(18, 18, 18, 255))
    
    save_path = os.path.join(ASSETS_DIR, "diamond_watermark.png")
    img.save(save_path, "PNG")
    print(f"✅ Created: {save_path}")


if __name__ == "__main__":
    print("🎨 Generating Binance-style assets...")
    create_binance_logo()
    create_diamond_watermark()
    print("✨ Done! All assets ready.")
