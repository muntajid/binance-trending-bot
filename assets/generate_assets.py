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
    width, height = 500, 130
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Load fonts
    try:
        font_binance = ImageFont.truetype(
            os.path.join(FONTS_DIR, "Inter_18pt-Bold.ttf"), 48
        )
        font_futures = ImageFont.truetype(
            os.path.join(FONTS_DIR, "Inter_18pt-Regular.ttf"), 42
        )
    except Exception as e:
        print(f"[Logo] Font load error: {e}")
        font_binance = ImageFont.load_default()
        font_futures = ImageFont.load_default()
    
    # Draw diamond icon (◆) - larger and more prominent
    diamond_size = 42
    diamond_x = 5
    diamond_y = 12
    
    # Outer diamond shape
    diamond_points = [
        (diamond_x + diamond_size // 2, diamond_y),                    # top
        (diamond_x + diamond_size, diamond_y + diamond_size // 2),     # right
        (diamond_x + diamond_size // 2, diamond_y + diamond_size),     # bottom
        (diamond_x, diamond_y + diamond_size // 2),                    # left
    ]
    draw.polygon(diamond_points, fill=BINANCE_YELLOW)
    
    # Inner diamond cutout (creates the classic Binance logo look)
    inner_size = 18
    inner_x = diamond_x + (diamond_size - inner_size) // 2
    inner_y = diamond_y + (diamond_size - inner_size) // 2
    inner_points = [
        (inner_x + inner_size // 2, inner_y),
        (inner_x + inner_size, inner_y + inner_size // 2),
        (inner_x + inner_size // 2, inner_y + inner_size),
        (inner_x, inner_y + inner_size // 2),
    ]
    draw.polygon(inner_points, fill=(0, 0, 0, 0))
    
    # "BINANCE" text - yellow
    text_x = diamond_x + diamond_size + 15
    draw.text((text_x, 5), "BINANCE", font=font_binance, fill=BINANCE_YELLOW)
    
    # "FUTURES" text - white, below
    draw.text((text_x, 70), "FUTURES", font=font_futures, fill=WHITE)
    
    save_path = os.path.join(ASSETS_DIR, "binance_logo.png")
    img.save(save_path, "PNG")
    print(f"✅ Created: {save_path}")


def create_diamond_watermark():
    """Create Binance-style layered diamond watermark for card background."""
    size = 900
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Color layers (very subtle grays, similar to real Binance card)
    layer1 = (18, 18, 18, 255)   # Outermost - very dark
    layer2 = (26, 26, 26, 255)   # Middle
    layer3 = (34, 34, 34, 255)   # Inner
    layer4 = (22, 22, 22, 255)   # Center small
    
    center = size // 2
    
    # Layer 1: Biggest outer diamond
    d1 = 780
    diamond1 = [
        (center, center - d1 // 2),
        (center + d1 // 2, center),
        (center, center + d1 // 2),
        (center - d1 // 2, center),
    ]
    draw.polygon(diamond1, fill=layer1)
    
    # Layer 2: Second diamond (offset for depth)
    d2 = 620
    diamond2 = [
        (center, center - d2 // 2),
        (center + d2 // 2, center),
        (center, center + d2 // 2),
        (center - d2 // 2, center),
    ]
    draw.polygon(diamond2, fill=layer2)
    
    # Layer 3: Inner diamond
    d3 = 400
    diamond3 = [
        (center, center - d3 // 2),
        (center + d3 // 2, center),
        (center, center + d3 // 2),
        (center - d3 // 2, center),
    ]
    draw.polygon(diamond3, fill=layer3)
    
    # Layer 4: Center small diamond
    d4 = 200
    diamond4 = [
        (center, center - d4 // 2),
        (center + d4 // 2, center),
        (center, center + d4 // 2),
        (center - d4 // 2, center),
    ]
    draw.polygon(diamond4, fill=layer4)
    
    save_path = os.path.join(ASSETS_DIR, "diamond_watermark.png")
    img.save(save_path, "PNG")
    print(f"✅ Created: {save_path}")


if __name__ == "__main__":
    print("🎨 Generating Binance-style assets...")
    create_binance_logo()
    create_diamond_watermark()
    print("✨ Done! All assets ready.")
