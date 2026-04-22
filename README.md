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

You can choose the execution mode:

```bash
python3 main.py /path/to/diagram.pdf --mode pipeline
python3 main.py /path/to/diagram.pdf --mode direct-llm --model your-vision-model
python3 main.py /path/to/diagram.pdf --mode both --model your-vision-model
```

If LMStudio is not running yet, you can still generate provisional output from the deterministic stages only:

```bash
python3 main.py /path/to/diagram.pdf --skip-llm
```

Or keep the LLM stage enabled but fall back automatically when LMStudio is unreachable:

```bash
python3 main.py /path/to/diagram.pdf --model your-lmstudio-model --allow-llm-fallback
```

Some OpenAI-compatible local servers reject `response_format`. This client disables that field by default and relies on prompt-constrained JSON output instead.

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
- `topology.mmd`
- `structured_candidates.json`
- `ocr_spans.json`
- `llm_debug.json`

`ocr_spans.json` is reused automatically for the pipeline mode when the source path and OCR settings match, so you can tune grouping and connection logic without rerunning PaddleOCR every time. Use `--refresh-ocr-cache` to force a fresh OCR pass.

The `direct-llm` mode sends rendered page images to an OpenAI-compatible multimodal endpoint. Use a vision-capable model there; text-only models will not work reliably.

If PaddleOCR cannot download models in your environment, pass local model directories:

```bash
python3 main.py /path/to/diagram.pdf \
  --model your-lmstudio-model \
  --ocr-det-model-dir /path/to/text_detection_model \
  --ocr-rec-model-dir /path/to/text_recognition_model \
  --ocr-cls-model-dir /path/to/textline_orientation_model
```

## Example

Illustrative example assets are in [`examples/`](/Volumes/Hub/Code/GitHub/ocr-diagram/examples):

- Input sketch: [`simple_topology.svg`](/Volumes/Hub/Code/GitHub/ocr-diagram/examples/simple_topology.svg)
- Expected JSON: [`expected_topology.json`](/Volumes/Hub/Code/GitHub/ocr-diagram/examples/expected_topology.json)
- Expected Mermaid: [`expected_topology.mmd`](/Volumes/Hub/Code/GitHub/ocr-diagram/examples/expected_topology.mmd)
