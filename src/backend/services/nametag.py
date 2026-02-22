"""Name tag image generation service using Pillow."""

from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import os


# Label 80mm x 50mm at 203 DPI
LABEL_WIDTH_PX = round((80 / 25.4) * 203)  # ~639
LABEL_HEIGHT_PX = round((50 / 25.4) * 203)  # ~400

# Font size configuration for responsive scaling
FONT_SIZES = {
    "name": {
        "base": 70,
        "thresholds": [
            {"length": 8, "size": 55},
            {"length": 12, "size": 45},
            {"length": 15, "size": 35},
        ],
    },
    "company": {
        "base": 22,
        "thresholds": [
            {"length": 15, "size": 20},
            {"length": 20, "size": 18},
            {"length": 25, "size": 16},
        ],
    },
    "group": {
        "base": 15,
        "thresholds": [
            {"length": 25, "size": 14},
            {"length": 30, "size": 13},
            {"length": 35, "size": 12},
            {"length": 40, "size": 11},
        ],
    },
    "location": {
        "base": 16,
        "thresholds": [
            {"length": 25, "size": 15},
            {"length": 30, "size": 14},
            {"length": 35, "size": 13},
            {"length": 40, "size": 12},
        ],
    },
}

SVG_CONFIG = {
    "padding": 10,
    "divider_color": "#ddd",
    "divider_width": 1,
    "text_color": "#1a1a1a",
    "font_family": "Arial",
}


def calculate_font_size(text: str, config: dict) -> int:
    """Calculate responsive font size based on text length."""
    size = config["base"]
    for threshold in config["thresholds"]:
        if len(text) > threshold["length"]:
            size = threshold["size"]
    return size


def get_font(size: int) -> ImageFont.FreeTypeFont:
    """Get a TrueType font of specified size. Falls back to default if not available."""
    try:
        # Try common system font paths
        font_paths = [
            "/System/Library/Fonts/Helvetica.ttc",  # macOS
            "/Windows/Fonts/arial.ttf",  # Windows
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",  # Linux alternative
        ]

        for font_path in font_paths:
            if os.path.exists(font_path):
                return ImageFont.truetype(font_path, size)
    except Exception:
        pass

    # Fallback to default font
    return ImageFont.load_default()


def generate_nametag_image(opts: dict) -> bytes:
    """
    Generate name tag as PNG buffer for Niimbot B3S (80mm x 50mm landscape).

    Args:
        opts: Dictionary with keys:
            - name: Person's name (uppercase)
            - company: Company name
            - groupName: Event/group name
            - location: Location name

    Returns:
        PNG image as bytes buffer
    """
    group_name = (opts.get("groupName") or "Energy & Utilities Data Connect").upper()
    location = (opts.get("location") or "Sydney").upper()
    name = (opts.get("name") or "YOUR NAME").upper()
    company = opts.get("company") or "Company"

    w = LABEL_WIDTH_PX
    h = LABEL_HEIGHT_PX
    padding = SVG_CONFIG["padding"]

    # Create white image
    img = Image.new("RGB", (w, h), color="white")
    draw = ImageDraw.Draw(img)

    # Vertical sections: top (event), middle (name), bottom (company)
    top_h = round(h * 0.25)
    middle_h = round(h * 0.5)
    bottom_h = h - top_h - middle_h

    div_y1 = top_h
    div_y2 = top_h + middle_h

    # Calculate font sizes based on text length
    name_font_size = calculate_font_size(name, FONT_SIZES["name"])
    company_font_size = calculate_font_size(company, FONT_SIZES["company"])
    group_font_size = calculate_font_size(group_name, FONT_SIZES["group"])
    location_font_size = calculate_font_size(location, FONT_SIZES["location"])

    # Get fonts
    name_font = get_font(name_font_size)
    company_font = get_font(company_font_size)
    group_font = get_font(group_font_size)
    location_font = get_font(location_font_size)
    divider_color = SVG_CONFIG["divider_color"]
    text_color = SVG_CONFIG["text_color"]

    # Top section: Event name and location
    top_center_y = top_h // 2
    draw.text(
        (w // 2, top_center_y - 9),
        group_name,
        fill=text_color,
        font=group_font,
        anchor="mm",
    )
    draw.text(
        (w // 2, top_center_y + 9),
        location,
        fill=text_color,
        font=location_font,
        anchor="mm",
    )

    # Top divider
    draw.line(
        [(padding, div_y1), (w - padding, div_y1)],
        fill=divider_color,
        width=SVG_CONFIG["divider_width"],
    )

    # Middle section: Name
    draw.text(
        (w // 2, top_h + middle_h // 2),
        name,
        fill=text_color,
        font=name_font,
        anchor="mm",
    )

    # Bottom divider
    draw.line(
        [(padding, div_y2), (w - padding, div_y2)],
        fill=divider_color,
        width=SVG_CONFIG["divider_width"],
    )

    # Bottom section: Company
    draw.text(
        (w // 2, div_y2 + bottom_h // 2),
        company,
        fill=text_color,
        font=company_font,
        anchor="mm",
    )

    # Convert to PNG buffer
    png_buffer = BytesIO()
    img.save(png_buffer, format="PNG")
    png_buffer.seek(0)

    return png_buffer.getvalue()
