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
HOSTNAME_TOKEN_PATTERN = re.compile(r"\b[A-Z][A-Z0-9-]{4,}\b")
HOSTNAME_EXCLUDE_PATTERN = re.compile(
    r"^(?:WIN\d+|R\d+|V\d+|X\d+|RAM|GHZ|CPU|STANDARD|EDITION|SERVICE|PACK|BACKUP)$",
    re.IGNORECASE,
)
NODE_TYPE_ALIASES = {
    "server": "host",
    "vm": "host",
    "instance": "host",
    "application": "software",
    "app": "software",
    "service": "software",
    "network": "router_switch",
    "router": "router_switch",
    "switch": "router_switch",
    "load_balancer": "router_switch",
    "load balancer": "router_switch",
    "waf": "firewall",
    "fw": "firewall",
    "db": "database",
}
ROLE_ONLY_HOST_LABEL_PATTERN = re.compile(
    r"^(?:vm|server|servers|web servers?|data store server|backup|"
    r"ram|cpu|memory|storage|sghz|ghz|[0-9.]+\s*ghz)(?:\s+|$)",
    re.IGNORECASE,
)
NETWORK_DEVICE_NAME_PATTERN = re.compile(
    r"\b([A-Z0-9][A-Z0-9-]*(?:\s+(?:F5|BIG-IP|Cisco|Juniper|Palo Alto|Fortinet))?\s+"
    r"(?:Switch|Router|Gateway|Firewall|WAF|Load Balancer))\b",
    re.IGNORECASE,
)


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
    if isinstance(raw_type, str):
        normalized = raw_type.strip().lower().replace("-", "_")
        normalized = NODE_TYPE_ALIASES.get(normalized, normalized)
        if normalized in SUPPORTED_NODE_TYPES:
            return normalized
    return "unknown"


def _refine_node_type_from_label(label: str, node_type: str) -> str:
    lowered = label.lower()
    if "firewall" in lowered or re.search(r"\bwaf\b|\bfw\b", lowered):
        return "firewall"
    if any(keyword in lowered for keyword in ("switch", "router", "load balancer", "gateway")):
        return "router_switch"
    if any(keyword in lowered for keyword in ("database", " db", "postgres", "mysql", "redis", "mongodb", "rds")):
        return "database"
    return node_type


def _extract_hostname_tokens(label: str) -> list[str]:
    hostnames: list[str] = []
    for match in HOSTNAME_TOKEN_PATTERN.finditer(label.upper()):
        token = match.group(0).strip("-")
        if HOSTNAME_EXCLUDE_PATTERN.match(token):
            continue
        if any(part in token for part in ("GHZ", "RAM", "CPU")):
            continue
        if not any(char.isdigit() for char in token):
            continue
        if token not in hostnames:
            hostnames.append(token)
    return hostnames


def _is_role_or_spec_only_host_label(label: str) -> bool:
    tokens = re.sub(r"[^A-Za-z0-9. ]+", " ", label).strip()
    if not tokens:
        return True
    if _extract_hostname_tokens(label):
        return False
    return bool(ROLE_ONLY_HOST_LABEL_PATTERN.search(tokens))


def _extract_network_device_name(label: str, node_type: str) -> str | None:
    if node_type not in {"router_switch", "firewall"}:
        return None
    match = NETWORK_DEVICE_NAME_PATTERN.search(label)
    if not match:
        return None
    return " ".join(match.group(1).split())


def _normalized_nodes_for_asset(
    *,
    raw_id: str | None,
    label: str,
    node_type: str,
    ip: str | None,
    seen_ids: set[str],
) -> list[TopologyNode]:
    node_type = _refine_node_type_from_label(label, node_type)

    if node_type == "unknown" and _extract_hostname_tokens(label):
        node_type = "host"

    if node_type == "host":
        hostnames = _extract_hostname_tokens(label)
        if not hostnames:
            if _is_role_or_spec_only_host_label(label):
                return []
            normalized_id = _normalize_node_id(raw_id, label, seen_ids)
            return [
                TopologyNode(
                    node_id=normalized_id,
                    label=label,
                    node_type=node_type,
                    ip=ip,
                )
            ]
        return [
            TopologyNode(
                node_id=_normalize_node_id(
                    raw_id=f"{raw_id or label}-{hostname}",
                    label=hostname,
                    seen_ids=seen_ids,
                ),
                label=hostname,
                node_type="host",
                ip=ip,
                description=label if label != hostname else None,
            )
            for hostname in hostnames
        ]

    device_name = _extract_network_device_name(label, node_type)
    if device_name is not None and device_name != label:
        normalized_id = _normalize_node_id(raw_id, device_name, seen_ids)
        return [
            TopologyNode(
                node_id=normalized_id,
                label=device_name,
                node_type=node_type,
                ip=ip,
                description=label,
            )
        ]

    if node_type == "unknown":
        return []

    normalized_id = _normalize_node_id(raw_id, label, seen_ids)
    return [
        TopologyNode(
            node_id=normalized_id,
            label=label,
            node_type=node_type,
            ip=ip,
        )
    ]


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
    raw_node_aliases: dict[str, list[str]] = {}

    for raw_node in raw_payload.get("nodes", []):
        if not isinstance(raw_node, dict):
            continue
        label = _as_nullable_string(raw_node.get("label"))
        if not label:
            continue
        raw_id = _as_nullable_string(raw_node.get("id"))
        node_type = _normalize_node_type(raw_node.get("type"))
        ip = _as_nullable_string(raw_node.get("ip"))
        if ip is None:
            ip = extract_ip(label)

        nodes = _normalized_nodes_for_asset(
            raw_id=raw_id,
            label=label,
            node_type=node_type,
            ip=ip,
            seen_ids=seen_ids,
        )
        if not nodes:
            continue
        graph.nodes.extend(nodes)
        alias_ids = [node.node_id for node in nodes]
        if raw_id:
            raw_node_aliases[raw_id] = alias_ids
        raw_node_aliases[label.lower()] = alias_ids

    if not graph.nodes:
        for candidate in structured_diagram.candidate_nodes:
            nodes = _normalized_nodes_for_asset(
                raw_id=candidate.node_id,
                label=candidate.label,
                node_type=candidate.type_hint if candidate.type_hint in SUPPORTED_NODE_TYPES else "unknown",
                ip=extract_ip(candidate.label),
                seen_ids=seen_ids,
            )
            if not nodes:
                continue
            graph.nodes.extend(nodes)
            raw_node_aliases[candidate.node_id] = [node.node_id for node in nodes]
            raw_node_aliases[candidate.label.lower()] = [node.node_id for node in nodes]

    node_lookup = {node.node_id: node for node in graph.nodes}
    for node in graph.nodes:
        raw_node_aliases.setdefault(node.label.lower(), [node.node_id])

    def resolve_node_ids(raw_value: Any) -> list[str]:
        candidate = _as_nullable_string(raw_value)
        if candidate is None:
            return []
        if candidate in node_lookup:
            return [candidate]
        return raw_node_aliases.get(candidate, raw_node_aliases.get(candidate.lower(), []))

    seen_edges: set[tuple[str, str, str | None, str | None, bool]] = set()

    for raw_edge in raw_payload.get("edges", []):
        if not isinstance(raw_edge, dict):
            continue
        from_node_ids = resolve_node_ids(raw_edge.get("from"))
        to_node_ids = resolve_node_ids(raw_edge.get("to"))
        if not from_node_ids or not to_node_ids:
            continue

        protocol = _normalize_protocol(raw_edge.get("protocol"))
        port = _normalize_port(raw_edge.get("port"))
        directional = bool(raw_edge.get("directional"))
        for from_node_id in from_node_ids:
            for to_node_id in to_node_ids:
                if from_node_id == to_node_id:
                    continue
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
            from_node_ids = resolve_node_ids(candidate_edge.from_node_id)
            to_node_ids = resolve_node_ids(candidate_edge.to_node_id)
            if not from_node_ids or not to_node_ids:
                continue
            protocol, port = _protocol_port_from_hints(candidate_edge.label_hints)
            for from_node_id in from_node_ids:
                for to_node_id in to_node_ids:
                    if from_node_id == to_node_id:
                        continue
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
