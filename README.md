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
   Sends only structured evidence to LMStudio and requests strict JSON output.
5. `diagram_parser/processing/validation.py`
   Deterministically validates the LLM response against the supported topology schema.
6. `diagram_parser/output/writers.py`
   Saves JSON and Mermaid output.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python3 main.py /path/to/diagram.png --model your-lmstudio-model
```

Outputs are written to `output/` next to the input image by default:

- `topology.json`
- `topology.mmd`
- `structured_candidates.json`

## Example

Illustrative example assets are in [`examples/`](/Volumes/Hub/Code/GitHub/ocr-diagram/examples):

- Input sketch: [`simple_topology.svg`](/Volumes/Hub/Code/GitHub/ocr-diagram/examples/simple_topology.svg)
- Expected JSON: [`expected_topology.json`](/Volumes/Hub/Code/GitHub/ocr-diagram/examples/expected_topology.json)
- Expected Mermaid: [`expected_topology.mmd`](/Volumes/Hub/Code/GitHub/ocr-diagram/examples/expected_topology.mmd)
