"""Name tag image generation service using Pillow."""

from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import os
import unicodedata
from typing import Optional
from dotenv import load_dotenv


load_dotenv()


_SYSTEM_FONT_PATHS = {
    "regular": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",  # macOS
        "/Library/Fonts/Arial.ttf",  # macOS user-installed
        "/System/Library/Fonts/Supplemental/Verdana.ttf",  # macOS
        "/Windows/Fonts/verdana.ttf",  # Windows
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
        "/Windows/Fonts/arial.ttf",  # Windows
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",  # Linux alternative
    ],
    "bold": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",  # macOS
        "/Library/Fonts/Arial Bold.ttf",  # macOS user-installed
        "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",  # macOS
        "/Windows/Fonts/verdanab.ttf",  # Windows
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
        "/Windows/Fonts/arialbd.ttf",  # Windows
        "/Windows/Fonts/arial.ttf",  # Windows fallback
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux fallback
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",  # Linux alternative
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",  # Linux fallback
    ],
}


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

LABEL_WIDTH_MM = _safe_float(os.getenv("NIIMBOT_LABEL_WIDTH_MM"), 70.0)
LABEL_HEIGHT_MM = _safe_float(os.getenv("NIIMBOT_LABEL_HEIGHT_MM"), 40.0)

_width_px_raw = _mm_to_px(LABEL_WIDTH_MM, _DPI)
_height_px_raw = _mm_to_px(LABEL_HEIGHT_MM, _DPI)

LABEL_WIDTH_PX = _align_to_multiple_of_8(max(8, _width_px_raw))
LABEL_HEIGHT_PX = _align_to_multiple_of_8(max(8, _height_px_raw))


def _measure_text(font: ImageFont.FreeTypeFont, text: str) -> tuple[int, int]:
    """Measure rendered text size for the given font."""
    left, top, right, bottom = font.getbbox(text)
    return right - left, bottom - top


def get_font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    """Get a font of specified size and weight, preferring Arial when available."""
    try:
        normalized_weight = "bold" if weight == "bold" else "regular"
        font_paths = (
            _SYSTEM_FONT_PATHS.get(normalized_weight, [])
            + _SYSTEM_FONT_PATHS["regular"]
        )

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
    weight: str = "regular",
) -> ImageFont.FreeTypeFont:
    """Find the largest available font that fits inside width/height bounds."""
    content = text if text else " "

    for size in range(max_size, min_size - 1, -1):
        font = get_font(size, weight=weight)
        text_w, text_h = _measure_text(font, content)
        if text_w <= max_width and text_h <= max_height:
            return font

    return get_font(min_size, weight=weight)


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
    weight: str = "regular",
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
        weight=weight,
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
    group_name = unicodedata.normalize("NFC", (opts.get("groupName") or "Energy & Utilities Data Connect")).upper()
    location = unicodedata.normalize("NFC", (opts.get("location") or "Sydney")).upper()
    name = unicodedata.normalize("NFC", (opts.get("name") or "YOUR NAME")).upper()
    company = unicodedata.normalize("NFC", (opts.get("company") or "Company")).upper()

    w = LABEL_WIDTH_PX
    h = LABEL_HEIGHT_PX
    pad = 14  # horizontal padding inside all bands

    rotate_text_raw = (os.getenv("NIIMBOT_ROTATE_TEXT_90") or "false").strip().lower()
    rotate_text_90 = rotate_text_raw in {"1", "true", "yes", "on"}

    canvas_w = h if rotate_text_90 else w
    canvas_h = w if rotate_text_90 else h

    # Render at 2x resolution and downscale (supersampling) so diacritics
    # such as the dot above Ė render as smooth circles rather than square blocks.
    SS = 2
    canvas_w, canvas_h = canvas_w * SS, canvas_h * SS
    img = Image.new("RGB", (canvas_w, canvas_h), color="#ffffff")
    draw = ImageDraw.Draw(img)
    pad = pad * SS

    # ── Header band ───────────────────────────────────────────────────────────
    # Dark filled rectangle spanning full width.
    header_h = round(canvas_h * 0.23)
    draw.rectangle([(0, 0), (canvas_w, header_h)], fill="#1a1a1a")

    # Event name takes ~55% of header height, location the remaining 45%.
    group_band_bottom = round(header_h * 0.55)
    _draw_centered_in_band(
        draw=draw,
        text=group_name,
        top=2 * SS,
        bottom=group_band_bottom,
        width=canvas_w,
        padding=pad,
        text_color="#ffffff",
        max_size=26 * SS,
        min_size=10 * SS,
        max_height_ratio=0.78,
        weight="bold",
    )
    _draw_centered_in_band(
        draw=draw,
        text=location,
        top=group_band_bottom,
        bottom=header_h - 2 * SS,
        width=canvas_w,
        padding=pad,
        text_color="#ffffff",
        max_size=28 * SS,
        min_size=9 * SS,
        max_height_ratio=0.82,
        weight="bold",
    )

    # ── Name zone ─────────────────────────────────────────────────────────────
    footer_h = round(canvas_h * 0.21)
    name_top = header_h + 4 * SS
    name_bottom = canvas_h - footer_h - 4 * SS
    _draw_centered_in_band(
        draw=draw,
        text=name,
        top=name_top,
        bottom=name_bottom,
        width=canvas_w,
        padding=pad,
        text_color="#000000",
        max_size=300 * SS,
        min_size=28 * SS,
        max_height_ratio=0.65,
        weight="bold",
    )

    # ── Separator & company footer ────────────────────────────────────────────
    sep_y = canvas_h - footer_h
    draw.line([(0, sep_y), (canvas_w, sep_y)], fill="#555555", width=3 * SS)
    _draw_centered_in_band(
        draw=draw,
        text=company,
        top=sep_y + 3 * SS,
        bottom=canvas_h - 5 * SS,
        width=canvas_w,
        padding=pad,
        text_color="#444444",
        max_size=60 * SS,
        min_size=12 * SS,
        max_height_ratio=0.75,
        weight="regular",
    )

    # Downscale from 2x to final label resolution
    img = img.resize((canvas_w // SS, canvas_h // SS), Image.Resampling.LANCZOS)

    if rotate_text_90:
        img = img.rotate(-90, expand=True)

    # Convert to PNG buffer
    png_buffer = BytesIO()
    img.save(png_buffer, format="PNG")
    png_buffer.seek(0)

    return png_buffer.getvalue()
