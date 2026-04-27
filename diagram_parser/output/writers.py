"""Stage 6: persist JSON and Mermaid output."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from diagram_parser.config import OutputConfig
from diagram_parser.models import StructuredDiagram, TopologyGraph, TopologyNode


def _escape_mermaid_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_mermaid(topology: TopologyGraph) -> str:
    """Convert the validated topology into a Mermaid graph."""

    lines = ["graph TD"]
    mermaid_ids = {
        node.node_id: f"n{index}"
        for index, node in enumerate(topology.nodes, start=1)
    }

    for node in topology.nodes:
        label = _escape_mermaid_label(node.label)
        lines.append(
            f'    {mermaid_ids[node.node_id]}["{label}\\n({node.node_type})"]'
        )

    for edge in topology.edges:
        connector = "-->" if edge.directional else "---"
        label_parts = [part for part in [edge.protocol, edge.port] if part]
        from_id = mermaid_ids.get(edge.from_node_id)
        to_id = mermaid_ids.get(edge.to_node_id)
        if from_id is None or to_id is None:
            continue
        if label_parts:
            edge_label = _escape_mermaid_label(" ".join(label_parts))
            lines.append(
                f'    {from_id} {connector}|"{edge_label}"| {to_id}'
            )
        else:
            lines.append(f"    {from_id} {connector} {to_id}")

    return "\n".join(lines) + "\n"


def _bmc_kind_for_node(node: TopologyNode) -> str:
    """Map the local coarse node type to a BMC Discovery-style node kind."""

    if node.node_type == "application":
        return "BusinessApplicationInstance"
    if node.node_type == "database":
        return "Database"
    if node.node_type == "server":
        return "SoftwareInstance"
    if node.node_type == "network":
        return "ExternalElement"
    if node.node_type == "zone":
        return "GenericElement"
    return "GenericElement"


def _stable_guid(*parts: str) -> str:
    raw_guid = "|".join(part.strip() for part in parts if part.strip())
    return base64.b64encode(raw_guid.encode("utf-8")).decode("ascii")


def _build_ucontrol_asset(node: TopologyNode) -> dict[str, object]:
    kind = _bmc_kind_for_node(node)
    guid = _stable_guid(node.node_id, node.label, kind)
    return {
        "record_identifier": None,
        "name": node.label,
        "short_name": None,
        "type": None,
        "product_version": None,
        "instance": None,
        "uControlID": None,
        "environment": None,
        "version": None,
        "description": node.label,
        "application_id": None,
        "guid": guid,
        "datasource_name": None,
        "datasource_key": None,
        "datasource": "UCONTROL",
        "atrium_key": None,
        "bmc_key": None,
        "servicenow_key": None,
        "kind": kind,
        "deleted_date": "",
        "deleted_status": False,
        "merge_status": False,
    }


def build_ucontrol_asset_tags(topology: TopologyGraph) -> dict[str, object]:
    """Convert topology into uControl asset/tag create body-shaped records."""

    assets_by_node_id = {
        node.node_id: _build_ucontrol_asset(node)
        for node in topology.nodes
    }
    relationships: list[dict[str, object]] = []

    for edge in topology.edges:
        from_asset = assets_by_node_id.get(edge.from_node_id)
        to_asset = assets_by_node_id.get(edge.to_node_id)
        if from_asset is None or to_asset is None:
            continue

        from_guid = str(from_asset["guid"])
        to_guid = str(to_asset["guid"])
        relationships.append(
            {
                "from_record_identifier": None,
                "from_guid": from_guid,
                "from_name": from_asset["name"],
                "from_kind": from_asset["kind"],
                "to_record_identifier": None,
                "to_guid": to_guid,
                "to_name": to_asset["name"],
                "to_kind": to_asset["kind"],
                "protocol": edge.protocol,
                "port": edge.port,
                "directional": edge.directional,
                "directional_pk_fk_identity": {
                    "primary_key": f"guid:{from_guid}",
                    "foreign_key": f"guid:{to_guid}",
                    "direction": "from->to" if edge.directional else "undirected",
                },
            }
        )

    return {
        "assets": list(assets_by_node_id.values()),
        "relationships": relationships,
    }


def save_outputs(
    output_dir: Path,
    topology: TopologyGraph,
    mermaid: str,
    config: OutputConfig,
    structured_diagram: StructuredDiagram | None = None,
    llm_artifacts: dict[str, object] | None = None,
) -> dict[str, Path]:
    """Save JSON, Mermaid, and optionally the structured intermediate payload."""

    output_dir.mkdir(parents=True, exist_ok=True)

    topology_json_path = output_dir / "topology.json"
    mermaid_path = output_dir / "topology.mmd"

    topology_json_path.write_text(
        json.dumps(topology.to_dict(), indent=config.json_indent) + "\n",
        encoding="utf-8",
    )
    mermaid_path.write_text(mermaid, encoding="utf-8")

    output_paths = {
        "json": topology_json_path,
        "mermaid": mermaid_path,
    }

    if config.save_ucontrol_asset_tags:
        ucontrol_asset_tags_path = output_dir / "ucontrol_asset_tags.json"
        ucontrol_asset_tags_path.write_text(
            json.dumps(build_ucontrol_asset_tags(topology), indent=config.json_indent) + "\n",
            encoding="utf-8",
        )
        output_paths["ucontrol_asset_tags"] = ucontrol_asset_tags_path

    if config.save_intermediate and structured_diagram is not None:
        intermediate_path = output_dir / "structured_candidates.json"
        intermediate_path.write_text(
            json.dumps(structured_diagram.to_dict(), indent=config.json_indent) + "\n",
            encoding="utf-8",
        )
        output_paths["intermediate"] = intermediate_path

    if config.save_llm_debug and llm_artifacts is not None:
        llm_debug_path = save_llm_debug(output_dir, llm_artifacts, config)
        output_paths["llm_debug"] = llm_debug_path

    return output_paths


def save_llm_debug(
    output_dir: Path,
    llm_artifacts: dict[str, object],
    config: OutputConfig,
) -> Path:
    """Persist raw LLM request/response artifacts even for failed runs."""

    output_dir.mkdir(parents=True, exist_ok=True)
    llm_debug_path = output_dir / "llm_debug.json"
    llm_debug_path.write_text(
        json.dumps(llm_artifacts, indent=config.json_indent) + "\n",
        encoding="utf-8",
    )
    return llm_debug_path
