"""Turning a file on disk into something a vision model will accept.

The base64 envelope and the OpenAI-shaped message. The pixels themselves are
`mirobody.documents.render`'s job: this module used to open pypdfium2 and
Pillow itself, with its own thresholds, so a page rendered here and a page
rendered for OCR came out at different resolutions.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

from mirobody.documents import render

logger = logging.getLogger(__name__)

# =============================================================================

class FileProcessor:
    """Base file processor with image optimization."""

    @staticmethod
    def optimize_image_for_llm(
        image_data: bytes,
        max_dimension: int = 2048,
        quality: int = 85,
        format: str = "JPEG",
    ) -> tuple[bytes, dict]:
        """Optimize image by reducing resolution and applying compression."""
        return render.fit_image(image_data, max_edge=max_dimension, quality=quality, fmt=format)


# =============================================================================
# Common Processing Utilities
# =============================================================================

def _convert_pdf_to_base64_images(pdf_path: str, scale: float = 1.5) -> list[dict[str, Any]]:
    """Convert PDF pages to optimized base64 images."""
    started = time.time()
    with open(pdf_path, "rb") as f:
        pages = render.pdf_pages_as_images(f.read(), scale=scale)
    logger.info(  # phi: ok a page count and an elapsed time, no page contents
        f"{len(pages)} pages rendered in {time.time() - started:.2f}s")
    return [
        {
            "page_num": i + 1,
            "base64_image": base64.b64encode(blob).decode("utf-8"),
            "conversion_time": stats.get("processing_time", 0.0),
            "stats": stats,
        }
        for i, (blob, stats) in enumerate(pages)
    ]


def _build_vision_message(base64_image: str, prompt: str, json_mode: bool) -> list[dict]:
    """Build OpenAI-compatible vision message."""
    text_content = f"{prompt}. Please return the result in JSON format." if json_mode else prompt
    return [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
            {"type": "text", "text": text_content}
        ]
    }]


def _read_and_optimize_image(image_path: str) -> tuple[str, dict]:
    """Read image file and return optimized base64 string."""
    with open(image_path, "rb") as f:
        img_data = f.read()
    optimized_data, stats = FileProcessor.optimize_image_for_llm(
        img_data, max_dimension=1536, quality=85
    )
    return base64.b64encode(optimized_data).decode('utf-8'), stats


