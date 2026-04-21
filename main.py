"""CLI entrypoint for the infrastructure topology extraction pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from diagram_parser.config import PipelineConfig
from diagram_parser.main import run_direct_llm, run_pipeline


SUPPORTED_PYTHON_MINOR_VERSIONS = {(3, 11), (3, 12)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract infrastructure topology from a diagram image."
    )
    parser.add_argument("image", type=Path, help="Path to a PNG/JPG/PDF infrastructure diagram.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory where topology.json and topology.mmd will be written.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="LMStudio model identifier. Defaults to config default.",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="LMStudio OpenAI-compatible base URL. Defaults to http://127.0.0.1:1234/v1.",
    )
    parser.add_argument(
        "--mode",
        choices=("pipeline", "direct-llm", "both"),
        default="pipeline",
        help="Run the staged pipeline, a direct image-to-LLM path, or both.",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip Stage 4 LLM normalization and use deterministic fallback output only.",
    )
    parser.add_argument(
        "--allow-llm-fallback",
        action="store_true",
        help="If LMStudio is unreachable, continue with deterministic fallback output.",
    )
    parser.add_argument(
        "--group-distance",
        type=float,
        default=None,
        help="Override text grouping distance threshold in pixels.",
    )
    parser.add_argument(
        "--endpoint-distance",
        type=float,
        default=None,
        help="Override maximum endpoint-to-node mapping distance in pixels.",
    )
    parser.add_argument(
        "--pdf-scale",
        type=float,
        default=None,
        help="Rasterization scale for PDF pages before OCR. Lower values use less memory.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Limit PDF processing to the first N pages.",
    )
    parser.add_argument(
        "--no-ocr-cache",
        action="store_true",
        help="Disable reuse of cached OCR spans.",
    )
    parser.add_argument(
        "--refresh-ocr-cache",
        action="store_true",
        help="Force PaddleOCR to rerun and overwrite the OCR cache.",
    )
    parser.add_argument(
        "--ocr-det-model-dir",
        type=str,
        default=None,
        help="Optional local PaddleOCR text detection model directory for offline runs.",
    )
    parser.add_argument(
        "--ocr-rec-model-dir",
        type=str,
        default=None,
        help="Optional local PaddleOCR text recognition model directory for offline runs.",
    )
    parser.add_argument(
        "--ocr-cls-model-dir",
        type=str,
        default=None,
        help="Optional local PaddleOCR text line orientation model directory for offline runs.",
    )
    return parser


def validate_runtime() -> None:
    version = sys.version_info[:2]
    if version not in SUPPORTED_PYTHON_MINOR_VERSIONS:
        supported = ", ".join(
            f"{major}.{minor}" for major, minor in sorted(SUPPORTED_PYTHON_MINOR_VERSIONS)
        )
        raise RuntimeError(
            f"Unsupported Python runtime: {sys.version.split()[0]}. "
            f"This project currently expects Python {supported} because PaddlePaddle "
            "does not provide reliable support for newer runtimes such as 3.13/3.14."
        )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    validate_runtime()

    image_path = args.image.expanduser().resolve()
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else PipelineConfig.default_output_dir(image_path)
    )
    if args.mode != "pipeline" and args.skip_llm:
        parser.error("--skip-llm cannot be used with `direct-llm` or `both` mode.")

    config = PipelineConfig()
    if args.skip_llm:
        config.llm.enabled = False
    if args.allow_llm_fallback:
        config.llm.allow_fallback_on_error = True
    if args.model:
        config.llm.model = args.model
    if args.base_url:
        config.llm.base_url = args.base_url
    if args.group_distance is not None:
        config.grouping.max_merge_distance = args.group_distance
    if args.endpoint_distance is not None:
        config.connections.node_endpoint_distance = args.endpoint_distance
    if args.pdf_scale is not None:
        config.ocr.pdf_render_scale = args.pdf_scale
    if args.max_pages is not None:
        config.ocr.max_pages = args.max_pages
    if args.no_ocr_cache:
        config.ocr.use_cache = False
    if args.refresh_ocr_cache:
        config.ocr.refresh_cache = True
    if args.ocr_det_model_dir:
        config.ocr.text_detection_model_dir = args.ocr_det_model_dir
    if args.ocr_rec_model_dir:
        config.ocr.text_recognition_model_dir = args.ocr_rec_model_dir
    if args.ocr_cls_model_dir:
        config.ocr.textline_orientation_model_dir = args.ocr_cls_model_dir

    results: list[tuple[str, object, dict[str, Path]]] = []
    failures: list[tuple[str, Exception]] = []

    def _run_mode(
        label: str,
        runner: Callable[[Path, Path, PipelineConfig], tuple[object, dict[str, Path]]],
        mode_output_dir: Path,
    ) -> None:
        try:
            topology, output_paths = runner(
                image_path=image_path,
                output_dir=mode_output_dir,
                config=config,
            )
            results.append((label, topology, output_paths))
        except Exception as exc:
            failures.append((label, exc))

    if args.mode in {"pipeline", "both"}:
        _run_mode("pipeline", run_pipeline, output_dir / "pipeline")

    if args.mode in {"direct-llm", "both"}:
        _run_mode("direct_llm", run_direct_llm, output_dir / "direct_llm")

    if not results and failures:
        failure_lines = [f"[{label}] {exc}" for label, exc in failures]
        raise RuntimeError("All selected modes failed:\n" + "\n".join(failure_lines))

    print(f"Processed {image_path}")
    for label, topology, output_paths in results:
        print(f"[{label}] Detected {len(topology.nodes)} nodes and {len(topology.edges)} edges")
        for name, path in output_paths.items():
            print(f"[{label}] {name}: {path}")

    for label, exc in failures:
        print(f"[{label}] ERROR: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
