"""Shared data contracts between stages."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUPPORTED_NODE_TYPES = {
    "server",
    "database",
    "application",
    "network",
    "zone",
    "unknown",
}

IP_ADDRESS_PATTERN = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
)


def slugify(value: str) -> str:
    """Create stable IDs from diagram labels."""

    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "node"


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float

    def to_dict(self) -> dict[str, float]:
        return {"x": round(self.x, 2), "y": round(self.y, 2)}


@dataclass(frozen=True, slots=True)
class BoundingBox:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def center(self) -> Point:
        return Point(
            x=(self.left + self.right) / 2.0,
            y=(self.top + self.bottom) / 2.0,
        )

    def padded(self, padding: float) -> "BoundingBox":
        return BoundingBox(
            left=self.left - padding,
            top=self.top - padding,
            right=self.right + padding,
            bottom=self.bottom + padding,
        )

    def union(self, other: "BoundingBox") -> "BoundingBox":
        return BoundingBox(
            left=min(self.left, other.left),
            top=min(self.top, other.top),
            right=max(self.right, other.right),
            bottom=max(self.bottom, other.bottom),
        )

    def contains(self, point: Point) -> bool:
        return (
            self.left <= point.x <= self.right
            and self.top <= point.y <= self.bottom
        )

    def distance_to_box(self, other: "BoundingBox") -> float:
        dx = max(other.left - self.right, self.left - other.right, 0.0)
        dy = max(other.top - self.bottom, self.top - other.bottom, 0.0)
        return math.hypot(dx, dy)

    def distance_to_point(self, point: Point) -> float:
        dx = max(self.left - point.x, point.x - self.right, 0.0)
        dy = max(self.top - point.y, point.y - self.bottom, 0.0)
        return math.hypot(dx, dy)

    def horizontal_gap(self, other: "BoundingBox") -> float:
        return max(other.left - self.right, self.left - other.right, 0.0)

    def vertical_gap(self, other: "BoundingBox") -> float:
        return max(other.top - self.bottom, self.top - other.bottom, 0.0)

    def to_dict(self) -> dict[str, float]:
        return {
            "left": round(self.left, 2),
            "top": round(self.top, 2),
            "right": round(self.right, 2),
            "bottom": round(self.bottom, 2),
        }


@dataclass(frozen=True, slots=True)
class OCRSpan:
    span_id: str
    text: str
    confidence: float
    bbox: BoundingBox
    polygon: tuple[Point, Point, Point, Point]

    @property
    def center(self) -> Point:
        return self.bbox.center

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "text": self.text,
            "confidence": round(self.confidence, 4),
            "bbox": self.bbox.to_dict(),
            "center": self.center.to_dict(),
            "polygon": [point.to_dict() for point in self.polygon],
        }


@dataclass(frozen=True, slots=True)
class CandidateNode:
    node_id: str
    label: str
    bbox: BoundingBox
    text_span_ids: tuple[str, ...]
    texts: tuple[str, ...]
    type_hint: str

    @property
    def center(self) -> Point:
        return self.bbox.center

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "label": self.label,
            "bbox": self.bbox.to_dict(),
            "center": self.center.to_dict(),
            "text_span_ids": list(self.text_span_ids),
            "texts": list(self.texts),
            "type_hint": self.type_hint,
        }


@dataclass(frozen=True, slots=True)
class LineSegment:
    start: Point
    end: Point

    @property
    def midpoint(self) -> Point:
        return Point(x=(self.start.x + self.end.x) / 2.0, y=(self.start.y + self.end.y) / 2.0)

    @property
    def length(self) -> float:
        return math.hypot(self.end.x - self.start.x, self.end.y - self.start.y)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "midpoint": self.midpoint.to_dict(),
            "length": round(self.length, 2),
        }


@dataclass(frozen=True, slots=True)
class ConnectionCandidate:
    from_node_id: str
    to_node_id: str
    line: LineSegment
    confidence: float
    label_hints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_node_id": self.from_node_id,
            "to_node_id": self.to_node_id,
            "line": self.line.to_dict(),
            "confidence": round(self.confidence, 4),
            "label_hints": list(self.label_hints),
        }


@dataclass(slots=True)
class StructuredDiagram:
    image_path: Path
    ocr_spans: list[OCRSpan]
    candidate_nodes: list[CandidateNode]
    candidate_connections: list[ConnectionCandidate]
    image_size: tuple[int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_path": str(self.image_path),
            "image_size": (
                {"width": self.image_size[0], "height": self.image_size[1]}
                if self.image_size
                else None
            ),
            "ocr_spans": [span.to_dict() for span in self.ocr_spans],
            "candidate_nodes": [node.to_dict() for node in self.candidate_nodes],
            "candidate_connections": [edge.to_dict() for edge in self.candidate_connections],
        }


@dataclass(frozen=True, slots=True)
class TopologyNode:
    node_id: str
    label: str
    node_type: str
    ip: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "label": self.label,
            "type": self.node_type,
            "ip": self.ip,
        }


@dataclass(frozen=True, slots=True)
class TopologyEdge:
    from_node_id: str
    to_node_id: str
    protocol: str | None
    port: str | None
    directional: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_node_id,
            "to": self.to_node_id,
            "protocol": self.protocol,
            "port": self.port,
            "directional": self.directional,
        }


@dataclass(slots=True)
class TopologyGraph:
    nodes: list[TopologyNode] = field(default_factory=list)
    edges: list[TopologyEdge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }


def extract_ip(text: str) -> str | None:
    match = IP_ADDRESS_PATTERN.search(text)
    return match.group(0) if match else None
