"""
Auto-generate Binance Logo and Diamond Watermark
Run this once to create static assets.
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
    """Create 'BINANCE FUTURES' logo with diamond icon."""
    width, height = 400, 100
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Load fonts
    try:
        font_bold = ImageFont.truetype(
            os.path.join(FONTS_DIR, "Inter-Bold.ttf"), 38
        )
        font_regular = ImageFont.truetype(
            os.path.join(FONTS_DIR, "Inter-Regular.ttf"), 30
        )
    except Exception:
        font_bold = ImageFont.load_default()
        font_regular = ImageFont.load_default()
    
    # Draw diamond icon (◆)
    diamond_size = 32
    diamond_x = 5
    diamond_y = 15
    
    # Diamond shape
    diamond_points = [
        (diamond_x + diamond_size // 2, diamond_y),
        (diamond_x + diamond_size, diamond_y + diamond_size // 2),
        (diamond_x + diamond_size // 2, diamond_y + diamond_size),
        (diamond_x, diamond_y + diamond_size // 2),
    ]
    draw.polygon(diamond_points, fill=BINANCE_YELLOW)
    
    # "BINANCE" text
    draw.text((50, 8), "BINANCE", font=font_bold, fill=BINANCE_YELLOW)
    
    # "FUTURES" text (below)
    draw.text((50, 52), "FUTURES", font=font_regular, fill=WHITE)
    
    save_path = os.path.join(ASSETS_DIR, "binance_logo.png")
    img.save(save_path, "PNG")
    print(f"✅ Created: {save_path}")


def create_diamond_watermark():
    """Create large diamond watermark for card background."""
    size = 800
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Colors (very dark gray, semi-transparent)
    dark_color = (25, 25, 25, 255)
    darker_color = (15, 15, 15, 255)
    
    # Big diamond (outer)
    big_diamond = [
        (size // 2, 50),
        (size - 50, size // 2),
        (size // 2, size - 50),
        (50, size // 2),
    ]
    draw.polygon(big_diamond, fill=dark_color)
    
    # Inner diamond (smaller, darker)
    inner_size = 300
    inner_x = (size - inner_size) // 2
    inner_y = (size - inner_size) // 2
    inner_diamond = [
        (inner_x + inner_size // 2, inner_y),
        (inner_x + inner_size, inner_y + inner_size // 2),
        (inner_x + inner_size // 2, inner_y + inner_size),
        (inner_x, inner_y + inner_size // 2),
    ]
    draw.polygon(inner_diamond, fill=darker_color)
    
    # Center small diamond
    center_size = 120
    center_x = (size - center_size) // 2
    center_y = (size - center_size) // 2
    center_diamond = [
        (center_x + center_size // 2, center_y),
        (center_x + center_size, center_y + center_size // 2),
        (center_x + center_size // 2, center_y + center_size),
        (center_x, center_y + center_size // 2),
    ]
    draw.polygon(center_diamond, fill=dark_color)
    
    save_path = os.path.join(ASSETS_DIR, "diamond_watermark.png")
    img.save(save_path, "PNG")
    print(f"✅ Created: {save_path}")


if __name__ == "__main__":
    print("🎨 Generating assets...")
    create_binance_logo()
    create_diamond_watermark()
    print("✨ Done! All assets ready.")
