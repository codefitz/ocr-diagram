"""Stage 2: turn OCR spans into candidate nodes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import count
import re

from diagram_parser.config import GroupingConfig
from diagram_parser.models import CandidateNode, OCRSpan, slugify


EDGE_LABEL_PATTERN = re.compile(
    r"^(?:(?:tcp|udp|http|https|ssh|icmp|cp|rcp)[-\s]?\d+|portmapper\s*\d+)$",
    re.IGNORECASE,
)
ROLE_PREFIX_PATTERN = re.compile(r"^\s*(?P<role>web|app|application|db|database)\s*:", re.IGNORECASE)
HOSTNAME_TOKEN_PATTERN = re.compile(r"\b[A-Z][A-Z0-9-]{4,}\b")
DATABASE_TECH_PATTERN = re.compile(r"\b(?:postgres|postgresql|mysql|mariadb|redis|mongodb|oracle|sql\s*server|rds)\b", re.IGNORECASE)
ZONE_LABEL_PATTERN = re.compile(
    r"\b(?:different network|network at|security zone|zone|segment|dmz)\b",
    re.IGNORECASE,
)
CLASSIFICATION_KEYWORDS = (
    ("firewall", ("firewall", "fw", "waf")),
    ("router_switch", ("gateway", "router", "switch", "network", "lb", "load balancer")),
    ("host", ("server", "host", "vm", "node", "ec2", "instance", "bastion")),
    ("software", ("software", "application", "app", "api", "service", "frontend", "backend")),
    ("network", ("internet", "vpc", "subnet")),
)


@dataclass(frozen=True, slots=True)
class NodeTypeDecision:
    node_type: str
    reason: str


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


def _has_hostname_token(label: str) -> bool:
    return any(
        any(char.isdigit() for char in match.group(0))
        for match in HOSTNAME_TOKEN_PATTERN.finditer(label.upper())
    )


def _rationalize_node_type(texts: tuple[str, ...]) -> NodeTypeDecision:
    combined = " ".join(texts).lower()
    label = " ".join(texts)
    if any(is_zone_label_text(text) for text in texts):
        return NodeTypeDecision("zone", "zone/segment wording")

    role_match = ROLE_PREFIX_PATTERN.match(label)
    has_hostname = _has_hostname_token(label)
    if role_match and has_hostname:
        role = role_match.group("role").lower()
        return NodeTypeDecision("host", f"{role} role label contains hostname-like token")

    if DATABASE_TECH_PATTERN.search(label):
        return NodeTypeDecision("database", "database technology keyword")

    if re.search(r"\b(?:db|database|datastore)\b", label, re.IGNORECASE):
        return NodeTypeDecision("host", "database wording defaults to server/host")

    for type_hint, keywords in CLASSIFICATION_KEYWORDS:
        if any(keyword in combined for keyword in keywords):
            return NodeTypeDecision(type_hint, f"keyword matched {type_hint}")

    if has_hostname:
        return NodeTypeDecision("host", "hostname-like token")

    return NodeTypeDecision("unknown", "no strong type signal")


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
        type_decision = _rationalize_node_type(texts)
        nodes.append(
            CandidateNode(
                page_id=page_id,
                node_id=node_id,
                label=label,
                bbox=bbox,
                text_span_ids=tuple(span.span_id for span in ordered_group),
                texts=texts,
                type_hint=type_decision.node_type,
                type_reason=type_decision.reason,
            )
        )

    return sorted(nodes, key=lambda node: (node.bbox.top, node.bbox.left))
