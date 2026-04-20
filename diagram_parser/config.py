"""Configuration for the staged diagram parsing pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class OCRConfig:
    language: str = "en"
    use_angle_cls: bool = True


@dataclass(slots=True)
class GroupingConfig:
    max_merge_distance: float = 55.0
    horizontal_gap_threshold: float = 60.0
    vertical_gap_threshold: float = 45.0
    same_line_tolerance: float = 24.0
    alignment_tolerance: float = 36.0


@dataclass(slots=True)
class ConnectionConfig:
    canny_threshold_1: int = 50
    canny_threshold_2: int = 150
    hough_threshold: int = 30
    min_line_length: int = 40
    max_line_gap: int = 18
    text_mask_padding: int = 8
    node_endpoint_distance: float = 80.0
    line_label_distance: float = 70.0


@dataclass(slots=True)
class LLMConfig:
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = "local-model"
    temperature: float = 0.0
    timeout_seconds: int = 120
    max_tokens: int = 1200


@dataclass(slots=True)
class OutputConfig:
    json_indent: int = 2
    save_intermediate: bool = True


@dataclass(slots=True)
class PipelineConfig:
    ocr: OCRConfig = field(default_factory=OCRConfig)
    grouping: GroupingConfig = field(default_factory=GroupingConfig)
    connections: ConnectionConfig = field(default_factory=ConnectionConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    @staticmethod
    def default_output_dir(image_path: Path) -> Path:
        return image_path.parent / "output"
