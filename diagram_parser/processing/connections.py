"""Stage 3: detect node-to-node connections with OpenCV."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from diagram_parser.config import ConnectionConfig
from diagram_parser.models import (
    CandidateNode,
    ConnectionCandidate,
    LineSegment,
    OCRSpan,
    Point,
)


def _load_cv_dependencies() -> tuple[Any, Any]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "OpenCV and NumPy are required for connection detection."
        ) from exc
    return cv2, np


def _distance_to_segment(point: Point, segment: LineSegment) -> float:
    x1, y1 = segment.start.x, segment.start.y
    x2, y2 = segment.end.x, segment.end.y
    px, py = point.x, point.y

    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5

    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    nearest_x = x1 + t * dx
    nearest_y = y1 + t * dy
    return ((px - nearest_x) ** 2 + (py - nearest_y) ** 2) ** 0.5


def _nearest_node(point: Point, nodes: list[CandidateNode], max_distance: float) -> CandidateNode | None:
    ranked = sorted(
        ((node.bbox.distance_to_point(point), node) for node in nodes),
        key=lambda item: item[0],
    )
    if not ranked or ranked[0][0] > max_distance:
        return None
    return ranked[0][1]


def _collect_label_hints(
    segment: LineSegment,
    spans: list[OCRSpan],
    excluded_span_ids: set[str],
    max_distance: float,
) -> tuple[str, ...]:
    nearby_hints: list[str] = []
    for span in spans:
        if span.span_id in excluded_span_ids:
            continue
        if _distance_to_segment(span.center, segment) <= max_distance:
            nearby_hints.append(span.text)
    return tuple(dict.fromkeys(nearby_hints))


def detect_connections(
    image_path: Path,
    spans: list[OCRSpan],
    nodes: list[CandidateNode],
    config: ConnectionConfig,
) -> tuple[list[ConnectionCandidate], tuple[int, int] | None]:
    """Detect simple line segments and map them to candidate nodes."""

    if not nodes:
        return [], None

    cv2, np = _load_cv_dependencies()
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"Unable to read image: {image_path}")

    image_height, image_width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blurred, config.canny_threshold_1, config.canny_threshold_2)

    # Mask OCR text boxes so text strokes do not dominate Hough line detection.
    masked_edges = edges.copy()
    for span in spans:
        padded = span.bbox.padded(config.text_mask_padding)
        cv2.rectangle(
            masked_edges,
            (int(padded.left), int(padded.top)),
            (int(padded.right), int(padded.bottom)),
            color=0,
            thickness=-1,
        )

    lines = cv2.HoughLinesP(
        masked_edges,
        rho=1,
        theta=np.pi / 180.0,
        threshold=config.hough_threshold,
        minLineLength=config.min_line_length,
        maxLineGap=config.max_line_gap,
    )

    if lines is None:
        return [], (image_width, image_height)

    grouped_connections: dict[tuple[str, str], list[ConnectionCandidate]] = defaultdict(list)
    node_to_span_ids = {node.node_id: set(node.text_span_ids) for node in nodes}

    for detected_line in lines:
        x1, y1, x2, y2 = detected_line[0]
        segment = LineSegment(
            start=Point(x=float(x1), y=float(y1)),
            end=Point(x=float(x2), y=float(y2)),
        )
        if segment.length < config.min_line_length:
            continue

        source = _nearest_node(segment.start, nodes, config.node_endpoint_distance)
        target = _nearest_node(segment.end, nodes, config.node_endpoint_distance)
        if source is None or target is None or source.node_id == target.node_id:
            continue

        key = tuple(sorted((source.node_id, target.node_id)))
        label_hints = _collect_label_hints(
            segment=segment,
            spans=spans,
            excluded_span_ids=node_to_span_ids[source.node_id] | node_to_span_ids[target.node_id],
            max_distance=config.line_label_distance,
        )
        confidence = min(0.99, 0.4 + (segment.length / max(image_width, image_height)))
        grouped_connections[key].append(
            ConnectionCandidate(
                from_node_id=source.node_id,
                to_node_id=target.node_id,
                line=segment,
                confidence=confidence,
                label_hints=label_hints,
            )
        )

    deduped_connections: list[ConnectionCandidate] = []
    for candidates in grouped_connections.values():
        best = max(candidates, key=lambda item: (item.confidence, item.line.length))
        deduped_connections.append(best)

    deduped_connections.sort(key=lambda edge: (edge.from_node_id, edge.to_node_id))
    return deduped_connections, (image_width, image_height)
