"""Stage 2: turn OCR spans into candidate nodes."""

from __future__ import annotations

from collections import defaultdict
from itertools import count

from diagram_parser.config import GroupingConfig
from diagram_parser.models import BoundingBox, CandidateNode, OCRSpan, slugify


TYPE_HINTS = {
    "database": ("database", "db", "postgres", "mysql", "redis", "mongodb", "rds"),
    "application": ("application", "app", "api", "service", "frontend", "backend"),
    "server": ("server", "host", "vm", "node", "ec2", "instance", "bastion"),
    "network": ("internet", "gateway", "router", "switch", "network", "firewall", "lb", "load balancer", "vpc", "subnet"),
    "zone": ("zone", "segment", "dmz", "public", "private"),
}


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


def _is_merge_candidate(left: OCRSpan, right: OCRSpan, config: GroupingConfig) -> bool:
    bbox_a = left.bbox
    bbox_b = right.bbox

    same_line = (
        abs(left.center.y - right.center.y) <= config.same_line_tolerance
        and bbox_a.horizontal_gap(bbox_b) <= config.horizontal_gap_threshold
    )
    stacked = (
        abs(left.center.x - right.center.x) <= config.alignment_tolerance
        and bbox_a.vertical_gap(bbox_b) <= config.vertical_gap_threshold
    )
    nearby = bbox_a.distance_to_box(bbox_b) <= config.max_merge_distance

    return same_line or stacked or nearby


def _infer_type_hint(texts: tuple[str, ...]) -> str:
    combined = " ".join(texts).lower()
    for type_hint, keywords in TYPE_HINTS.items():
        if any(keyword in combined for keyword in keywords):
            return type_hint
    return "unknown"


def group_text_into_nodes(spans: list[OCRSpan], config: GroupingConfig) -> list[CandidateNode]:
    """Group OCR spans that likely belong to the same diagram node."""

    if not spans:
        return []

    sorted_spans = sorted(spans, key=lambda span: (span.bbox.top, span.bbox.left))
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
        bbox = ordered_group[0].bbox
        for span in ordered_group[1:]:
            bbox = bbox.union(span.bbox)

        base_id = slugify(label)
        node_id = f"{base_id}-{next(id_counter)}"
        texts = tuple(span.text for span in ordered_group)
        nodes.append(
            CandidateNode(
                node_id=node_id,
                label=label,
                bbox=bbox,
                text_span_ids=tuple(span.span_id for span in ordered_group),
                texts=texts,
                type_hint=_infer_type_hint(texts),
            )
        )

    return sorted(nodes, key=lambda node: (node.bbox.top, node.bbox.left))
