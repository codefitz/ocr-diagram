"""CLI entrypoint for the infrastructure topology extraction pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from diagram_parser.config import PipelineConfig
from diagram_parser.main import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract infrastructure topology from a diagram image."
    )
    parser.add_argument("image", type=Path, help="Path to a PNG/JPG infrastructure diagram.")
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
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    image_path = args.image.expanduser().resolve()
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else PipelineConfig.default_output_dir(image_path)
    )

    config = PipelineConfig()
    if args.model:
        config.llm.model = args.model
    if args.base_url:
        config.llm.base_url = args.base_url
    if args.group_distance is not None:
        config.grouping.max_merge_distance = args.group_distance
    if args.endpoint_distance is not None:
        config.connections.node_endpoint_distance = args.endpoint_distance

    topology, output_paths = run_pipeline(
        image_path=image_path,
        output_dir=output_dir,
        config=config,
    )

    print(f"Processed {image_path}")
    print(f"Detected {len(topology.nodes)} nodes and {len(topology.edges)} edges")
    for name, path in output_paths.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
