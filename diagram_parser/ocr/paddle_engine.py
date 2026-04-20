"""PaddleOCR-based text extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from diagram_parser.config import OCRConfig
from diagram_parser.models import BoundingBox, OCRSpan, Point


def _load_paddleocr() -> Any:
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "PaddleOCR is not installed. Install dependencies from requirements.txt first."
        ) from exc
    return PaddleOCR


def _polygon_to_bbox(points: list[list[float]]) -> tuple[BoundingBox, tuple[Point, Point, Point, Point]]:
    polygon = tuple(Point(x=float(x), y=float(y)) for x, y in points)
    xs = [point.x for point in polygon]
    ys = [point.y for point in polygon]
    bbox = BoundingBox(
        left=min(xs),
        top=min(ys),
        right=max(xs),
        bottom=max(ys),
    )
    return bbox, polygon  # type: ignore[return-value]


def run_ocr(image_path: Path, config: OCRConfig) -> list[OCRSpan]:
    """Extract OCR spans from a diagram image."""

    PaddleOCR = _load_paddleocr()
    ocr = PaddleOCR(
        use_angle_cls=config.use_angle_cls,
        lang=config.language,
        show_log=False,
    )

    raw_result = ocr.ocr(str(image_path), cls=config.use_angle_cls)
    spans: list[OCRSpan] = []

    for page_index, page in enumerate(raw_result or []):
        if page is None:
            continue
        for line_index, line in enumerate(page):
            if len(line) != 2:
                continue
            points, recognition = line
            if not recognition or len(recognition) != 2:
                continue
            text, confidence = recognition
            text_value = str(text).strip()
            if not text_value:
                continue
            bbox, polygon = _polygon_to_bbox(points)
            spans.append(
                OCRSpan(
                    span_id=f"ocr-{page_index}-{line_index}",
                    text=text_value,
                    confidence=float(confidence),
                    bbox=bbox,
                    polygon=polygon,
                )
            )

    return spans
