"""Stage 4: send structured data to LMStudio and parse strict JSON."""

from __future__ import annotations

import json
import re
from typing import Any

from diagram_parser.config import LLMConfig
from diagram_parser.models import StructuredDiagram


JSON_BLOCK_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


def _build_prompt(structured_diagram: StructuredDiagram) -> list[dict[str, str]]:
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
        f"Schema:\n{json.dumps(schema, indent=2)}"
    )
    evidence = json.dumps(structured_diagram.to_dict(), indent=2)
    return [
        {"role": "system", "content": instructions},
        {
            "role": "user",
            "content": (
                "Convert the following structured diagram evidence into the target JSON schema.\n"
                "Return null for unsupported or unknown fields.\n"
                f"Evidence:\n{evidence}"
            ),
        },
    ]


def _extract_json_object(raw_content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_content)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = JSON_BLOCK_PATTERN.search(raw_content)
    if not match:
        raise ValueError("LMStudio response did not contain a JSON object.")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LMStudio response was not a JSON object.")
    return parsed


def convert_structured_data_to_topology(
    structured_diagram: StructuredDiagram,
    config: LLMConfig,
) -> dict[str, Any]:
    """Call LMStudio's OpenAI-compatible API using structured, non-visual input."""

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "The requests package is required for LMStudio API calls."
        ) from exc

    response = requests.post(
        url=f"{config.base_url.rstrip('/')}/chat/completions",
        headers={"Content-Type": "application/json"},
        json={
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": _build_prompt(structured_diagram),
        },
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("LMStudio returned a non-text response.")
    return _extract_json_object(content)
