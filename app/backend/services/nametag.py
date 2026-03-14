"""Name tag image generation service using Pillow."""

from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import os
from typing import Optional


def _align_to_multiple_of_8(value: int) -> int:
    """Align integer pixel value to the next multiple of 8."""
    return ((value + 7) // 8) * 8


def _safe_float(value: Optional[str], default: float) -> float:
    """Parse float env value with a safe default."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mm_to_px(mm: float, dpi: int = 203) -> int:
    """Convert millimeters to pixels for a given DPI."""
    return round((mm / 25.4) * dpi)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    """Clamp integer into [minimum, maximum] range."""
    return max(minimum, min(value, maximum))


# Label dimensions are env-driven and converted to printer-safe pixels.
# Defaults target B3S_P real label: 70mm x 40mm.
_DPI = 203
_MAX_B3S_WIDTH_PX = 576
_MAX_B3S_HEIGHT_PX = 400

LABEL_WIDTH_MM = _safe_float(os.getenv("NIIMBOT_LABEL_WIDTH_MM"), 70.0)
LABEL_HEIGHT_MM = _safe_float(os.getenv("NIIMBOT_LABEL_HEIGHT_MM"), 40.0)

_width_px_raw = _mm_to_px(LABEL_WIDTH_MM, _DPI)
_height_px_raw = _mm_to_px(LABEL_HEIGHT_MM, _DPI)

LABEL_WIDTH_PX = _align_to_multiple_of_8(_clamp(_width_px_raw, 8, _MAX_B3S_WIDTH_PX))
LABEL_HEIGHT_PX = _align_to_multiple_of_8(_clamp(_height_px_raw, 8, _MAX_B3S_HEIGHT_PX))

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


def _measure_text(font: ImageFont.FreeTypeFont, text: str) -> tuple[int, int]:
    """Measure rendered text size for the given font."""
    left, top, right, bottom = font.getbbox(text)
    return right - left, bottom - top


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


def _fit_font_for_box(
    text: str,
    max_width: int,
    max_height: int,
    max_size: int,
    min_size: int,
) -> ImageFont.FreeTypeFont:
    """Find the largest available font that fits inside width/height bounds."""
    content = text if text else " "

    for size in range(max_size, min_size - 1, -1):
        font = get_font(size)
        text_w, text_h = _measure_text(font, content)
        if text_w <= max_width and text_h <= max_height:
            return font

    return get_font(min_size)


def _draw_centered_in_band(
    draw: ImageDraw.ImageDraw,
    text: str,
    top: int,
    bottom: int,
    width: int,
    padding: int,
    text_color: str,
    max_size: int,
    min_size: int,
    max_height_ratio: float = 1.0,
) -> None:
    """Draw centered text in a horizontal band using best-fit font size."""
    band_height = max(1, bottom - top)
    fitted_max_height = max(1, int((band_height - 4) * max(0.1, min(max_height_ratio, 1.0))))
    font = _fit_font_for_box(
        text=text,
        max_width=max(1, width - (padding * 2)),
        max_height=fitted_max_height,
        max_size=max_size,
        min_size=min_size,
    )
    draw.text((width // 2, top + band_height // 2), text, fill=text_color, font=font, anchor="mm")


def generate_nametag_image(opts: dict) -> bytes:
    """
    Generate name tag PNG for Niimbot B3S/B3S_P using env-configured label size.

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

    # Add outer breathing room (top/bottom), then apply requested proportions inside.
    top_margin = max(8, round(h * 0.03))
    bottom_margin = max(10, round(h * 0.04))
    content_top = top_margin
    content_bottom = h - bottom_margin
    content_h = max(12, content_bottom - content_top)

    # Vertical allocation requested:
    # - Name: 75% of content height
    # - Remaining 25% split equally between group/location/company (~8.33% each)
    small_h = max(1, round(content_h / 12))
    name_h = max(1, content_h - (small_h * 3))

    group_top = content_top
    group_bottom = group_top + small_h
    location_top = group_bottom
    location_bottom = location_top + small_h
    name_top = location_bottom
    company_bottom = content_bottom

    divider_color = SVG_CONFIG["divider_color"]
    text_color = SVG_CONFIG["text_color"]

    # Extra visual separation between event name and location text
    event_location_gap = max(4, round(h * 0.012))
    group_text_bottom = max(group_top + 1, group_bottom - (event_location_gap // 2))
    location_text_top = min(location_bottom - 1, location_top + (event_location_gap // 2))
    divider_text_gap = max(8, round(h * 0.025))

    # Keep divider distances symmetric from top and bottom of content area.
    top_divider_y = location_bottom
    top_divider_offset = top_divider_y - content_top
    bottom_divider_y = content_bottom - top_divider_offset
    bottom_divider_y = max(top_divider_y + max(28, round(h * 0.09)), bottom_divider_y)
    bottom_divider_y = min(content_bottom - max(14, round(h * 0.045)), bottom_divider_y)
    company_top = bottom_divider_y

    # Keep text farther from divider lines
    location_text_bottom = max(location_text_top + 1, top_divider_y - divider_text_gap)
    name_text_top = min(bottom_divider_y - 1, name_top + divider_text_gap)
    name_text_bottom = max(name_text_top + 1, bottom_divider_y - divider_text_gap)
    company_divider_gap = max(4, round(h * 0.012))
    company_text_top = min(company_bottom - 1, company_top + company_divider_gap)

    _draw_centered_in_band(
        draw=draw,
        text=group_name,
        top=group_top,
        bottom=group_text_bottom,
        width=w,
        padding=padding,
        text_color=text_color,
        max_size=38,
        min_size=12,
    )
    _draw_centered_in_band(
        draw=draw,
        text=location,
        top=location_text_top,
        bottom=location_text_bottom,
        width=w,
        padding=padding,
        text_color=text_color,
        max_size=32,
        min_size=11,
    )

    # Single top divider under location
    draw.line(
        [(padding, top_divider_y), (w - padding, top_divider_y)],
        fill=divider_color,
        width=SVG_CONFIG["divider_width"],
    )

    # Lower divider between name and company (symmetrical placement)
    draw.line(
        [(padding, bottom_divider_y), (w - padding, bottom_divider_y)],
        fill=divider_color,
        width=SVG_CONFIG["divider_width"],
    )

    _draw_centered_in_band(
        draw=draw,
        text=name,
        top=name_text_top,
        bottom=name_text_bottom,
        width=w,
        padding=padding,
        text_color=text_color,
        max_size=300,
        min_size=24,
        max_height_ratio=0.62,
    )

    _draw_centered_in_band(
        draw=draw,
        text=company,
        top=company_text_top,
        bottom=company_bottom,
        width=w,
        padding=padding,
        text_color=text_color,
        max_size=80,
        min_size=14,
        max_height_ratio=0.82,
    )

    # Convert to PNG buffer
    png_buffer = BytesIO()
    img.save(png_buffer, format="PNG")
    png_buffer.seek(0)

    return png_buffer.getvalue()
