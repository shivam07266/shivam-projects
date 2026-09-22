"""
DRISHTI - Fast image preparation.

Designed for:
- Gemini Vision direct image analysis
- Minimal image processing
- No expensive 3x2 tile generation
- EXIF orientation correction
- Controlled JPEG compression
- Optional lightweight OCR preprocessing
"""

import io
from typing import List, Tuple

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageOps, ImageFilter


# ============================================================
# GEMINI SETTINGS
# ============================================================

# Gemini gets a reasonably detailed version of the original image.
# Keeping this around 1800 gives good visual detail without
# creating huge request payloads.
GEMINI_MAX_DIM = 1800

# JPEG quality for Gemini uploads.
GEMINI_JPEG_QUALITY = 88


# ============================================================
# OCR SETTINGS
# ============================================================

# OCR is now deliberately lighter than the old 2000px pipeline.
OCR_MIN_LONG_EDGE = 1600
OCR_MAX_LONG_EDGE = 1800


# ============================================================
# IMAGE LOADING
# ============================================================

def _load_oriented(image_bytes: bytes) -> Image.Image:
    """
    Open an image and correctly apply EXIF orientation.

    This is important for photos taken vertically on phones.
    """

    image = Image.open(io.BytesIO(image_bytes))

    # Correct phone-camera orientation.
    image = ImageOps.exif_transpose(image)

    # Gemini and OpenCV work most predictably with RGB.
    if image.mode != "RGB":
        image = image.convert("RGB")

    return image


# ============================================================
# RESIZE
# ============================================================

def _resize_long_edge(
    image: Image.Image,
    target: int
) -> Image.Image:
    """
    Resize only when the image is larger than target.
    """

    width, height = image.size
    longest = max(width, height)

    if longest <= target:
        return image

    scale = target / longest

    new_size = (
        max(1, round(width * scale)),
        max(1, round(height * scale)),
    )

    return image.resize(
        new_size,
        Image.Resampling.LANCZOS
    )


# ============================================================
# JPEG ENCODING
# ============================================================

def _jpeg(
    image: Image.Image,
    quality: int = GEMINI_JPEG_QUALITY
) -> bytes:
    """
    Convert PIL image to JPEG bytes.
    """

    buffer = io.BytesIO()

    image.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
    )

    return buffer.getvalue()


# ============================================================
# GEMINI IMAGE BUILDER
# ============================================================

def build_gemini_image_set(
    image_bytes: bytes,
    mime_type: str = "image/jpeg"
) -> List[Tuple[bytes, str]]:
    """
    FAST Gemini image preparation.

    OLD:
        1 full image + 6 overlapping tiles

    NEW:
        1 properly oriented/resized image

    This dramatically reduces Gemini request size and latency.
    """

    image = _load_oriented(image_bytes)

    image = _resize_long_edge(
        image,
        GEMINI_MAX_DIM
    )

    encoded = _jpeg(
        image,
        GEMINI_JPEG_QUALITY
    )

    return [
        (
            encoded,
            "image/jpeg"
        )
    ]


# ============================================================
# OCR PREPROCESSING
# ============================================================

def enhance_for_ocr(
    image_bytes: bytes
) -> np.ndarray:
    """
    Lightweight OCR preprocessing.

    This is intentionally cheaper than the previous
    2000px + aggressive enhancement pipeline.
    """

    image = _load_oriented(image_bytes)

    longest = max(image.size)

    # Keep OCR image within a controlled size.
    if longest < OCR_MIN_LONG_EDGE:
        scale = OCR_MIN_LONG_EDGE / longest

        image = image.resize(
            (
                round(image.width * scale),
                round(image.height * scale),
            ),
            Image.Resampling.LANCZOS,
        )

    elif longest > OCR_MAX_LONG_EDGE:
        image = _resize_long_edge(
            image,
            OCR_MAX_LONG_EDGE
        )

    # Mild sharpening.
    image = ImageEnhance.Sharpness(
        image
    ).enhance(1.20)

    image = image.filter(
        ImageFilter.UnsharpMask(
            radius=1.0,
            percent=80,
            threshold=4,
        )
    )

    rgb = np.asarray(image)

    gray = cv2.cvtColor(
        rgb,
        cv2.COLOR_RGB2GRAY
    )

    # Mild CLAHE for printed package text.
    clahe = cv2.createCLAHE(
        clipLimit=1.8,
        tileGridSize=(8, 8)
    )

    enhanced = clahe.apply(gray)

    return cv2.cvtColor(
        enhanced,
        cv2.COLOR_GRAY2BGR
    )


# ============================================================
# OCR HINT FORMATTER
# ============================================================

def format_ocr_hint(
    ocr_items,
    max_items: int = 60
) -> str:
    """
    Convert OCR results into a compact hint for Gemini.
    """

    if not ocr_items:
        return "(no OCR readings available)"

    items = sorted(
        ocr_items,
        key=lambda item: item.get(
            "confidence",
            0.0
        ),
        reverse=True,
    )[:max_items]

    lines = []

    for item in items:

        text = str(
            item.get(
                "text",
                ""
            )
        ).strip()

        if not text:
            continue

        box = item.get("box") or []

        if box:

            xs = [
                point[0]
                for point in box
            ]

            ys = [
                point[1]
                for point in box
            ]

            position = (
                f"bbox=({min(xs)},"
                f"{min(ys)},"
                f"{max(xs)},"
                f"{max(ys)})"
            )

        else:
            position = "bbox=unknown"

        lines.append(
            f'- "{text}" '
            f'| confidence='
            f'{item.get("confidence", 0.0):.2f} '
            f'| source='
            f'{item.get("source", "unknown")} '
            f'| {position}'
        )

    if not lines:
        return "(no OCR readings available)"

    return "\n".join(lines)