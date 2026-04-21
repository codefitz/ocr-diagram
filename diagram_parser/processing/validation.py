"""Stage 5: deterministic schema validation and cleanup."""

from __future__ import annotations

import re
from typing import Any

from diagram_parser.models import (
    SUPPORTED_NODE_TYPES,
    StructuredDiagram,
    TopologyEdge,
    TopologyGraph,
    TopologyNode,
    extract_ip,
    slugify,
)

PORT_LABEL_PATTERN = re.compile(r"^(?P<proto>[A-Za-z]+)[-\s]?(?P<port>\d+)$")
PORT_VALUE_PATTERN = re.compile(r"\d+")


def _normalize_node_id(raw_id: str | None, label: str, seen_ids: set[str]) -> str:
    base_id = slugify(raw_id or label or "node")
    candidate_id = base_id
    suffix = 2
    while candidate_id in seen_ids:
        candidate_id = f"{base_id}-{suffix}"
        suffix += 1
    seen_ids.add(candidate_id)
    return candidate_id


def _normalize_node_type(raw_type: Any) -> str:
    if isinstance(raw_type, str) and raw_type.lower() in SUPPORTED_NODE_TYPES:
        return raw_type.lower()
    return "unknown"


def _as_nullable_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _normalize_protocol(raw_value: Any) -> str | None:
    protocol = _as_nullable_string(raw_value)
    if protocol is None:
        return None
    normalized = re.sub(r"[^A-Za-z]+", "", protocol).upper()
    if normalized in {"CP", "RCP"}:
        normalized = "TCP"
    return normalized or None


def _normalize_port(raw_value: Any) -> str | None:
    raw_port = _as_nullable_string(raw_value)
    if raw_port is None:
        return None
    matches = PORT_VALUE_PATTERN.findall(raw_port)
    if not matches:
        return None
    unique_ports = list(dict.fromkeys(matches))
    return ",".join(unique_ports)


def _protocol_port_from_hints(label_hints: tuple[str, ...]) -> tuple[str | None, str | None]:
    parsed_labels: list[tuple[str, str]] = []
    for hint in label_hints:
        match = PORT_LABEL_PATTERN.match(hint.strip())
        if not match:
            continue
        protocol = match.group("proto").upper()
        if protocol in {"CP", "RCP"}:
            protocol = "TCP"
        port = match.group("port")
        parsed_labels.append((protocol, port))

    if not parsed_labels:
        return None, None

    primary_protocol = parsed_labels[0][0]
    ports = [port for protocol, port in parsed_labels if protocol == primary_protocol]
    unique_ports = list(dict.fromkeys(ports))
    return primary_protocol, ",".join(unique_ports)


def validate_topology(raw_payload: dict[str, Any], structured_diagram: StructuredDiagram) -> TopologyGraph:
    """Validate the LLM payload and drop unsupported or unverifiable fields."""

    graph = TopologyGraph()
    seen_ids: set[str] = set()

    for raw_node in raw_payload.get("nodes", []):
        if not isinstance(raw_node, dict):
            continue
        label = _as_nullable_string(raw_node.get("label"))
        if not label:
            continue
        normalized_id = _normalize_node_id(
            raw_id=_as_nullable_string(raw_node.get("id")),
            label=label,
            seen_ids=seen_ids,
        )
        node_type = _normalize_node_type(raw_node.get("type"))
        ip = _as_nullable_string(raw_node.get("ip"))
        if ip is None:
            ip = extract_ip(label)

        graph.nodes.append(
            TopologyNode(
                node_id=normalized_id,
                label=label,
                node_type=node_type,
                ip=ip,
            )
        )

    if not graph.nodes:
        for candidate in structured_diagram.candidate_nodes:
            graph.nodes.append(
                TopologyNode(
                    node_id=candidate.node_id,
                    label=candidate.label,
                    node_type=candidate.type_hint if candidate.type_hint in SUPPORTED_NODE_TYPES else "unknown",
                    ip=extract_ip(candidate.label),
                )
            )

    node_lookup = {node.node_id: node for node in graph.nodes}
    label_to_id = {node.label.lower(): node.node_id for node in graph.nodes}

    def resolve_node_id(raw_value: Any) -> str | None:
        candidate = _as_nullable_string(raw_value)
        if candidate is None:
            return None
        if candidate in node_lookup:
            return candidate
        return label_to_id.get(candidate.lower())

    seen_edges: set[tuple[str, str, str | None, str | None, bool]] = set()

    for raw_edge in raw_payload.get("edges", []):
        if not isinstance(raw_edge, dict):
            continue
        from_node_id = resolve_node_id(raw_edge.get("from"))
        to_node_id = resolve_node_id(raw_edge.get("to"))
        if from_node_id is None or to_node_id is None or from_node_id == to_node_id:
            continue

        protocol = _normalize_protocol(raw_edge.get("protocol"))
        port = _normalize_port(raw_edge.get("port"))
        directional = bool(raw_edge.get("directional"))
        edge_key = (from_node_id, to_node_id, protocol, port, directional)
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        graph.edges.append(
            TopologyEdge(
                from_node_id=from_node_id,
                to_node_id=to_node_id,
                protocol=protocol,
                port=port,
                directional=directional,
            )
        )

    if not graph.edges:
        for candidate_edge in structured_diagram.candidate_connections:
            from_node_id = candidate_edge.from_node_id
            to_node_id = candidate_edge.to_node_id
            if from_node_id not in node_lookup or to_node_id not in node_lookup:
                continue
            protocol, port = _protocol_port_from_hints(candidate_edge.label_hints)
            edge_key = (from_node_id, to_node_id, protocol, port, True)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            graph.edges.append(
                TopologyEdge(
                    from_node_id=from_node_id,
                    to_node_id=to_node_id,
                    protocol=protocol,
                    port=port,
                    directional=True,
                )
            )

    return graph
