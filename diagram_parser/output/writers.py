"""Stage 6: persist JSON and Mermaid output."""

from __future__ import annotations

import json
from pathlib import Path

from diagram_parser.config import OutputConfig
from diagram_parser.models import StructuredDiagram, TopologyGraph


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
