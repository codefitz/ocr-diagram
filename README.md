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
When omitted, the pipeline attempts to infer the application name from extracted
labels and their positions. Pass `--app-id` to set the uControl application ID;
when omitted, it defaults to `<application-name>01`.

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
`ucontrol_model_create.json`, `ucontrol_populate_umap.json`, and
`ucontrol_retrieval_requests.json`. The model-create file is a request
descriptor for `/api/umap/model/create`, which takes query parameters rather
than a JSON body; the populate file is shaped as the manual POST body for
`/api/umap/populate/umap`; the retrieval file contains GET request descriptors
for `/api/asset/data/{definition}` with a name filter. The tool does not create
or call the API.

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
- `ucontrol_populate_umap.json`
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

The pipeline writes three uControl-facing files when `--no-ucontrol-asset-tags`
is not used:

- `output/<mode>/ucontrol_model_create.json`: application/model metadata plus
  copyable query strings and Params for manual model creation.
- `output/<mode>/ucontrol_populate_umap.json`: Host mappings for manual
  `/api/umap/populate/umap` testing.
- `output/<mode>/ucontrol_retrieval_requests.json`: per-node asset lookup
  descriptors such as `/api/asset/data/Host?filter=asset.nameEQUALS%27ABCD12PD%27`.

The current uMap API docs show model creation as query-parameter based. Use
`ucontrol_model_create.json` for the create URL or for Params in Insomnia, then
use the detected host names and CI definitions to populate the model.

The examples below assume:

```bash
export UCONTROL_BASE="https://your-ucontrol-host/uControl"
export COOKIE="JSESSIONID=...; serverTime=...; sessionExpiry=..."
```

## Using Postman or Insomnia

The curl examples translate directly into Postman or Insomnia requests:

1. Create an environment with:
   - `UCONTROL_BASE`: `https://your-ucontrol-host/uControl`
   - `COOKIE`: `JSESSIONID=...; serverTime=...; sessionExpiry=...`
   - Optional values such as `UMAP_ID`, `HOST_NAME`, and `ENVIRONMENT_ID`
2. For every request, add a `Cookie` header:

```text
Cookie: {{COOKIE}}
```

3. Use query parameters in the client Params tab instead of hand-encoding the
   full URL. Postman and Insomnia will encode spaces and special characters.
4. For JSON body APIs, select `Body` -> `raw` -> `JSON` in Postman, or
   `Body` -> `JSON` in Insomnia.
5. For `/api/umap/populate/umap`, set the body to raw text or JSON text and set
   this header exactly as shown in the docs:

```text
Content-Type: text/plain
```

Common request setup:

| Purpose | Method | URL | Params | Body |
| --- | --- | --- | --- | --- |
| Find model | `GET` | `{{UCONTROL_BASE}}/api/umap/model/details` | `name=APP NAME` | none |
| Create model | `POST` | `{{UCONTROL_BASE}}/api/umap/model/create` | `name`, `description`, `appID`, `modellingType`, `applicationType` | none |
| Get Host IRE rules | `GET` | `{{UCONTROL_BASE}}/api/umap/ire/data` | `kind=Host` | none |
| Check Host asset by name | `GET` | `{{UCONTROL_BASE}}/api/asset/data/Host` | `filter=asset.nameEQUALS'{{HOST_NAME}}'` | none |
| Check Host asset by created ID | `GET` | `{{UCONTROL_BASE}}/api/asset/data/Host` | `filter=asset.record_identifierEQUALS{{HOST_RECORD_IDENTIFIER}}` | none |
| Create Host asset | `POST` | `{{UCONTROL_BASE}}/api/asset/create` | none | JSON |
| Populate uMap | `POST` | `{{UCONTROL_BASE}}/api/umap/populate/umap` | none | raw text JSON |
| List model Hosts | `GET` | `{{UCONTROL_BASE}}/api/umap/model/ci/list` | `uMapId={{UMAP_ID}}`, `kind=Host` | none |
| Unlink Host | `POST` | `{{UCONTROL_BASE}}/api/umap/model/ci/unlink` | `ciIds=306`, `kind=Host` | none |

Example Postman/Insomnia body for creating a Host asset:

```json
{
  "asset_kind": "Host",
  "fields": {
    "name": "{{HOST_NAME}}"
  }
}
```

Example Postman/Insomnia body for populating a uMap model:

```json
{
  "data": [
    {
      "ciType": "Host",
      "uMapId": "{{UMAP_ID}}",
      "environmentId": "{{ENVIRONMENT_ID}}",
      "name": "{{HOST_NAME}}"
    }
  ]
}
```

## 1. Generate the uControl payloads

```bash
python3 main.py /path/to/diagram.pdf \
  --application-name "APP NAME" \
  --app-id "APPID" \
  --model your-lmstudio-model
```

Use `output/pipeline/ucontrol_model_create.json` unless you ran
`--mode direct-llm` or `--mode both`. If `--application-name` is omitted, the
pipeline attempts to infer it from prominent extracted labels near the top of
the diagram, with application/software labels preferred. If `--app-id` is
omitted, the generated `appID` defaults to `<application-name>01`.

## 2. Find or create the uMap model

If the model may already exist, check by name first:

```bash
curl -sS "$UCONTROL_BASE/api/umap/model/details?name=APP%20NAME" \
  -b "$COOKIE"
```

The details response uses `data[0].uMapModelID`. If it returns the correct
model, keep that value as `uMapId` and skip creation.

The generated `ucontrol_model_create.json` is a manual request descriptor, not
a JSON body. It contains copyable URL strings and a Params-friendly list:

```json
{
  "method": "POST",
  "endpoint": "/api/umap/model/create",
  "query_string": "name=APP%20NAME&description=APP%20NAME%20application&appID=APPID&modellingType=Standard&applicationType=Application%20Service",
  "url": "/api/umap/model/create?name=APP%20NAME&description=APP%20NAME%20application&appID=APPID&modellingType=Standard&applicationType=Application%20Service",
  "insomnia_url": "{{UCONTROL_BASE}}/api/umap/model/create?name=APP%20NAME&description=APP%20NAME%20application&appID=APPID&modellingType=Standard&applicationType=Application%20Service",
  "params": [
    {
      "name": "name",
      "value": "APP NAME"
    },
    {
      "name": "description",
      "value": "APP NAME application"
    },
    {
      "name": "appID",
      "value": "APPID"
    },
    {
      "name": "modellingType",
      "value": "Standard"
    },
    {
      "name": "applicationType",
      "value": "Application Service"
    }
  ]
}
```

In Insomnia, create a `POST` request using `insomnia_url`, or use
`{{UCONTROL_BASE}}/api/umap/model/create` as the URL and copy the generated
`params` entries into the Query tab. Do not put this payload in the request
body.

To create a new model with curl, use the generated `url` or `curl` string.
The mandatory parameters are `name`, `description`, `appID`,
`modellingType=Standard`, and `applicationType=Application Service`.

```bash
curl -sS -X POST "$UCONTROL_BASE/api/umap/model/create\
?name=Name\
&description=Description\
&appID=APPID\
&modellingType=Standard\
&applicationType=Application%20Service" \
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
asset. To look up a Host by name, use the asset API `filter` query parameter:

```bash
curl -sS --get "$UCONTROL_BASE/api/asset/data/Host" \
  --data-urlencode "filter=asset.nameEQUALS'ABCD12PD'" \
  -b "$COOKIE"
```

Use this as a pre-flight check that the existing host can be found by the same
IRE field you will send to `/api/umap/populate/umap`. Do not use
`/api/asset/data/Host/name=ABCD12PD` for name lookup; the path value is a
`record_identifier`, and some uControl instances treat an invalid path value as
a broad Host list.

If you already have the numeric `record_identifier` from `/api/asset/create`,
use a filtered list query to confirm it. On some uControl instances the
documented `/api/asset/data/Host/{record_identifier}` path still returns a broad
list, so prefer the filter form:

```bash
curl -sS --get "$UCONTROL_BASE/api/asset/data/Host" \
  --data-urlencode "filter=asset.record_identifierEQUALS1395" \
  -b "$COOKIE"
```

After a host is linked to the uMap model, `/api/umap/model/ci/list` returns the
model CI identifier as `ciId`.

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
      "name": "ABCD12PD"
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

Save `data[0].record_identifier` if later asset-level updates are needed. The
create response is the primary confirmation that the asset was created. For the
example above, verify the created ID with:

```bash
curl -sS --get "$UCONTROL_BASE/api/asset/data/Host" \
  --data-urlencode "filter=asset.record_identifierEQUALS47038" \
  -b "$COOKIE"
```

Then re-run the filtered lookup to confirm the Host is retrievable by name:

```bash
curl -sS --get "$UCONTROL_BASE/api/asset/data/Host" \
  --data-urlencode "filter=asset.nameEQUALS'ABCD12PD'" \
  -b "$COOKIE"
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

The generated `ucontrol_populate_umap.json` contains one item per extracted
Host, with a placeholder `uMapId` to replace after model creation or lookup:

```json
{
  "data": [
    {
      "ciType": "Host",
      "uMapId": "<uMapId>",
      "name": "ABCD12PD"
    }
  ]
}
```

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
