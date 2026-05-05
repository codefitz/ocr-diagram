"""PaddleOCR-based text extraction."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any

from diagram_parser.config import OCRConfig
from diagram_parser.models import BoundingBox, DocumentPage, OCRSpan, Point


def _load_paddleocr() -> Any:
    try:
        import paddle  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "PaddleOCR requires PaddlePaddle (`paddle`) at runtime. "
            "Install `paddlepaddle` in a supported Python environment before running OCR. "
            "Python 3.11 or 3.12 is the safer choice for Paddle-based stacks."
        ) from exc
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "PaddleOCR is not installed. Install dependencies from requirements.txt first."
        ) from exc
    return PaddleOCR


def _load_cv2() -> Any:
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "OpenCV is required to load raster images for OCR."
        ) from exc
    return cv2


def _load_pdfium() -> Any:
    try:
        import pypdfium2 as pdfium  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "PDF input requires pypdfium2, which is not installed."
        ) from exc
    return pdfium


def load_document_pages(source_path: Path, config: OCRConfig) -> list[DocumentPage]:
    """Load raster images directly or render PDF pages to raster images."""

    if not source_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {source_path}")
    if not source_path.is_file():
        raise FileNotFoundError(f"Input path is not a file: {source_path}")

    suffix = source_path.suffix.lower()
    if suffix == ".pdf":
        pdfium = _load_pdfium()
        try:
            document = pdfium.PdfDocument(str(source_path))
        except Exception as exc:
            raise RuntimeError(f"Unable to open PDF input: {source_path}") from exc
        pages: list[DocumentPage] = []
        page_limit = min(len(document), config.max_pages) if config.max_pages is not None else len(document)
        for page_index in range(page_limit):
            try:
                rendered = document[page_index].render(scale=config.pdf_render_scale).to_numpy()
            except Exception as exc:
                raise RuntimeError(
                    f"Unable to render PDF page {page_index + 1} from {source_path}"
                ) from exc
            height, width = rendered.shape[:2]
            pages.append(
                DocumentPage(
                    page_id=f"page-{page_index + 1}",
                    source_path=source_path,
                    image=rendered,
                    width=width,
                    height=height,
                )
            )
        return pages

    cv2 = _load_cv2()
    image = cv2.imread(str(source_path))
    if image is None:
        raise FileNotFoundError(f"Unable to read image: {source_path}")
    height, width = image.shape[:2]
    return [
        DocumentPage(
            page_id="page-1",
            source_path=source_path,
            image=image,
            width=width,
            height=height,
        )
    ]


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


def _normalize_polygon(raw_polygon: Any) -> list[list[float]]:
    if hasattr(raw_polygon, "tolist"):
        raw_polygon = raw_polygon.tolist()

    points: list[list[float]] = []
    for point in raw_polygon:
        if hasattr(point, "tolist"):
            point = point.tolist()
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            points.append([float(point[0]), float(point[1])])
    if len(points) != 4:
        raise ValueError(f"Unexpected OCR polygon format: {raw_polygon!r}")
    return points


def _coerce_prediction_payload(result: Any) -> Any:
    json_attr = getattr(result, "json", None)
    if isinstance(json_attr, dict):
        if isinstance(json_attr.get("res"), dict):
            return json_attr["res"]
        return json_attr
    if isinstance(json_attr, str):
        try:
            parsed = json.loads(json_attr)
            if isinstance(parsed, dict) and isinstance(parsed.get("res"), dict):
                return parsed["res"]
            return parsed
        except json.JSONDecodeError:
            pass

    if isinstance(result, dict):
        if isinstance(result.get("res"), dict):
            return result["res"]
        return result
    if isinstance(result, list):
        return result

    for attribute_name in ("res",):
        attribute_value = getattr(result, attribute_name, None)
        if isinstance(attribute_value, (dict, list)):
            if isinstance(attribute_value, dict) and isinstance(attribute_value.get("res"), dict):
                return attribute_value["res"]
            return attribute_value

    for method_name in ("to_dict", "model_dump"):
        method = getattr(result, method_name, None)
        if callable(method):
            payload = method()
            if isinstance(payload, (dict, list)):
                if isinstance(payload, dict) and isinstance(payload.get("res"), dict):
                    return payload["res"]
                return payload

    raise ValueError(
        f"Unsupported PaddleOCR result payload type: {type(result).__name__}"
    )


def _extract_spans_from_payload(
    payload: Any,
    page_id: str,
    prediction_index: int,
) -> list[OCRSpan]:
    spans: list[OCRSpan] = []

    if isinstance(payload, list):
        for line_index, line in enumerate(payload):
            if not isinstance(line, (list, tuple)) or len(line) != 2:
                continue
            points, recognition = line
            if not recognition or len(recognition) != 2:
                continue
            text, confidence = recognition
            text_value = str(text).strip()
            if not text_value:
                continue
            bbox, polygon = _polygon_to_bbox(_normalize_polygon(points))
            spans.append(
                OCRSpan(
                    page_id=page_id,
                    span_id=f"{page_id}-ocr-{prediction_index}-{line_index}",
                    text=text_value,
                    confidence=float(confidence),
                    bbox=bbox,
                    polygon=polygon,
                )
            )
        return spans

    if not isinstance(payload, dict):
        raise ValueError(f"Unsupported OCR payload: {payload!r}")

    polygons = payload.get("dt_polys") or payload.get("text_det_polys") or []
    texts = payload.get("rec_texts") or []
    scores = payload.get("rec_scores") or [1.0] * len(texts)

    for line_index, (points, text, confidence) in enumerate(zip(polygons, texts, scores)):
        text_value = str(text).strip()
        if not text_value:
            continue
        bbox, polygon = _polygon_to_bbox(_normalize_polygon(points))
        spans.append(
            OCRSpan(
                page_id=page_id,
                span_id=f"{page_id}-ocr-{prediction_index}-{line_index}",
                text=text_value,
                confidence=float(confidence),
                bbox=bbox,
                polygon=polygon,
            )
        )

    return spans


def _build_ocr_init_kwargs(PaddleOCR: Any, config: OCRConfig) -> dict[str, Any]:
    parameters = inspect.signature(PaddleOCR).parameters
    kwargs: dict[str, Any] = {}

    if "lang" in parameters:
        kwargs["lang"] = config.language
    if "use_textline_orientation" in parameters:
        kwargs["use_textline_orientation"] = config.use_angle_cls
    elif "use_angle_cls" in parameters:
        kwargs["use_angle_cls"] = config.use_angle_cls
    if "text_detection_model_dir" in parameters and config.text_detection_model_dir:
        kwargs["text_detection_model_dir"] = config.text_detection_model_dir
    if "text_recognition_model_dir" in parameters and config.text_recognition_model_dir:
        kwargs["text_recognition_model_dir"] = config.text_recognition_model_dir
    if "textline_orientation_model_dir" in parameters and config.textline_orientation_model_dir:
        kwargs["textline_orientation_model_dir"] = config.textline_orientation_model_dir

    return kwargs


def run_ocr(pages: list[DocumentPage], config: OCRConfig) -> list[OCRSpan]:
    """Extract OCR spans from one or more rasterized document pages."""

    if config.disable_model_source_check:
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    PaddleOCR = _load_paddleocr()
    try:
        ocr = PaddleOCR(**_build_ocr_init_kwargs(PaddleOCR, config))
    except Exception as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "Failed to initialize PaddleOCR. If the environment is offline, provide local PaddleOCR model directories "
            "for detection/recognition/orientation in OCRConfig."
        ) from exc

    spans: list[OCRSpan] = []
    for page in pages:
        raw_results = ocr.predict(
            page.image,
            use_textline_orientation=config.use_angle_cls,
        )
        for prediction_index, result in enumerate(raw_results or []):
            spans.extend(
                _extract_spans_from_payload(
                    payload=_coerce_prediction_payload(result),
                    page_id=page.page_id,
                    prediction_index=prediction_index,
                )
            )

    return spans


def build_ocr_cache_payload(
    *,
    source_path: Path,
    config: OCRConfig,
    spans: list[OCRSpan],
) -> dict[str, Any]:
    """Serialize OCR spans with the settings required to validate cache reuse."""

    return {
        "source_path": str(source_path),
        "ocr_config": {
            "language": config.language,
            "use_angle_cls": config.use_angle_cls,
            "pdf_render_scale": config.pdf_render_scale,
            "max_pages": config.max_pages,
        },
        "spans": [span.to_dict() for span in spans],
    }


def load_ocr_cache(cache_path: Path, *, source_path: Path, config: OCRConfig) -> list[OCRSpan] | None:
    """Load cached OCR spans when the cache matches the current source and OCR settings."""

    if not cache_path.exists():
        return None

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    cached_source = payload.get("source_path")
    cached_config = payload.get("ocr_config", {})
    if cached_source != str(source_path):
        return None
    if cached_config.get("language") != config.language:
        return None
    if bool(cached_config.get("use_angle_cls")) != config.use_angle_cls:
        return None
    if float(cached_config.get("pdf_render_scale", -1.0)) != float(config.pdf_render_scale):
        return None
    if cached_config.get("max_pages") != config.max_pages:
        return None

    spans_payload = payload.get("spans", [])
    return [OCRSpan.from_dict(item) for item in spans_payload]


def save_ocr_cache(cache_path: Path, *, source_path: Path, config: OCRConfig, spans: list[OCRSpan]) -> None:
    """Persist OCR spans so later grouping/connection tuning can skip PaddleOCR."""

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            build_ocr_cache_payload(source_path=source_path, config=config, spans=spans),
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
