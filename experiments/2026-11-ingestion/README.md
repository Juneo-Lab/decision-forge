# Native document and local OCR smoke test

On November 1, 2026, the seven-format fictional example completed on macOS ARM64 with Python 3.12.
DOCX, XLSX, PPTX, HTML, CSV, PNG and scanned PDF each produced a versioned source snapshot with
locations. The PNG and scanned PDF both transcribed the exact sentence:

> Only managers approve refunds above 100 dollars.

The local RapidOCR 3.10.0 runtime used its bundled PP-OCRv6 small detection and recognition models
through ONNX Runtime. [results.json](results.json) records extracted text, locations, real confidence
scores, model hashes and parser/runtime versions. No model service was called. These are functional
smoke fixtures; they do not measure accuracy on real organizational documents or difficult layouts.

Reproduce from the repository root, choosing a fresh output directory:

```bash
uv sync --group dev --extra documents --extra ocr-rapid
uv run --no-sync python scripts/create_document_fixtures.py --output runs/document-fixtures
uv run --no-sync decision-forge ingest runs/document-fixtures/corpus.yaml --output runs/documents.json
uv run --no-sync pytest tests/test_document_formats.py tests/test_ocr.py
```

The generator uses Pillow's bundled font and fictional text. Office container timestamps may produce
different binary hashes across runs. Compare normalized text and source locations. The OCR test
blocks model downloading and performs real local inference; other tests explicitly use scripted
OCR and a loopback HTTP server to verify failure, cache, authorization and service contracts.
Tesseract and remote vision models have contract tests, with no model-quality claim from this run.
