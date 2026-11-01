# Document formats and OCR

Document ingestion turns company files into versioned text snapshots with source locations. The
decision compiler then extracts rules from those snapshots, creates a review draft, and builds
decision examples after business review. OCR text is a transcription to review, not a verified policy.

## Format support

| Input | Adapter | Preserved locations | Scope |
| --- | --- | --- | --- |
| TXT, Markdown | `text` | Line and Markdown heading | UTF-8, including BOM |
| JSON, YAML | `workflow` | JSON pointer | Objects/arrays; leaf values normalized to text |
| DOCX | `docx` | Body paragraph, table row/column, header/footer section | Native text and nested tables |
| XLSX | `xlsx` | Sheet and cell | Cell values; formulas retained as explicitly unevaluated text |
| PPTX | `pptx` | Slide, shape, paragraph, table cell, notes | Native text, tables, grouped shapes and speaker notes |
| HTML, HTM | `html` | Parsed text-node index and tag | Static text; scripts/styles/hidden attributes excluded; no network requests or script execution |
| CSV, TSV | `csv` | Logical row and column | UTF-8; quoted fields and embedded newlines |
| PDF | `pdf` | Page; OCR line and box when available | Native text, or configured page OCR |
| PNG, JPEG, TIFF, BMP, WebP | `image` | Frame; OCR line and box when available | Requires explicit OCR configuration; multi-frame TIFF supported |

`adapter: auto` is the default and selects from the file extension. Existing explicit adapter values
remain valid. An explicit `text` adapter can still ingest textual JSON/YAML without normalization.
Legacy DOC/XLS/PPT, ODT/ODS/ODP and RTF require conversion to a supported format. Embedded images,
charts, SmartArt, DOCX drawing text, tracked revisions and attachment content are outside the native
office parsers' scope. Export those visual elements as images/PDF pages for OCR. HTML parsing does
not evaluate CSS visibility or reproduce a browser layout. Spreadsheet formulas are never executed.

```bash
uv sync --group dev --extra documents --extra ocr-rapid
uv run --no-sync python scripts/create_document_fixtures.py --output runs/document-fixtures
uv run --no-sync decision-forge ingest runs/document-fixtures/corpus.yaml --output runs/documents.json
```

The example generator writes only fictional policy material. The core pipeline remains usable
without these optional dependencies.

## Local lightweight OCR

`ocr-rapid` installs RapidOCR and ONNX Runtime, plus Pillow and the PDFium renderer. The locked
RapidOCR package includes PP-OCRv6 small detection/recognition models. Ingestion resolves those
local files explicitly and never downloads missing weights. Missing packages or weights produce
an actionable error. Model bytes and package versions participate in the cache identity.

```yaml
schema_version: '1.0'
sources:
  - id: scanned-policy
    path: policy.pdf
    version: '2026-11'
    adapter: auto
    access:
      classification: confidential
      allowed_roles: [operations]
    ocr:
      backend: rapidocr
      mode: auto
      dpi: 144
      max_pages: 100
      min_confidence: 0.85
```

For PDF, `auto` uses native text on pages with extractable text and OCR on pages without it. A page
containing both text and image-only content needs `mode: always` to OCR the whole rendered page.
`always` also handles an incorrect hidden text layer. Image sources always run OCR. Pages with no
recognized text are rejected explicitly; there is no silent page dropping.

To use custom RapidOCR-compatible ONNX models, configure both `det_model` and `rec_model` paths.
Optionally provide `rec_keys` for a local recognition dictionary; otherwise the recognition model
must embed its character dictionary. Paths resolve relative to the manifest directory (absolute
model paths are also accepted). Custom model input/output and preprocessing requirements must match
the installed RapidOCR runtime. Only the bundled PP-OCRv6 small pair has a recorded inference smoke
test in this repository; custom weight compatibility is not inferred from a filename.

The `tesseract` backend uses a separately installed Tesseract executable and explicit local language
data. Install the `ocr` extra for image rendering (plus `documents` for PDF inputs), and configure:

```yaml
ocr:
  backend: tesseract
  tessdata_dir: ./models/tessdata
  language: eng+chi_sim
  timeout_seconds: 120
```

The selected `.traineddata` files are hashed for provenance. The command runs without a shell and
with a timeout. TSV words are grouped by text line, retaining the minimum word confidence and union
bounding box. CI tests its invocation and TSV parsing contract; Tesseract recognition quality is
not measured by the scripted command fixture.

## Small vision OCR models served through an API

The `openai` backend sends a PNG data URL to an explicit `/chat/completions` endpoint. It supports
the standard image-plus-text request contract used by compatible local model servers. Configure
the model name and prompt required by the server, for example a PaddleOCR-VL deployment:

```yaml
ocr:
  backend: openai
  base_url: http://127.0.0.1:8118/v1
  model: PaddlePaddle/PaddleOCR-VL-1.6
  revision: deployment-v1
  prompt: 'OCR:'
  max_output_tokens: 8192
  timeout_seconds: 120
  max_pages: 50
```

```bash
# Optional authentication is read from FORGE_OCR_API_KEY, never from the manifest.
uv run decision-forge ingest ./private/corpus.yaml --output runs/ocr-corpus.json --allow-external-ocr
uv run decision-forge compile-corpus ./private/corpus.yaml --output runs/compiled \
  --base-url https://your-compiler.example/v1 --model your-compiler-model --allow-external-ocr
```

The flag is required even for localhost; merely opening a manifest cannot authorize transmission.
Remote hosts require HTTPS. Redirects and automatic retries are disabled. Endpoints serving
DeepSeek-OCR or other vision OCR models can use the same adapter with their own model ID and prompt.
This is protocol compatibility: the local HTTP contract is tested, while actual PaddleOCR-VL and
DeepSeek server inference are not claimed as validated here. Follow each model's serving instructions
and update `revision` when changing deployed weights under the same model name.

Generative OCR returns no invented confidence or boxes. Truncated, empty, or malformed completions
fail explicitly. The compiler endpoint and OCR endpoint are independently selected; OCR usage does
not count toward `compile-corpus --max-calls` or its token budget.

## Provenance, recovery and limits

- Every document retains the raw-file hash, normalized-text hash, source version and access metadata.
  Office/PDF/HTML parser versions are recorded. OCR configuration, model hashes and runtime versions
  are stored in `extraction`; spans carry `method: ocr`, the backend, frame/page, and any real confidence
  and bounding box. Boxes use rendered-image pixels after orientation correction.
- Source offsets index the normalized transcription, not bytes in the original binary. Quotation
  verification proves consistency with that transcription; business reviewers must check OCR-sensitive
  numbers, negations and permissions against the original image. Compiler review gates remain active.
- `.forge-ocr-cache` stores private, hash-checked page results and attempt journals. Completed pages
  are reused; changed image pixels, model weights, settings or declared revisions invalidate reuse.
  Failed/interrupted calls retain an attempt record and require an explicit rerun. A response lost
  before caching may need another request. Never commit the cache or private source snapshots.
- Limits: 2 MB raw and normalized text per source; 32 MB raw source corpus; 500 PDF pages; 100 OCR
  pages/frames per source by default; 16 million pixels per rendered frame by default; 16 MB encoded
  PNG per request. `max_pages` limits OCR calls per source invocation, not a persistent monetary budget.
  Cached pages make no inference call. Responses are limited to 8 MB and normalized output to 2 MB.
- OOXML archives are checked before parsing: at most 5,000 members and 50 MB expanded contents.
  Spreadsheet traversal is capped at 200,000 cells, presentations at 500 slides, and normalized
  fragments at 100,000. Encrypted documents require decryption first. These are resource controls,
  not a sandbox for hostile native-parser inputs.
- `timeout_seconds` controls Tesseract execution and HTTP I/O timeouts. In-process RapidOCR and
  native document parsing have no hard wall-clock timeout; use a worker process for deployment-level
  deadlines.
- `min_confidence` rejects the entire source on a lower-confidence recognized unit; it never quietly
  deletes a low-confidence word. The threshold does not detect text that the detector missed.

## Upstream interfaces

- [RapidOCR Python usage](https://rapidai.github.io/RapidOCRDocs/main/quickstart/)
- [PaddleOCR-VL serving](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL.html)
- [DeepSeek-OCR](https://github.com/deepseek-ai/DeepSeek-OCR)
- [PDFium Python resource lifecycle](https://pypdfium2.readthedocs.io/en/stable/python_api.html)
