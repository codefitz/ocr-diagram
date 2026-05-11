"""Stage 6: persist JSON and Mermaid output."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

from diagram_parser.config import OutputConfig
from diagram_parser.models import StructuredDiagram, TopologyGraph, TopologyNode


MONTH_PATTERN = re.compile(
    r"\b(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|"
    r"aug|august|sep|sept|september|oct|october|nov|november|dec|december)\b",
    re.IGNORECASE,
)
HOSTNAME_LIKE_PATTERN = re.compile(r"^[A-Z]{2,}[A-Z0-9-]*\d[A-Z0-9-]*$")
NOISE_LABEL_WORDS = {
    "backup",
    "bit",
    "cpu",
    "edition",
    "f5",
    "firewall",
    "gb",
    "ghz",
    "ibm",
    "internet",
    "live",
    "ram",
    "router",
    "server",
    "servers",
    "service pack",
    "switch",
    "vm",
    "web servers",
    "win2008",
}


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


def _is_plausible_application_label(label: str) -> bool:
    stripped = " ".join(label.split()).strip(" -_:")
    if len(stripped) < 3 or len(stripped) > 80:
        return False
    lowered = stripped.lower()
    if "://" in lowered or lowered.startswith(("http", "www.")):
        return False
    if MONTH_PATTERN.search(stripped) and any(char.isdigit() for char in stripped):
        return False
    if not any(char.isalpha() for char in stripped):
        return False
    if HOSTNAME_LIKE_PATTERN.fullmatch(stripped) and " " not in stripped:
        return False
    if any(word in lowered for word in NOISE_LABEL_WORDS):
        return False
    return True


def _fallback_application_name(topology: TopologyGraph) -> str:
    preferred_types = {"application", "software"}
    for node in topology.nodes:
        label = " ".join(node.label.split())
        if node.node_type in preferred_types and _is_plausible_application_label(label):
            return label
    for node in topology.nodes:
        label = " ".join(node.label.split())
        if node.node_type not in {"host", "server"} and _is_plausible_application_label(label):
            return label
    for node in topology.nodes:
        label = " ".join(node.label.split())
        if _is_plausible_application_label(label):
            return label
    return "Application"


def infer_ucontrol_application_name(
    topology: TopologyGraph,
    structured_diagram: StructuredDiagram | None = None,
) -> str:
    """Infer an application name from extracted labels, preferring top/central text."""

    if structured_diagram is None or not structured_diagram.ocr_spans:
        return _fallback_application_name(topology)

    topology_labels = {node.label.strip().lower(): node for node in topology.nodes}
    page_heights = {page.page_id: page.height for page in structured_diagram.pages}
    page_widths = {page.page_id: page.width for page in structured_diagram.pages}
    candidates: list[tuple[float, str]] = []

    for span in structured_diagram.ocr_spans:
        label = " ".join(span.text.split())
        if not _is_plausible_application_label(label):
            continue

        page_height = page_heights.get(span.page_id) or 1
        page_width = page_widths.get(span.page_id) or 1
        top_ratio = span.bbox.top / page_height
        center_distance = abs(span.center.x - (page_width / 2)) / page_width
        score = span.confidence * 20
        score += max(0.0, 80.0 * (1.0 - top_ratio))
        score += max(0.0, 20.0 * (1.0 - center_distance))
        score += min(span.bbox.width / 10.0, 20.0)

        matching_node = topology_labels.get(label.lower())
        if matching_node is not None:
            score += 100.0
            if matching_node.node_type in {"application", "software"}:
                score += 50.0
        if any(word in label.lower() for word in ("app", "application", "service", "web")):
            score += 20.0

        candidates.append((score, label))

    if not candidates:
        return _fallback_application_name(topology)
    return max(candidates, key=lambda item: item[0])[1]


def resolve_ucontrol_application_identity(
    topology: TopologyGraph,
    config: OutputConfig,
    structured_diagram: StructuredDiagram | None = None,
) -> tuple[str, str]:
    application_name = (
        config.application_name.strip()
        if config.application_name and config.application_name.strip()
        else infer_ucontrol_application_name(topology, structured_diagram)
    )
    app_id = (
        config.app_id.strip()
        if config.app_id and config.app_id.strip()
        else f"{application_name}01"
    )
    return application_name, app_id


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


def build_ucontrol_model_create_request(
    application_name: str,
    app_id: str,
) -> dict[str, object]:
    """Build a request descriptor for uControl model creation query params."""

    description = f"{application_name} application"
    params = [
        {"name": "name", "value": application_name},
        {"name": "description", "value": description},
        {"name": "appID", "value": app_id},
        {"name": "modellingType", "value": "Standard"},
        {"name": "applicationType", "value": "Application Service"},
    ]
    query_string = "&".join(
        f"{quote(param['name'], safe='')}={quote(param['value'], safe='')}"
        for param in params
    )
    endpoint = "/api/umap/model/create"
    return {
        "method": "POST",
        "endpoint": endpoint,
        "query_string": query_string,
        "url": f"{endpoint}?{query_string}",
        "insomnia_url": f"{{{{UCONTROL_BASE}}}}{endpoint}?{query_string}",
        "params": params,
        "headers": [
            {
                "name": "Cookie",
                "value": "{{COOKIE}}",
            }
        ],
        "curl": (
            'curl -sS -X POST "{{UCONTROL_BASE}}'
            f'{endpoint}?{query_string}" -b "{{{{COOKIE}}}}"'
        ),
    }


def build_ucontrol_populate_umap_body(
    topology: TopologyGraph,
    u_map_id_placeholder: str = "<uMapId>",
) -> dict[str, object]:
    """Build the JSON body for mapping extracted Hosts to a uMap model."""

    data: list[dict[str, str]] = []
    seen_hosts: set[str] = set()
    for node in topology.nodes:
        name = " ".join(node.label.split())
        if not name or name in seen_hosts or _bmc_kind_for_node(node) != "Host":
            continue
        seen_hosts.add(name)
        data.append(
            {
                "ciType": "Host",
                "uMapId": u_map_id_placeholder,
                "name": name,
            }
        )
    return {"data": data}


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
        filter_expression = f"asset.nameEQUALS'{name}'"
        requests.append(
            {
                "node_name": name,
                "node_type": node.node_type,
                "definition": definition,
                "filter": filter_expression,
                "method": "GET",
                "endpoint": (
                    f"/api/asset/data/{quote(definition, safe='')}"
                    f"?filter={quote(filter_expression, safe='')}"
                ),
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
        application_name, app_id = resolve_ucontrol_application_identity(
            topology=topology,
            config=config,
            structured_diagram=structured_diagram,
        )
        ucontrol_model_create_path = output_dir / "ucontrol_model_create.json"
        ucontrol_model_create_path.write_text(
            json.dumps(
                build_ucontrol_model_create_request(application_name, app_id),
                indent=config.json_indent,
            ) + "\n",
            encoding="utf-8",
        )
        output_paths["ucontrol_model_create"] = ucontrol_model_create_path

        ucontrol_populate_path = output_dir / "ucontrol_populate_umap.json"
        ucontrol_populate_path.write_text(
            json.dumps(
                build_ucontrol_populate_umap_body(topology),
                indent=config.json_indent,
            ) + "\n",
            encoding="utf-8",
        )
        output_paths["ucontrol_populate_umap"] = ucontrol_populate_path

        ucontrol_retrieval_path = output_dir / "ucontrol_retrieval_requests.json"
        ucontrol_retrieval_path.write_text(
            json.dumps(
                build_ucontrol_retrieval_requests(topology, application_name),
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
