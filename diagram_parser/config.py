"""Configuration for the staged diagram parsing pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class OCRConfig:
    language: str = "en"
    use_angle_cls: bool = True
    disable_model_source_check: bool = True
    pdf_render_scale: float = 1.25
    max_pages: int | None = None
    use_cache: bool = True
    refresh_cache: bool = False
    text_detection_model_dir: str | None = None
    text_recognition_model_dir: str | None = None
    textline_orientation_model_dir: str | None = None


@dataclass(slots=True)
class GroupingConfig:
    max_merge_distance: float = 30.0
    horizontal_gap_threshold: float = 28.0
    vertical_gap_threshold: float = 24.0
    same_line_tolerance: float = 18.0
    alignment_tolerance: float = 52.0


@dataclass(slots=True)
class ConnectionConfig:
    canny_threshold_1: int = 50
    canny_threshold_2: int = 150
    hough_threshold: int = 30
    min_line_length: int = 40
    max_line_gap: int = 18
    text_mask_padding: int = 8
    node_endpoint_distance: float = 80.0
    line_label_distance: float = 90.0


@dataclass(slots=True)
class LLMConfig:
    enabled: bool = True
    allow_fallback_on_error: bool = False
    include_ucontrol_asset_rag: bool = True
    use_response_format: bool = True
    repair_retries: int = 1
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = "local-model"
    temperature: float = 0.0
    timeout_seconds: int = 300
    max_tokens: int = 2400


@dataclass(slots=True)
class OutputConfig:
    json_indent: int = 2
    application_name: str = "Application1"
    save_ucontrol_asset_tags: bool = True
    save_intermediate: bool = True
    save_llm_debug: bool = True


@dataclass(slots=True)
class PipelineConfig:
    ocr: OCRConfig = field(default_factory=OCRConfig)
    grouping: GroupingConfig = field(default_factory=GroupingConfig)
    connections: ConnectionConfig = field(default_factory=ConnectionConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    @staticmethod
    def default_output_dir(image_path: Path) -> Path:
        return Path.cwd() / "output"
