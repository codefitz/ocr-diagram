"""Pipeline orchestration."""

from __future__ import annotations

import warnings
from pathlib import Path

from diagram_parser.config import PipelineConfig
from diagram_parser.llm import convert_images_to_topology, convert_structured_data_to_topology
from diagram_parser.models import StructuredDiagram, TopologyGraph
from diagram_parser.ocr import load_document_pages, load_ocr_cache, run_ocr, save_ocr_cache
from diagram_parser.output import build_mermaid, save_llm_debug, save_outputs
from diagram_parser.processing import detect_connections, group_text_into_nodes, validate_topology


def run_pipeline(image_path: Path, output_dir: Path, config: PipelineConfig) -> tuple[TopologyGraph, dict[str, Path]]:
    """Run all pipeline stages in sequence."""

    pages = load_document_pages(source_path=image_path, config=config.ocr)
    ocr_cache_path = output_dir / "ocr_spans.json"

    ocr_spans = None
    if config.ocr.use_cache and not config.ocr.refresh_cache:
        ocr_spans = load_ocr_cache(
            ocr_cache_path,
            source_path=image_path,
            config=config.ocr,
        )

    if ocr_spans is None:
        ocr_spans = run_ocr(pages=pages, config=config.ocr)
        if config.ocr.use_cache:
            save_ocr_cache(
                ocr_cache_path,
                source_path=image_path,
                config=config.ocr,
                spans=ocr_spans,
            )

    candidate_nodes = []
    candidate_connections = []
    for page in pages:
        page_spans = [span for span in ocr_spans if span.page_id == page.page_id]
        page_nodes = group_text_into_nodes(spans=page_spans, config=config.grouping)
        page_connections = detect_connections(
            image=page.image,
            spans=page_spans,
            nodes=page_nodes,
            config=config.connections,
        )
        candidate_nodes.extend(page_nodes)
        candidate_connections.extend(page_connections)

    structured_diagram = StructuredDiagram(
        image_path=image_path,
        pages=pages,
        ocr_spans=ocr_spans,
        candidate_nodes=candidate_nodes,
        candidate_connections=candidate_connections,
    )
    raw_topology: dict[str, object]
    llm_artifacts: dict[str, object] | None = None
    if config.llm.enabled:
        try:
            raw_topology, artifacts = convert_structured_data_to_topology(
                structured_diagram=structured_diagram,
                config=config.llm,
            )
            llm_artifacts = artifacts.to_dict()
        except Exception as exc:
            if hasattr(exc, "artifacts"):
                save_llm_debug(output_dir, exc.artifacts.to_dict(), config.output)  # type: ignore[attr-defined]
            if not config.llm.allow_fallback_on_error:
                raise
            warnings.warn(
                f"LLM stage failed; continuing with deterministic fallback. Reason: {exc}",
                stacklevel=2,
            )
            llm_artifacts = (
                exc.artifacts.to_dict()  # type: ignore[attr-defined]
                if hasattr(exc, "artifacts")
                else {"error": str(exc)}
            )
            raw_topology = {}
    else:
        raw_topology = {}
    topology = validate_topology(raw_payload=raw_topology, structured_diagram=structured_diagram)
    mermaid = build_mermaid(topology)
    output_paths = save_outputs(
        output_dir=output_dir,
        topology=topology,
        mermaid=mermaid,
        config=config.output,
        structured_diagram=structured_diagram,
        llm_artifacts=llm_artifacts,
    )
    if config.ocr.use_cache:
        output_paths["ocr_cache"] = ocr_cache_path
    return topology, output_paths


def run_direct_llm(image_path: Path, output_dir: Path, config: PipelineConfig) -> tuple[TopologyGraph, dict[str, Path]]:
    """Run a direct image-to-LLM path for comparison with the staged pipeline."""

    pages = load_document_pages(source_path=image_path, config=config.ocr)
    structured_diagram = StructuredDiagram(
        image_path=image_path,
        pages=pages,
        ocr_spans=[],
        candidate_nodes=[],
        candidate_connections=[],
    )
    try:
        raw_topology, artifacts = convert_images_to_topology(
            image_path=str(image_path),
            pages=pages,
            config=config.llm,
        )
    except Exception as exc:
        if hasattr(exc, "artifacts"):
            save_llm_debug(output_dir, exc.artifacts.to_dict(), config.output)  # type: ignore[attr-defined]
        raise
    topology = validate_topology(raw_payload=raw_topology, structured_diagram=structured_diagram)
    mermaid = build_mermaid(topology)
    output_paths = save_outputs(
        output_dir=output_dir,
        topology=topology,
        mermaid=mermaid,
        config=config.output,
        structured_diagram=None,
        llm_artifacts=artifacts.to_dict(),
    )
    return topology, output_paths
