"""Stage 6: persist JSON and Mermaid output."""

from __future__ import annotations

import json
from pathlib import Path

from diagram_parser.config import OutputConfig
from diagram_parser.models import StructuredDiagram, TopologyGraph


def build_mermaid(topology: TopologyGraph) -> str:
    """Convert the validated topology into a Mermaid graph."""

    lines = ["graph TD"]

    for node in topology.nodes:
        label = node.label.replace('"', '\\"')
        lines.append(f'    {node.node_id}["{label}\\n({node.node_type})"]')

    for edge in topology.edges:
        connector = "-->" if edge.directional else "---"
        label_parts = [part for part in [edge.protocol, edge.port] if part]
        if label_parts:
            edge_label = " ".join(label_parts).replace('"', '\\"')
            lines.append(
                f'    {edge.from_node_id} {connector}|"{edge_label}"| {edge.to_node_id}'
            )
        else:
            lines.append(f"    {edge.from_node_id} {connector} {edge.to_node_id}")

    return "\n".join(lines) + "\n"


def save_outputs(
    output_dir: Path,
    topology: TopologyGraph,
    mermaid: str,
    config: OutputConfig,
    structured_diagram: StructuredDiagram | None = None,
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

    if config.save_intermediate and structured_diagram is not None:
        intermediate_path = output_dir / "structured_candidates.json"
        intermediate_path.write_text(
            json.dumps(structured_diagram.to_dict(), indent=config.json_indent) + "\n",
            encoding="utf-8",
        )
        output_paths["intermediate"] = intermediate_path

    return output_paths
