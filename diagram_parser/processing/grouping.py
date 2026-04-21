"""Stage 2: turn OCR spans into candidate nodes."""

from __future__ import annotations

from collections import defaultdict
from itertools import count
import re

from diagram_parser.config import GroupingConfig
from diagram_parser.models import CandidateNode, OCRSpan, slugify


TYPE_HINTS = {
    "database": ("database", "db", "postgres", "mysql", "redis", "mongodb", "rds"),
    "application": ("application", "app", "api", "service", "frontend", "backend"),
    "server": ("server", "host", "vm", "node", "ec2", "instance", "bastion"),
    "network": ("internet", "gateway", "router", "switch", "network", "firewall", "lb", "load balancer", "vpc", "subnet"),
    "zone": ("zone", "segment", "dmz", "public", "private"),
}

EDGE_LABEL_PATTERN = re.compile(
    r"^(?:(?:tcp|udp|http|https|ssh|icmp|cp|rcp)[-\s]?\d+|portmapper\s*\d+)$",
    re.IGNORECASE,
)
ZONE_LABEL_PATTERN = re.compile(
    r"\b(?:different network|network at|security zone|zone|segment|dmz)\b",
    re.IGNORECASE,
)


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def is_edge_label_text(text: str) -> bool:
    """Return true for protocol/port annotations that belong on links, not nodes."""

    return bool(EDGE_LABEL_PATTERN.fullmatch(text.strip()))


def is_zone_label_text(text: str) -> bool:
    return bool(ZONE_LABEL_PATTERN.search(text.strip()))


def _horizontal_overlap_ratio(left: OCRSpan, right: OCRSpan) -> float:
    overlap = max(0.0, min(left.bbox.right, right.bbox.right) - max(left.bbox.left, right.bbox.left))
    smallest_width = max(1.0, min(left.bbox.width, right.bbox.width))
    return overlap / smallest_width


def _is_merge_candidate(left: OCRSpan, right: OCRSpan, config: GroupingConfig) -> bool:
    bbox_a = left.bbox
    bbox_b = right.bbox

    if is_edge_label_text(left.text) or is_edge_label_text(right.text):
        return False

    left_is_zone = is_zone_label_text(left.text)
    right_is_zone = is_zone_label_text(right.text)
    if left_is_zone != right_is_zone:
        return False

    same_line = (
        abs(left.center.y - right.center.y) <= config.same_line_tolerance
        and bbox_a.horizontal_gap(bbox_b) <= config.horizontal_gap_threshold
        and min(bbox_a.height, bbox_b.height) >= 10.0
    )
    stacked = (
        abs(left.center.x - right.center.x) <= config.alignment_tolerance
        and bbox_a.vertical_gap(bbox_b) <= config.vertical_gap_threshold
        and _horizontal_overlap_ratio(left, right) >= 0.25
    )
    nearby = bbox_a.distance_to_box(bbox_b) <= config.max_merge_distance

    # Avoid broad transitive merging of unrelated nearby captions.
    return same_line or stacked or (nearby and _horizontal_overlap_ratio(left, right) >= 0.6)


def _infer_type_hint(texts: tuple[str, ...]) -> str:
    combined = " ".join(texts).lower()
    if any(is_zone_label_text(text) for text in texts):
        return "zone"
    for type_hint, keywords in TYPE_HINTS.items():
        if any(keyword in combined for keyword in keywords):
            return type_hint
    return "unknown"


def group_text_into_nodes(spans: list[OCRSpan], config: GroupingConfig) -> list[CandidateNode]:
    """Group OCR spans that likely belong to the same diagram node."""

    node_candidate_spans = [span for span in spans if not is_edge_label_text(span.text)]
    if not node_candidate_spans:
        return []

    sorted_spans = sorted(node_candidate_spans, key=lambda span: (span.bbox.top, span.bbox.left))
    union_find = UnionFind(len(sorted_spans))

    for left_index, left in enumerate(sorted_spans):
        for right_index in range(left_index + 1, len(sorted_spans)):
            right = sorted_spans[right_index]
            if _is_merge_candidate(left, right, config):
                union_find.union(left_index, right_index)

    grouped_indices: dict[int, list[OCRSpan]] = defaultdict(list)
    for index, span in enumerate(sorted_spans):
        grouped_indices[union_find.find(index)].append(span)

    nodes: list[CandidateNode] = []
    id_counter = count(1)

    for group in grouped_indices.values():
        ordered_group = sorted(group, key=lambda span: (span.bbox.top, span.bbox.left))
        label_parts = [span.text for span in ordered_group]
        label = " ".join(label_parts)
        page_id = ordered_group[0].page_id
        bbox = ordered_group[0].bbox
        for span in ordered_group[1:]:
            bbox = bbox.union(span.bbox)

        base_id = slugify(label)
        node_id = f"{page_id}-{base_id}-{next(id_counter)}"
        texts = tuple(span.text for span in ordered_group)
        nodes.append(
            CandidateNode(
                page_id=page_id,
                node_id=node_id,
                label=label,
                bbox=bbox,
                text_span_ids=tuple(span.span_id for span in ordered_group),
                texts=texts,
                type_hint=_infer_type_hint(texts),
            )
        )

    return sorted(nodes, key=lambda node: (node.bbox.top, node.bbox.left))
