# ocr-diagram

Multi-stage Python pipeline that extracts structured infrastructure topology from diagram images.

## Architecture

The pipeline is intentionally staged and does not send raw images to the LLM:

1. `diagram_parser/ocr/paddle_engine.py`
   Uses PaddleOCR to extract text spans with bounding boxes.
2. `diagram_parser/processing/grouping.py`
   Groups nearby OCR spans into candidate nodes using configurable distance thresholds.
3. `diagram_parser/processing/connections.py`
   Uses OpenCV line detection to infer node-to-node connections from image geometry.
4. `diagram_parser/llm/lmstudio_client.py`
   Sends only structured evidence to a local LLM server such as LM Studio or Ollama and requests strict JSON output.
5. `diagram_parser/processing/validation.py`
   Deterministically validates the LLM response against the supported topology schema.
6. `diagram_parser/output/writers.py`
   Saves topology JSON, Mermaid, and uControl model-create/retrieval JSON output.

## Install

This project currently supports Python 3.11, 3.12, and 3.13.

```bash
python3.11 --version  # Python 3.11, 3.12, or 3.13
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`paddleocr` also requires `paddlepaddle` at runtime. Python 3.14+ is not yet
supported by the current PaddlePaddle dependency stack.

## Run

```bash
python3 main.py /path/to/diagram.png --model your-lmstudio-model
python3 main.py /path/to/diagram.pdf --model your-lmstudio-model
```

Pass an application/model name for uControl output with `--application-name`.
When omitted, the application name defaults to `Application1`.

The LLM client accepts:

- LM Studio OpenAI-compatible URLs such as `http://127.0.0.1:1234/v1`
- Ollama OpenAI-compatible URLs such as `http://127.0.0.1:11434/v1`
- Ollama native API URLs such as `http://127.0.0.1:11434/api`

Example with Ollama:

```bash
python3 main.py /path/to/diagram.png \
  --model gemma3 \
  --base-url http://127.0.0.1:11434/api
```

Large local or remote models can take longer to answer the structured pipeline
prompt. The default request timeout is 300 seconds, and you can override it with
`--timeout-seconds`.

You can choose the execution mode:

```bash
python3 main.py /path/to/diagram.pdf --mode pipeline
python3 main.py /path/to/diagram.pdf --mode direct-llm --model your-vision-model
python3 main.py /path/to/diagram.pdf --mode both --model your-vision-model
```

If your local LLM server is not running yet, you can still generate provisional output from the deterministic stages only:

```bash
python3 main.py /path/to/diagram.pdf --skip-llm
```

By default, prompts include local uControl/BMC Discovery guidance from
`diagram_parser/llm/rag/ucontrol_asset_tag_schema.md`, and outputs include
`ucontrol_model_create.json` and `ucontrol_retrieval_requests.json`.
The model-create file is shaped as the POST body for
`/api/umap/model/create`; the retrieval file contains GET request descriptors
for `/api/asset/data/{definition}/{record-identifier}`. The tool does not
create or call the API.

Disable these independently when needed:

```bash
python3 main.py /path/to/diagram.pdf --no-ucontrol-rag
python3 main.py /path/to/diagram.pdf --no-ucontrol-asset-tags
```

Or keep the LLM stage enabled but fall back automatically when the configured LLM server is unreachable:

```bash
python3 main.py /path/to/diagram.pdf --model your-lmstudio-model --allow-llm-fallback
```

Some local servers reject strict structured-output settings. This client retries without them when necessary and otherwise relies on prompt-constrained JSON output.

For safer first passes on large PDFs, start with a lower raster scale and a single page:

```bash
python3 main.py /path/to/diagram.pdf \
  --model your-lmstudio-model \
  --pdf-scale 1.0 \
  --max-pages 1
```

Outputs are written to the project-local `output/` directory by default, split by mode:

- `output/pipeline/`
- `output/direct_llm/`

Each subdirectory may include:

- `topology.json`
- `ucontrol_model_create.json`
- `ucontrol_retrieval_requests.json`
- `topology.mmd`
- `topology.svg` when Mermaid CLI (`mmdc`) is available
- `structured_candidates.json`
- `ocr_spans.json`
- `llm_debug.json`

`ocr_spans.json` is reused automatically for the pipeline mode when the source path and OCR settings match, so you can tune grouping and connection logic without rerunning PaddleOCR every time. Use `--refresh-ocr-cache` to force a fresh OCR pass.

The `direct-llm` mode sends rendered page images to a multimodal endpoint. Use a vision-capable model there; text-only models will not work reliably.

If PaddleOCR cannot download models in your environment, pass local model directories:

```bash
python3 main.py /path/to/diagram.pdf \
  --model your-lmstudio-model \
  --ocr-det-model-dir /path/to/text_detection_model \
  --ocr-rec-model-dir /path/to/text_recognition_model \
  --ocr-cls-model-dir /path/to/textline_orientation_model
```

## Example

Illustrative example assets are in [`examples/`](../ocr-diagram/examples):

- Input sketch: [`simple_topology.svg`](../ocr-diagram/examples/simple_topology.svg)
- Expected JSON: [`expected_topology.json`](../ocr-diagram/examples/expected_topology.json)
- Expected Mermaid: [`expected_topology.mmd`](../ocr-diagram/examples/expected_topology.mmd)

# uControl uMap import guide

The pipeline writes two uControl-facing files when `--no-ucontrol-asset-tags`
is not used:

- `output/<mode>/ucontrol_model_create.json`: application/model metadata plus
  detected nodes and relationships.
- `output/<mode>/ucontrol_retrieval_requests.json`: per-node asset lookup
  descriptors such as `/api/asset/data/Host/name=ABCD12PD`.

The current uMap API docs show model creation as query-parameter based. Treat
`ucontrol_model_create.json` as the source of values for the create call, then
use the detected host names and CI definitions to populate the model.

The examples below assume:

```bash
export UCONTROL_BASE="https://your-ucontrol-host/uControl"
export COOKIE="JSESSIONID=...; serverTime=...; sessionExpiry=..."
```

## 1. Generate the uControl payloads

```bash
python3 main.py /path/to/diagram.pdf \
  --application-name "APP NAME" \
  --model your-lmstudio-model
```

Use `output/pipeline/ucontrol_model_create.json` unless you ran
`--mode direct-llm` or `--mode both`.

## 2. Find or create the uMap model

If the model may already exist, check by name first:

```bash
curl -sS "$UCONTROL_BASE/api/umap/model/details?name=APP%20NAME" \
  -b "$COOKIE"
```

The details response uses `data[0].uMapModelID`. If it returns the correct
model, keep that value as `uMapId` and skip creation.

To create a new model, call `/api/umap/model/create` with values from
`ucontrol_model_create.json`. URL-encode values that contain spaces.

```bash
curl -sS -X POST "$UCONTROL_BASE/api/umap/model/create\
?name=Name\
&description=Description\
&appID=APPID\
&modellingType=Standard\
&applicationType=Application%20Service\
  -b "$COOKIE"
```

Expected response shape:

```json
{
	"status_code": 201,
	"message": "Call processed without issue",
	"ok": true,
	"data": [
		{
			"uMapModelID": 67
		}
	],
	"dataItem": null
}
```

Save `data[0].uMapModelID`; the populate API calls this value `uMapId`.

For a metadata update to an existing model, the available docs show targeted
model APIs rather than a single full update endpoint. For example, rename the
model with:

```bash
curl -sS -X POST "$UCONTROL_BASE/api/umap/model/rename?id=588&name=New%20Model%20Name" \
  -b "$COOKIE"
```

## 3. Confirm IRE fields for the CI type

Before linking existing hosts, retrieve the IRE rules for the CI type you are
adding. For hosts:

```bash
curl -sS "$UCONTROL_BASE/api/umap/ire/data?kind=Host" -b "$COOKIE"
```

The captured API output for `BusinessApplicationInstance` returns rules such as
`name` and `name,version`; the uMap PDF example for `Host` shows `name` and
`name,serial`. Use the highest-priority rule for which this repo has values.
For OCR-extracted hosts that usually means `name`.

## 4. Optionally verify the existing assets

`ucontrol_retrieval_requests.json` contains lookup endpoints for each detected
asset. Example host lookup:

```bash
curl -sS "$UCONTROL_BASE/api/asset/data/Host/name=ABCD12PD" -b "$COOKIE"
```

Use this as a pre-flight check that the existing host can be found by the same
IRE field you will send to `/api/umap/populate/umap`. Depending on the asset
API response, the asset identifier may appear as `record_identifier`. After a
host is linked to the uMap model, `/api/umap/model/ci/list` returns the model
CI identifier as `ciId`.

## 5. Create a Host asset when it does not already exist

If the host lookup does not return an existing asset, create it with the uAsset
API before populating the uMap model. The uAsset docs show:

- Endpoint: `POST /api/asset/create`
- Content type: `application/json`
- Required body fields: `asset_kind` and `fields`
- Minimum `fields`: the IRE fields for that asset kind, for example `name` for
  a Host when using the `name` IRE rule

Minimal Host creation:

```bash
curl -sS -X POST "$UCONTROL_BASE/api/asset/create" \
  -H "Content-Type: application/json" \
  -b "$COOKIE" \
  --data '{
    "asset_kind": "Host",
    "fields": {
      "name": "BDHW8KW3"
    }
  }'
```

Expected response shape:

```json
{
  "status_code": 201,
  "message": "Call processed without issue",
  "ok": true,
  "data": [
    {
      "record_identifier": 47038
    }
  ],
  "dataItem": null
}
```

Save `data[0].record_identifier` if later asset-level updates are needed.
Then re-run the lookup to confirm the Host is retrievable by name:

```bash
curl -sS "$UCONTROL_BASE/api/asset/data/Host/name=ABCD12PD" -b "$COOKIE"
```

Optional fields can be added at creation time if you have them:

```bash
curl -sS -X POST "$UCONTROL_BASE/api/asset/create" \
  -H "Content-Type: application/json" \
  -b "$COOKIE" \
  --data '{
    "asset_kind": "Host",
    "fields": {
      "name": "ABCD12PD",
      "short_name": "ABCD12PD",
      "description": "Host detected from APP NAME"
    },
    "location": {
      "locationValue": "London - London, City of - GB"
    },
    "business_unit": {
      "businessUnitValue": "HR"
    },
    "tag_fields": [
      {
        "source": "ocr-diagram"
      }
    ]
  }'
```

If the Host IRE rule includes multiple fields, include all required values under
`fields`. For example, if `/api/umap/ire/data?kind=Host` returns `name,serial`
as the rule you intend to use:

```json
{
  "asset_kind": "Host",
  "fields": {
    "name": "ABCD12PD",
    "serial": "SERIAL-12345"
  }
}
```

## 6. Add existing hosts to the model

Use `/api/umap/populate/umap` with one item per host. The docs show
`Content-Type: text/plain` with a JSON string body.

```bash
curl -sS -X POST "$UCONTROL_BASE/api/umap/populate/umap" \
  -H "Content-Type: text/plain" \
  -b "$COOKIE" \
  --data '{
    "data": [
      {
        "ciType": "Host",
        "uMapId": "588",
        "environmentId": "1",
        "name": "ABCD12PD"
      },
      {
        "ciType": "Host",
        "uMapId": "588",
        "environmentId": "1",
        "name": "ABCD12PD"
      },
      {
        "ciType": "Host",
        "uMapId": "588",
        "environmentId": "1",
        "name": "ABCD12PD"
      }
    ]
  }'
```

Field mapping:

- `ciType`: uControl schema definition, for example `Host`, `NetworkDevice`,
  `SoftwareInstance`, or `Database`.
- `uMapId`: `uMapModelID` from create/details.
- `environmentId`: from `/api/umap/environment/data`; `1` is `Not Set` in the
  captured output.
- IRE fields: name/value fields from `/api/umap/ire/data?kind=<ciType>`, for
  example `"name": "BDHW8KW3"` for a host.

## 7. Verify the model contents

List the CIs now linked to the model:

```bash
curl -sS "$UCONTROL_BASE/api/umap/model/ci/list?uMapId=588&kind=Host" \
  -b "$COOKIE"
```

The response includes records such as:

```json
{
  "valid": true,
  "cloud": false,
  "environment": "Production",
  "component": "Not Set",
  "uMapId": 588,
  "kind": "Host",
  "name": "ABCD12PD",
  "uMapName": "APP NAME",
  "ciId": 306
}
```

If a host was added incorrectly, unlink it by `ciId`:

```bash
curl -sS -X POST "$UCONTROL_BASE/api/umap/model/ci/unlink?ciIds=306&kind=Host" \
  -b "$COOKIE"
```
