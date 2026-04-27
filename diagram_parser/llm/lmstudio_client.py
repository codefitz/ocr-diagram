"""Stage 4: send structured data to a local LLM server and parse strict JSON."""

from __future__ import annotations

import json
import re
import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from diagram_parser.config import LLMConfig
from diagram_parser.models import DocumentPage, StructuredDiagram


JSON_BLOCK_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
CODE_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
UCONTROL_RAG_PATH = Path(__file__).with_name("rag") / "ucontrol_asset_tag_schema.md"


@dataclass(slots=True)
class LLMCallArtifacts:
    request_messages: list[dict[str, Any]] = field(default_factory=list)
    raw_response_payload: dict[str, Any] | None = None
    raw_content: str = ""
    repair_raw_content: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_messages": self.request_messages,
            "raw_response_payload": self.raw_response_payload,
            "raw_content": self.raw_content,
            "repair_raw_content": self.repair_raw_content,
            "error": self.error,
        }


class LLMResponseError(ValueError):
    def __init__(self, message: str, artifacts: LLMCallArtifacts) -> None:
        super().__init__(message)
        self.artifacts = artifacts


@dataclass(frozen=True, slots=True)
class ChatEndpoint:
    kind: str
    url: str


def _append_no_think(prompt: str) -> str:
    stripped = prompt.rstrip()
    if stripped.endswith("/no_think"):
        return stripped
    return f"{stripped}\n/no_think"


def _topology_json_schema_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "topology_graph",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                                "type": {
                                    "type": "string",
                                    "enum": ["server", "database", "application", "network", "zone", "unknown"],
                                },
                                "ip": {"type": ["string", "null"]},
                            },
                            "required": ["id", "label", "type", "ip"],
                            "additionalProperties": False,
                        },
                    },
                    "edges": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "protocol": {"type": ["string", "null"]},
                                "port": {"type": ["string", "null"]},
                                "directional": {"type": "boolean"},
                            },
                            "required": ["from", "to", "protocol", "port", "directional"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["nodes", "edges"],
                "additionalProperties": False,
            },
        },
    }


def _topology_json_schema() -> dict[str, Any]:
    return _topology_json_schema_response_format()["json_schema"]["schema"]


def _load_ucontrol_asset_rag(config: LLMConfig) -> str:
    if not config.include_ucontrol_asset_rag:
        return ""
    try:
        return UCONTROL_RAG_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _resolve_chat_endpoint(base_url: str) -> ChatEndpoint:
    normalized = base_url.rstrip("/")
    parsed = urlsplit(normalized)
    path = parsed.path.rstrip("/")

    if path.endswith("/chat/completions"):
        return ChatEndpoint(kind="openai", url=normalized)
    if path.endswith("/v1"):
        return ChatEndpoint(kind="openai", url=f"{normalized}/chat/completions")
    if path.endswith("/api/chat"):
        return ChatEndpoint(kind="ollama", url=normalized)
    if path.endswith("/api"):
        return ChatEndpoint(kind="ollama", url=f"{normalized}/chat")
    if not path:
        return ChatEndpoint(kind="openai", url=f"{normalized}/v1/chat/completions")
    return ChatEndpoint(kind="openai", url=f"{normalized}/chat/completions")


def _build_llm_evidence(structured_diagram: StructuredDiagram) -> dict[str, Any]:
    return {
        "image_path": str(structured_diagram.image_path),
        "pages": [page.to_dict() for page in structured_diagram.pages],
        "ocr_text_spans": [
            {
                "page_id": span.page_id,
                "span_id": span.span_id,
                "text": span.text,
                "confidence": round(span.confidence, 4),
                "bbox": span.bbox.to_dict(),
            }
            for span in structured_diagram.ocr_spans
            if span.text.strip()
        ],
        "candidate_nodes": [
            {
                "page_id": node.page_id,
                "node_id": node.node_id,
                "label": node.label,
                "bbox": node.bbox.to_dict(),
                "center": node.center.to_dict(),
                "type_hint": node.type_hint,
                "texts": list(node.texts),
            }
            for node in structured_diagram.candidate_nodes
        ],
        "candidate_connections": [
            {
                "page_id": edge.page_id,
                "from_node_id": edge.from_node_id,
                "to_node_id": edge.to_node_id,
                "confidence": round(edge.confidence, 4),
                "label_hints": list(edge.label_hints),
                "line_midpoint": edge.line.midpoint.to_dict(),
            }
            for edge in structured_diagram.candidate_connections
        ],
    }


def _build_prompt(structured_diagram: StructuredDiagram, config: LLMConfig) -> list[dict[str, str]]:
    schema = {
        "nodes": [
            {
                "id": "string",
                "label": "string",
                "type": "server|database|application|network|zone|unknown",
                "ip": "string|null",
            }
        ],
        "edges": [
            {
                "from": "string",
                "to": "string",
                "protocol": "string|null",
                "port": "string|null",
                "directional": "boolean",
            }
        ],
    }
    ucontrol_rag = _load_ucontrol_asset_rag(config)
    ucontrol_guidance = (
        "\n\nAdditional optional target-output guidance:\n"
        f"{ucontrol_rag}\n"
        "This guidance is for choosing accurate node labels and coarse topology types. "
        "Still return only the strict topology JSON schema below from this LLM call."
        if ucontrol_rag
        else ""
    )
    instructions = (
        "You convert structured infrastructure diagram evidence into JSON topology.\n"
        "Rules:\n"
        "1. Return valid JSON only. No markdown.\n"
        "2. Use only evidence from the provided OCR spans, grouped candidate nodes, and candidate connections.\n"
        "3. Do not hallucinate labels, IPs, protocols, ports, or edge directions.\n"
        "4. If a value is unknown, use null.\n"
        "5. Keep node types within the allowed enum.\n"
        "6. Preserve connections only when the candidate connection list supports them.\n"
        "7. Prefer candidate node IDs where possible so edges can reference stable IDs.\n"
        f"{ucontrol_guidance}\n"
        f"Schema:\n{json.dumps(schema, indent=2)}"
    )
    evidence = json.dumps(_build_llm_evidence(structured_diagram), indent=2)
    user_prompt = (
        "Convert the following structured diagram evidence into the target JSON schema.\n"
        "Return null for unsupported or unknown fields.\n"
        f"Evidence:\n{evidence}"
    )
    return [
        {"role": "system", "content": instructions},
        {
            "role": "user",
            "content": _append_no_think(user_prompt),
        },
    ]


def _build_direct_llm_prompt(image_path: str, pages: list[DocumentPage], config: LLMConfig) -> list[dict[str, Any]]:
    page_refs = ", ".join(page.page_id for page in pages)
    ucontrol_rag = _load_ucontrol_asset_rag(config)
    ucontrol_guidance = (
        "Additional optional target-output guidance:\n"
        f"{ucontrol_rag}\n"
        "This guidance is for choosing accurate node labels and coarse topology types. "
        "Still return only the strict topology JSON schema below from this LLM call.\n"
        if ucontrol_rag
        else ""
    )
    text_prompt = (
        "Return exactly one JSON object and nothing else.\n"
        "You are extracting infrastructure topology directly from diagram images.\n"
        f"{ucontrol_guidance}"
        "Schema:\n"
        "{\n"
        '  "nodes": [{"id":"string","label":"string","type":"server|database|application|network|zone|unknown","ip":"string|null"}],\n'
        '  "edges": [{"from":"string","to":"string","protocol":"string|null","port":"string|null","directional":true}]\n'
        "}\n"
        "Rules:\n"
        "1. Use only information visible in the diagram images.\n"
        "2. Copy labels faithfully but you may normalize obvious OCR-free reading issues from the image itself.\n"
        "3. Use null for unknown IPs, protocols, ports, or directions.\n"
        "4. Do not invent hidden infrastructure.\n"
        "5. Prefer concise node labels from the diagram, not long concatenations.\n"
        f"Source file: {image_path}\n"
        f"Rendered pages included: {page_refs}\n"
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": _append_no_think(text_prompt)}]
    for page in pages:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _page_to_data_url(page)},
            }
        )
    return [{"role": "user", "content": content}]


def _build_single_message_retry_prompt(structured_diagram: StructuredDiagram, config: LLMConfig) -> list[dict[str, str]]:
    evidence = json.dumps(_build_llm_evidence(structured_diagram), indent=2)
    ucontrol_rag = _load_ucontrol_asset_rag(config)
    ucontrol_guidance = (
        f"Optional target-output guidance:\n{ucontrol_rag}\n"
        "Still return only the strict topology JSON schema below.\n"
        if ucontrol_rag
        else ""
    )
    retry_prompt = (
        "Return exactly one JSON object and nothing else.\n"
        f"{ucontrol_guidance}"
        "Schema:\n"
        "{\n"
        '  "nodes": [{"id":"string","label":"string","type":"server|database|application|network|zone|unknown","ip":"string|null"}],\n'
        '  "edges": [{"from":"string","to":"string","protocol":"string|null","port":"string|null","directional":true}]\n'
        "}\n"
        "Use only the supplied evidence. Use null for unknown fields. Do not hallucinate.\n"
        f"Evidence:\n{evidence}"
    )
    return [
        {
            "role": "user",
            "content": _append_no_think(retry_prompt),
        }
    ]


def _build_direct_llm_retry_prompt() -> list[dict[str, str]]:
    retry_prompt = (
        "Your previous response was invalid. Return exactly one JSON object, no markdown, no commentary, no reasoning."
    )
    return [
        {
            "role": "user",
            "content": _append_no_think(retry_prompt),
        }
    ]


def _extract_json_object(raw_content: str) -> dict[str, Any]:
    candidate_texts = [raw_content.strip()]

    fence_match = CODE_FENCE_PATTERN.search(raw_content)
    if fence_match:
        candidate_texts.append(fence_match.group(1).strip())

    for candidate in candidate_texts:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    match = JSON_BLOCK_PATTERN.search(raw_content)
    if not match:
        excerpt = raw_content.strip().replace("\n", " ")
        raise ValueError(
            f"LLM response did not contain a JSON object. Raw content: {excerpt[:1200]}"
        )
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM response was not a JSON object.")
    return parsed


def _normalize_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "content", "value", "output_text"):
                    text = item.get(key)
                    if isinstance(text, str) and text.strip():
                        parts.append(text)
                        break
        return "\n".join(part for part in parts if part).strip()
    raise ValueError("LLM server returned a non-text response.")


def _page_to_data_url(page: DocumentPage) -> str:
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError("OpenCV is required to encode page images for direct LLM mode.") from exc

    ok, encoded = cv2.imencode(".png", page.image)
    if not ok:
        raise RuntimeError(f"Failed to encode page image for {page.page_id}")
    encoded_bytes = encoded.tobytes()
    return f"data:image/png;base64,{base64.b64encode(encoded_bytes).decode('ascii')}"


def _extract_data_url_payload(url: str) -> str:
    if not url.startswith("data:") or "," not in url:
        raise ValueError(
            "Ollama native API expects base64 image data. Use a data URL or an OpenAI-compatible /v1 endpoint."
        )
    return url.split(",", 1)[1]


def _convert_messages_to_ollama(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted_messages: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")

        if isinstance(content, str):
            converted_messages.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            raise ValueError("Unsupported message content for Ollama native API.")

        text_parts: list[str] = []
        images: list[str] = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
                continue
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")
            if item_type == "text":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text)
                continue

            if item_type == "image_url":
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    url = image_url.get("url")
                    if isinstance(url, str) and url.strip():
                        images.append(_extract_data_url_payload(url))

        converted_message: dict[str, Any] = {
            "role": role,
            "content": "\n".join(part for part in text_parts if part).strip(),
        }
        if images:
            converted_message["images"] = images
        converted_messages.append(converted_message)

    return converted_messages


def _extract_content_from_payload(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    if isinstance(message, dict):
        for key in ("content", "thinking"):
            if key in message:
                try:
                    normalized = _normalize_message_content(message[key])
                except ValueError:
                    normalized = ""
                if normalized:
                    return normalized

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            message = first_choice.get("message")
            if isinstance(message, dict):
                for key in ("content", "reasoning_content", "reasoning", "text"):
                    if key in message:
                        try:
                            normalized = _normalize_message_content(message[key])
                        except ValueError:
                            normalized = ""
                        if normalized:
                            return normalized
            if "text" in first_choice:
                try:
                    normalized = _normalize_message_content(first_choice["text"])
                except ValueError:
                    normalized = ""
                if normalized:
                    return normalized

    if "content" in payload:
        return _normalize_message_content(payload["content"])
    if "text" in payload:
        return _normalize_message_content(payload["text"])

    return ""


def _build_request_payload(
    endpoint: ChatEndpoint,
    config: LLMConfig,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    if endpoint.kind == "ollama":
        request_payload: dict[str, Any] = {
            "model": config.model,
            "messages": _convert_messages_to_ollama(messages),
            "stream": False,
            "think": False,
            "options": {
                "temperature": config.temperature,
                "num_predict": config.max_tokens,
            },
        }
        if config.use_response_format:
            request_payload["format"] = _topology_json_schema()
        return request_payload

    request_payload = {
        "model": config.model,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "messages": messages,
        "stream": False,
    }
    if config.use_response_format:
        request_payload["response_format"] = _topology_json_schema_response_format()
    return request_payload


def _post_chat_completion(
    requests_module: Any,
    config: LLMConfig,
    messages: list[dict[str, Any]],
    artifacts: LLMCallArtifacts,
) -> str:
    endpoint = _resolve_chat_endpoint(config.base_url)
    request_payload = _build_request_payload(endpoint, config, messages)

    artifacts.request_messages = messages
    response = requests_module.post(
        url=endpoint.url,
        headers={"Content-Type": "application/json"},
        json=request_payload,
        timeout=config.timeout_seconds,
    )
    try:
        response.raise_for_status()
    except requests_module.exceptions.HTTPError as exc:
        if response.status_code == 400 and config.use_response_format:
            if endpoint.kind == "ollama":
                request_payload.pop("format", None)
            else:
                request_payload.pop("response_format", None)
            response = requests_module.post(
                url=endpoint.url,
                headers={"Content-Type": "application/json"},
                json=request_payload,
                timeout=config.timeout_seconds,
            )
            response.raise_for_status()
        else:
            response_text = response.text.strip()
            detail = f" Response body: {response_text[:1200]}" if response_text else ""
            raise RuntimeError(
                f"LLM server returned HTTP {response.status_code} for model {config.model}.{detail}"
            ) from exc
    except requests_module.exceptions.RequestException:
        raise

    payload = response.json()
    artifacts.raw_response_payload = payload
    return _extract_content_from_payload(payload)


def convert_structured_data_to_topology(
    structured_diagram: StructuredDiagram,
    config: LLMConfig,
) -> tuple[dict[str, Any], LLMCallArtifacts]:
    """Call a local LLM API using structured, non-visual input."""

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "The requests package is required for local LLM API calls."
        ) from exc

    base_messages = _build_prompt(structured_diagram, config)
    artifacts = LLMCallArtifacts()
    try:
        content = _post_chat_completion(requests, config, base_messages, artifacts)
    except requests.exceptions.RequestException as exc:
        detail = f" ({type(exc).__name__}: {exc})" if str(exc) else f" ({type(exc).__name__})"
        raise RuntimeError(
            f"Could not reach the LLM server at {config.base_url}. "
            "Start LM Studio or Ollama, or rerun with `--skip-llm` "
            f"or `--allow-llm-fallback`.{detail}"
        ) from exc
    artifacts.raw_content = content
    try:
        return _extract_json_object(content), artifacts
    except ValueError as initial_error:
        if config.repair_retries <= 0:
            artifacts.error = str(initial_error)
            raise LLMResponseError(str(initial_error), artifacts) from initial_error

        repair_messages = _build_single_message_retry_prompt(structured_diagram, config)
        repair_content = _post_chat_completion(requests, config, repair_messages, artifacts)
        artifacts.repair_raw_content = repair_content
        try:
            return _extract_json_object(repair_content), artifacts
        except ValueError as repair_error:
            artifacts.error = f"{repair_error}. Initial raw content: {content[:1200]}"
            raise LLMResponseError(
                f"{repair_error}. Initial raw content: {content[:1200]}",
                artifacts,
            ) from initial_error


def convert_images_to_topology(
    image_path: str,
    pages: list[DocumentPage],
    config: LLMConfig,
) -> tuple[dict[str, Any], LLMCallArtifacts]:
    """Call a local LLM API directly with rendered page images."""

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "The requests package is required for local LLM API calls."
        ) from exc

    base_messages = _build_direct_llm_prompt(image_path, pages, config)
    artifacts = LLMCallArtifacts()
    try:
        content = _post_chat_completion(requests, config, base_messages, artifacts)
    except requests.exceptions.RequestException as exc:
        detail = f" ({type(exc).__name__}: {exc})" if str(exc) else f" ({type(exc).__name__})"
        raise RuntimeError(
            f"Could not reach the LLM server at {config.base_url}. "
            f"Start LM Studio or Ollama, or rerun with `--skip-llm`.{detail}"
        ) from exc
    artifacts.raw_content = content
    try:
        return _extract_json_object(content), artifacts
    except ValueError as initial_error:
        if config.repair_retries <= 0:
            artifacts.error = str(initial_error)
            raise LLMResponseError(str(initial_error), artifacts) from initial_error

        repair_messages = [*base_messages, * _build_direct_llm_retry_prompt()]
        repair_content = _post_chat_completion(requests, config, repair_messages, artifacts)
        artifacts.repair_raw_content = repair_content
        try:
            return _extract_json_object(repair_content), artifacts
        except ValueError as repair_error:
            artifacts.error = f"{repair_error}. Initial raw content: {content[:1200]}"
            raise LLMResponseError(
                f"{repair_error}. Initial raw content: {content[:1200]}",
                artifacts,
            ) from initial_error
