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

    Layout (top → bottom):
      ┌────────────────────────────────────────┐
      │  DARK HEADER: event name + · + location │  ~22% h
      ├────────────────────────────────────────┤
      │                                        │
      │            FIRST NAME                  │  ~58% h
      │                                        │
      ├─ thin rule ────────────────────────────┤
      │  Company Name                          │  ~20% h
      └────────────────────────────────────────┘

    Args:
        opts: Dictionary with keys:
            - name: Person's name (will be uppercased)
            - company: Company name
            - groupName: Event/group name
            - location: Location name

    Returns:
        PNG image as bytes buffer
    """
    group_name = (opts.get("groupName") or "Energy & Utilities Data Connect").upper()
    location = (opts.get("location") or "Sydney").upper()
    name = (opts.get("name") or "YOUR NAME").upper()
    company = (opts.get("company") or "Company").upper()

    w = LABEL_WIDTH_PX
    h = LABEL_HEIGHT_PX
    pad = 14  # horizontal padding inside all bands

    img = Image.new("RGB", (w, h), color="#ffffff")
    draw = ImageDraw.Draw(img)

    # ── Header band ───────────────────────────────────────────────────────────
    # Dark filled rectangle spanning full width.
    header_h = round(h * 0.23)
    draw.rectangle([(0, 0), (w, header_h)], fill="#1a1a1a")

    # Event name takes ~62% of header height, location the remaining 38%.
    group_band_bottom = round(header_h * 0.62)
    _draw_centered_in_band(
        draw=draw,
        text=group_name,
        top=2,
        bottom=group_band_bottom,
        width=w,
        padding=pad,
        text_color="#ffffff",
        max_size=26,
        min_size=10,
        max_height_ratio=0.78,
    )
    _draw_centered_in_band(
        draw=draw,
        text=location,
        top=group_band_bottom,
        bottom=header_h - 2,
        width=w,
        padding=pad,
        text_color="#aaaaaa",
        max_size=18,
        min_size=9,
        max_height_ratio=0.78,
    )

    # ── Name zone ─────────────────────────────────────────────────────────────
    footer_h = round(h * 0.21)
    name_top = header_h + 4
    name_bottom = h - footer_h - 4
    _draw_centered_in_band(
        draw=draw,
        text=name,
        top=name_top,
        bottom=name_bottom,
        width=w,
        padding=pad,
        text_color="#000000",
        max_size=300,
        min_size=28,
        max_height_ratio=0.70,
    )

    # ── Separator & company footer ────────────────────────────────────────────
    sep_y = h - footer_h
    draw.line([(pad, sep_y), (w - pad, sep_y)], fill="#cccccc", width=3)
    _draw_centered_in_band(
        draw=draw,
        text=company,
        top=sep_y + 3,
        bottom=h - 5,
        width=w,
        padding=pad,
        text_color="#444444",
        max_size=60,
        min_size=12,
        max_height_ratio=0.75,
    )

    # Convert to PNG buffer
    png_buffer = BytesIO()
    img.save(png_buffer, format="PNG")
    png_buffer.seek(0)

    return png_buffer.getvalue()
