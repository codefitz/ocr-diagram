"""Pipeline orchestration."""

from __future__ import annotations

from pathlib import Path

from diagram_parser.config import PipelineConfig
from diagram_parser.llm import convert_structured_data_to_topology
from diagram_parser.models import StructuredDiagram, TopologyGraph
from diagram_parser.ocr import run_ocr
from diagram_parser.output import build_mermaid, save_outputs
from diagram_parser.processing import detect_connections, group_text_into_nodes, validate_topology


def run_pipeline(image_path: Path, output_dir: Path, config: PipelineConfig) -> tuple[TopologyGraph, dict[str, Path]]:
    """Run all pipeline stages in sequence."""

    ocr_spans = run_ocr(image_path=image_path, config=config.ocr)
    candidate_nodes = group_text_into_nodes(spans=ocr_spans, config=config.grouping)
    candidate_connections, image_size = detect_connections(
        image_path=image_path,
        spans=ocr_spans,
        nodes=candidate_nodes,
        config=config.connections,
    )

    structured_diagram = StructuredDiagram(
        image_path=image_path,
        ocr_spans=ocr_spans,
        candidate_nodes=candidate_nodes,
        candidate_connections=candidate_connections,
        image_size=image_size,
    )
    raw_topology = convert_structured_data_to_topology(
        structured_diagram=structured_diagram,
        config=config.llm,
    )
    topology = validate_topology(raw_payload=raw_topology, structured_diagram=structured_diagram)
    mermaid = build_mermaid(topology)
    output_paths = save_outputs(
        output_dir=output_dir,
        topology=topology,
        mermaid=mermaid,
        config=config.output,
        structured_diagram=structured_diagram,
    )
    return topology, output_paths
