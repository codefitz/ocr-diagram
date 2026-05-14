"""Create a uControl uMap model and populate it with extracted host CIs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3


DEFAULT_OUTPUT_DIR = Path("output/direct_llm")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a uControl model from generated extraction output and link host CIs.",
    )
    parser.add_argument(
        "server",
        help="uControl server base, e.g. tekucontrol.example.com or https://host/uControl.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory containing ucontrol_model_create.json and ucontrol_populate_umap.json.",
    )
    parser.add_argument(
        "--app-name",
        default=None,
        help="Override the extracted application/model name.",
    )
    parser.add_argument(
        "--app-id",
        default=None,
        help="Override generated appID. Defaults to '<app-name>01'.",
    )
    parser.add_argument(
        "--cookie",
        default=os.environ.get("COOKIE"),
        help="Optional uControl Cookie header value. Defaults to COOKIE environment variable.",
    )
    parser.add_argument(
        "--environment-id",
        default="1",
        help="Environment ID sent while linking hosts. Defaults to 1 / Not Set.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--no-ssl-verify",
        action="store_true",
        help="Disable TLS certificate verification for uControl HTTPS requests.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print requests without sending them.",
    )
    return parser


def normalize_base_url(server: str) -> str:
    value = server.strip().rstrip("/")
    if not value:
        raise ValueError("server must not be empty")
    if "://" not in value:
        value = f"https://{value}/uControl"
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid uControl server URL: {server!r}")
    return value


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FileNotFoundError(f"Required file not found: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def request_params(descriptor: dict[str, Any]) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in descriptor.get("params", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and value is not None:
            params[name] = str(value)
    return params


def apply_app_overrides(params: dict[str, str], app_name: str | None, app_id: str | None) -> dict[str, str]:
    updated = dict(params)
    if app_name and app_name.strip():
        name = " ".join(app_name.split())
        updated["name"] = name
        updated["description"] = f"{name} application"
        updated["appID"] = app_id.strip() if app_id and app_id.strip() else f"{name}01"
    elif app_id and app_id.strip():
        updated["appID"] = app_id.strip()
    return updated


def extract_umap_id(payload: Any) -> str:
    candidates: list[Any] = []
    if isinstance(payload, dict):
        candidates.extend(
            [
                payload.get("uMapModelID"),
                payload.get("uMapId"),
                payload.get("id"),
            ]
        )
        data = payload.get("data")
        if isinstance(data, list):
            candidates.extend(data)
        elif isinstance(data, dict):
            candidates.append(data)
        data_item = payload.get("dataItem")
        if isinstance(data_item, dict):
            candidates.append(data_item)

    for candidate in candidates:
        if isinstance(candidate, (str, int)) and str(candidate).strip():
            return str(candidate)
        if isinstance(candidate, dict):
            for key in ("uMapModelID", "uMapId", "id"):
                value = candidate.get(key)
                if isinstance(value, (str, int)) and str(value).strip():
                    return str(value)
    raise ValueError(f"Could not find uMapModelID/uMapId in create response: {payload!r}")


def expected_hosts(populate_payload: dict[str, Any]) -> list[str]:
    hosts: list[str] = []
    seen: set[str] = set()
    data = populate_payload.get("data", [])
    if not isinstance(data, list):
        return hosts
    for item in data:
        if not isinstance(item, dict) or item.get("ciType") != "Host":
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        normalized = " ".join(name.split())
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            hosts.append(normalized)
    return hosts


def populate_body(hosts: list[str], umap_id: str, environment_id: str) -> dict[str, Any]:
    return {
        "data": [
            {
                "ciType": "Host",
                "uMapId": umap_id,
                "environmentId": environment_id,
                "name": host,
            }
            for host in hosts
        ]
    }


def response_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw_text": response.text}


def host_names_from_ci_list(payload: Any) -> set[str]:
    names: set[str] = set()

    def collect(record: Any) -> None:
        if isinstance(record, list):
            for item in record:
                collect(item)
            return
        if not isinstance(record, dict):
            return
        for key in ("name", "ciName", "assetName", "label"):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                names.add(" ".join(value.split()).lower())
                return
        for key in ("data", "dataItem", "items", "results"):
            collect(record.get(key))

    collect(payload)
    return names


def ensure_success(response: requests.Response, action: str) -> Any:
    payload = response_json(response)
    if response.status_code >= 400:
        raise RuntimeError(f"{action} failed with HTTP {response.status_code}: {payload!r}")
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise RuntimeError(f"{action} failed: {payload!r}")
    return payload


def existing_model_umap_id(payload: Any) -> str | None:
    if not isinstance(payload, dict) or payload.get("ok") is not False:
        return None
    message = payload.get("message")
    if not isinstance(message, str) or "already exists" not in message.lower():
        return None
    try:
        return extract_umap_id(payload)
    except ValueError:
        return None


def main() -> None:
    args = build_parser().parse_args()

    base_url = normalize_base_url(args.server)
    model_descriptor = read_json(args.output_dir / "ucontrol_model_create.json")
    populate_descriptor = read_json(args.output_dir / "ucontrol_populate_umap.json")

    create_params = apply_app_overrides(
        request_params(model_descriptor),
        app_name=args.app_name,
        app_id=args.app_id,
    )
    hosts = expected_hosts(populate_descriptor)
    if not hosts:
        raise RuntimeError(f"No host entries found in {args.output_dir / 'ucontrol_populate_umap.json'}")

    create_url = f"{base_url}/api/umap/model/create"
    populate_url = f"{base_url}/api/umap/populate/umap"
    headers = {"Cookie": args.cookie} if args.cookie else {}
    verify_ssl = not args.no_ssl_verify
    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    if args.dry_run:
        preview_umap_id = "<uMapModelID>"
        print(f"DRY RUN create: POST {create_url}")
        print(json.dumps({"params": create_params}, indent=2))
        print(f"DRY RUN populate: POST {populate_url}")
        print(json.dumps(populate_body(hosts, preview_umap_id, args.environment_id), indent=2))
        print(
            "DRY RUN verify: GET "
            f"{base_url}/api/umap/model/ci/list?kind=Host&uMapId={preview_umap_id}"
        )
        return

    create_response = requests.post(
        create_url,
        params=create_params,
        headers=headers,
        verify=verify_ssl,
        timeout=args.timeout,
    )
    create_payload = response_json(create_response)
    existing_umap_id = existing_model_umap_id(create_payload)
    if existing_umap_id is not None:
        umap_id = existing_umap_id
        reused_existing_model = True
    else:
        if create_response.status_code >= 400:
            raise RuntimeError(f"Create model failed with HTTP {create_response.status_code}: {create_payload!r}")
        if isinstance(create_payload, dict) and create_payload.get("ok") is False:
            raise RuntimeError(f"Create model failed: {create_payload!r}")
        umap_id = extract_umap_id(create_payload)
        reused_existing_model = False

    body = populate_body(hosts, umap_id, args.environment_id)
    populate_response = requests.post(
        populate_url,
        data=json.dumps(body),
        headers={**headers, "Content-Type": "text/plain"},
        verify=verify_ssl,
        timeout=args.timeout,
    )
    populate_payload = ensure_success(populate_response, "Populate uMap")

    verify_response = requests.get(
        f"{base_url}/api/umap/model/ci/list",
        params={"kind": "Host", "uMapId": umap_id},
        headers=headers,
        verify=verify_ssl,
        timeout=args.timeout,
    )
    verify_payload = ensure_success(verify_response, "Verify model hosts")
    linked_names = host_names_from_ci_list(verify_payload)
    missing_hosts = [host for host in hosts if host.lower() not in linked_names]

    result = {
        "uMapId": umap_id,
        "application_name": create_params.get("name"),
        "expected_hosts": hosts,
        "linked_hosts": sorted(linked_names),
        "missing_hosts": missing_hosts,
        "reused_existing_model": reused_existing_model,
        "populate_response": populate_payload,
    }
    print(json.dumps(result, indent=2))
    if missing_hosts:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
