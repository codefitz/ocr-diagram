"""Stage 6: persist JSON and Mermaid output."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

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
    """Map the local infrastructure type to a uControl definition/kind."""

    if node.node_type == "host":
        return "Host"
    if node.node_type == "router_switch":
        return "NetworkDevice"
    if node.node_type == "firewall":
        return "Firewall"
    if node.node_type == "software":
        return "SoftwareInstance"
    if node.node_type == "database":
        return "Database"
    if node.node_type == "server":
        return "Host"
    if node.node_type == "application":
        return "SoftwareInstance"
    if node.node_type == "network":
        return "NetworkDevice"
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
        "description": node.description or node.label,
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


def build_ucontrol_model_create_body(
    topology: TopologyGraph,
    application_name: str,
) -> dict[str, object]:
    """Build the JSON body for uControl model creation."""

    assets_by_node_id = {
        node.node_id: _build_ucontrol_asset(node)
        for node in topology.nodes
        if node.label.strip()
    }
    relationships: list[dict[str, object]] = []

    for edge in topology.edges:
        from_asset = assets_by_node_id.get(edge.from_node_id)
        to_asset = assets_by_node_id.get(edge.to_node_id)
        if from_asset is None or to_asset is None:
            continue
        relationship_label = "connects_to"
        if edge.protocol or edge.port:
            relationship_label = "communicates_with"
        relationships.append(
            {
                "from": from_asset["name"],
                "from_definition": from_asset["kind"],
                "to": to_asset["name"],
                "to_definition": to_asset["kind"],
                "relationship": relationship_label,
                "protocol": edge.protocol,
                "port": edge.port,
                "directional": edge.directional,
            }
        )

    return {
        "name": application_name,
        "description": application_name,
        "appID": application_name,
        "modellingType": "Standard",
        "applicationType": "Application Service",
        "teamID": 7,
        "reviewSettingID": 1,
        "linkToServiceID": 23,
        "nodes": [
            {
                "name": asset["name"],
                "type": asset["kind"],
                "definition": asset["kind"],
                "record_identifier": asset["record_identifier"],
                "description": asset["description"],
            }
            for asset in assets_by_node_id.values()
        ],
        "relationships": relationships,
    }


def build_ucontrol_retrieval_requests(
    topology: TopologyGraph,
    application_name: str,
) -> dict[str, object]:
    """Build request descriptors for retrieving detected assets by name."""

    requests: list[dict[str, object]] = []
    for node in topology.nodes:
        name = node.label.strip()
        if not name:
            continue
        definition = _bmc_kind_for_node(node)
        record_identifier = f"name={name}"
        encoded_record_identifier = f"name={quote(name, safe='')}"
        requests.append(
            {
                "node_name": name,
                "node_type": node.node_type,
                "definition": definition,
                "record_identifier": record_identifier,
                "method": "GET",
                "endpoint": f"/api/asset/data/{quote(definition, safe='')}/{encoded_record_identifier}",
            }
        )

    return {
        "application_name": application_name,
        "requests": requests,
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
    mermaid_svg_path = output_dir / "topology.svg"

    topology_json_path.write_text(
        json.dumps(topology.to_dict(), indent=config.json_indent) + "\n",
        encoding="utf-8",
    )
    mermaid_path.write_text(mermaid, encoding="utf-8")

    output_paths = {
        "json": topology_json_path,
        "mermaid": mermaid_path,
    }

    rendered_mermaid_path = render_mermaid_diagram(
        mermaid_path=mermaid_path,
        output_path=mermaid_svg_path,
    )
    if rendered_mermaid_path is not None:
        output_paths["mermaid_svg"] = rendered_mermaid_path

    if config.save_ucontrol_asset_tags:
        ucontrol_model_create_path = output_dir / "ucontrol_model_create.json"
        ucontrol_model_create_path.write_text(
            json.dumps(
                build_ucontrol_model_create_body(topology, config.application_name),
                indent=config.json_indent,
            ) + "\n",
            encoding="utf-8",
        )
        output_paths["ucontrol_model_create"] = ucontrol_model_create_path

        ucontrol_retrieval_path = output_dir / "ucontrol_retrieval_requests.json"
        ucontrol_retrieval_path.write_text(
            json.dumps(
                build_ucontrol_retrieval_requests(topology, config.application_name),
                indent=config.json_indent,
            ) + "\n",
            encoding="utf-8",
        )
        output_paths["ucontrol_retrieval_requests"] = ucontrol_retrieval_path

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


def render_mermaid_diagram(mermaid_path: Path, output_path: Path) -> Path | None:
    """Render Mermaid source to SVG when Mermaid CLI is available."""

    mmdc = shutil.which("mmdc")
    if mmdc is None:
        return None

    try:
        subprocess.run(
            [
                mmdc,
                "--input",
                str(mermaid_path),
                "--output",
                str(output_path),
                "--quiet",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    return output_path if output_path.exists() else None


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
