"""Explicit OCR backends with private, integrity-checked page caches."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import math
import os
import shutil
import subprocess
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from filelock import FileLock
from pydantic import Field, model_validator

from decision_forge.document_formats import Fragment, dependency
from decision_forge.io import MAX_SOURCE_BYTES, digest, read_json, write_json
from decision_forge.models import Contract


class OCRConfig(Contract):
    backend: Literal["rapidocr", "tesseract", "openai"]
    mode: Literal["auto", "always"] = "auto"
    dpi: int = Field(default=144, ge=72, le=300)
    max_pages: int = Field(default=100, ge=1, le=500)
    max_pixels: int = Field(default=16_000_000, ge=1000, le=40_000_000)
    min_confidence: float = Field(default=0, ge=0, le=1)
    timeout_seconds: float = Field(default=120, gt=0, le=600)
    det_model: str | None = None
    rec_model: str | None = None
    rec_keys: str | None = None
    tessdata_dir: str | None = None
    language: str = "eng"
    base_url: str | None = None
    model: str | None = None
    revision: str = "unspecified"
    max_output_tokens: int = Field(default=8192, ge=1, le=32768)
    prompt: str = Field(
        default="Read all visible text in reading order. Return only the transcription.", max_length=4000
    )

    @model_validator(mode="after")
    def configuration(self) -> OCRConfig:
        if self.backend == "rapidocr" and bool(self.det_model) != bool(self.rec_model):
            raise ValueError("Configure both det_model and rec_model, or use the bundled RapidOCR models")
        if self.backend == "tesseract" and (
            not self.tessdata_dir
            or not self.language
            or any(not part.replace("_", "").isalnum() for part in self.language.split("+"))
        ):
            raise ValueError("Tesseract requires a tessdata_dir and language codes joined by '+'")
        if self.backend == "openai":
            if not self.base_url or not self.model:
                raise ValueError("OCR service requires an explicit endpoint and model")
            parsed = urlparse(self.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("OCR endpoint must use HTTP(S)")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("OCR endpoint must not contain credentials, query parameters or fragments")
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Remote OCR endpoints require HTTPS")
            if self.min_confidence:
                raise ValueError("Generative OCR does not provide calibrated confidence; use business review")
        return self


class OCRLine(Contract):
    text: str = Field(min_length=1, max_length=MAX_SOURCE_BYTES)
    confidence: float | None = Field(default=None, ge=0, le=1)
    box: list[float] | None = Field(default=None, min_length=4, max_length=4)

    @model_validator(mode="after")
    def coordinates(self) -> OCRLine:
        if self.box and (
            any(not math.isfinite(v) or v < 0 for v in self.box)
            or self.box[2] < self.box[0]
            or self.box[3] < self.box[1]
        ):
            raise ValueError("Invalid OCR bounding box")
        return self


class OCRRunner:
    def __init__(self, config: OCRConfig, root: Path, *, allow_external: bool = False):
        if config.backend == "openai" and not allow_external:
            raise ValueError("OCR service calls require --allow-external-ocr (including localhost)")
        self.config = config
        self.root = root
        self.cache = root / ".forge-ocr-cache"
        self.engine: Any = None
        self.calls = 0
        self.paths: dict[str, str] = {}
        self.last_usage: dict[str, Any] | None = None
        models = {}
        if config.backend == "rapidocr" and config.det_model is None:
            rapid = dependency("rapidocr", "ocr-rapid")
            bundled = Path(rapid.__file__).parent / "models"
            self.paths.update(
                det_model=str(bundled / "PP-OCRv6_det_small.onnx"), rec_model=str(bundled / "PP-OCRv6_rec_small.onnx")
            )
        for key in ("det_model", "rec_model", "rec_keys"):
            value = getattr(config, key) or self.paths.get(key)
            if value:
                path = (root / value).resolve()
                if not path.is_file():
                    raise ValueError(f"Local OCR model file missing: {key}")
                self.paths[key] = str(path)
                models[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        dependency("PIL.Image", "ocr")
        packages = {name: version(name) for name in ("Pillow",)}
        if config.backend == "rapidocr":
            dependency("rapidocr", "ocr-rapid")
            packages.update({name: version(name) for name in ("rapidocr", "onnxruntime")})
            if "rec_keys" not in self.paths:
                ort = dependency("onnxruntime", "ocr-rapid")
                settings = ort.SessionOptions()
                settings.intra_op_num_threads = settings.inter_op_num_threads = 1
                session = ort.InferenceSession(
                    self.paths["rec_model"], sess_options=settings, providers=["CPUExecutionProvider"]
                )
                if not session.get_modelmeta().custom_metadata_map.get("character"):
                    raise ValueError("Recognition model needs embedded characters or an explicit local rec_keys file")
        if config.backend == "tesseract":
            self.binary = shutil.which("tesseract") or ""
            if not self.binary:
                raise ValueError("Install the Tesseract executable before selecting this backend")
            tessdata = (root / str(config.tessdata_dir)).resolve()
            self.paths["tessdata_dir"] = str(tessdata)
            for language in config.language.split("+"):
                path = tessdata / f"{language}.traineddata"
                if not path.is_file():
                    raise ValueError("Tesseract language data missing from the configured tessdata_dir")
                models[language] = hashlib.sha256(path.read_bytes()).hexdigest()
            packages["tesseract-binary"] = hashlib.sha256(Path(self.binary).read_bytes()).hexdigest()
        self.identity = {
            "protocol": "ocr-pages-v1",
            "config": config.model_dump(),
            "models": models,
            "packages": packages,
        }

    def recognize(self, png: bytes, locator: str) -> list[Fragment]:
        if len(png) > 16_000_000:
            raise ValueError("Rendered OCR image exceeds the 16 MB request limit")
        key = digest({"image": hashlib.sha256(png).hexdigest(), "engine": self.identity})
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.cache / f"{key}.json"
        with FileLock(str(path) + ".lock", timeout=0):
            if path.exists():
                if path.stat().st_size > 4 * MAX_SOURCE_BYTES:
                    raise ValueError("Oversized OCR cache entry")
                cached = read_json(path)
                if cached.get("key") != key or cached.get("digest") != digest(cached.get("result")):
                    raise ValueError("OCR cache integrity check failed")
                lines = [OCRLine.model_validate(line) for line in cached["result"]]
            else:
                if self.calls >= self.config.max_pages:
                    raise ValueError("OCR page-call limit exhausted")
                self.calls += 1
                event = self.cache / f"{key}.attempt.json"
                if event.exists() and event.stat().st_size > 4 * MAX_SOURCE_BYTES:
                    raise ValueError("Oversized OCR attempt journal")
                attempts = read_json(event) if event.exists() else []
                attempts.append({"key": key, "attempt": len(attempts) + 1, "status": "reserved", "usage": None})
                write_json(event, attempts)
                self.last_usage = None
                try:
                    lines, usage = self._recognize(png)
                    self._check(lines)
                except Exception:
                    attempts[-1].update(status="failed", usage=self.last_usage)
                    write_json(event, attempts)
                    raise
                body = [line.model_dump() for line in lines]
                write_json(path, {"key": key, "result": body, "digest": digest(body)})
                attempts[-1].update(status="completed", usage=usage)
                write_json(event, attempts)
        self._check(lines)
        return [
            Fragment(line.text, f"{locator};ocr:{self.config.backend};line:{index}", "ocr", line.confidence, line.box)
            for index, line in enumerate(lines, 1)
        ]

    def _check(self, lines: list[OCRLine]) -> None:
        if not lines or not any(line.text.strip() for line in lines):
            raise ValueError("OCR found no text; provide a transcription for blank or unreadable pages")
        if len(lines) > 100_000 or sum(len(line.text.encode()) + 1 for line in lines) > MAX_SOURCE_BYTES:
            raise ValueError("OCR output exceeds the source text limit")
        if any(line.confidence is not None and line.confidence < self.config.min_confidence for line in lines):
            raise ValueError("OCR confidence is below the configured threshold; review the source before retrying")

    def _recognize(self, png: bytes) -> tuple[list[OCRLine], dict[str, Any] | None]:
        if self.config.backend == "openai":
            return self._remote(png)
        if self.config.backend == "tesseract":
            with tempfile.TemporaryDirectory(prefix="forge-ocr-") as folder:
                image = Path(folder) / "page.png"
                image.write_bytes(png)
                try:
                    result = subprocess.run(
                        [
                            self.binary,
                            str(image),
                            "stdout",
                            "--tessdata-dir",
                            self.paths["tessdata_dir"],
                            "-l",
                            self.config.language,
                            "tsv",
                        ],
                        capture_output=True,
                        timeout=self.config.timeout_seconds,
                        check=True,
                    )
                except (subprocess.SubprocessError, OSError):
                    raise ValueError("Tesseract OCR failed or timed out") from None
            grouped: dict[tuple[str, ...], list[OCRLine]] = {}
            for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"):
                if not row.get("text", "").strip() or float(row["conf"]) < 0:
                    continue
                left, top, width, height = (float(row[k]) for k in ("left", "top", "width", "height"))
                key = tuple(row[k] for k in ("page_num", "block_num", "par_num", "line_num"))
                grouped.setdefault(key, []).append(
                    OCRLine(
                        text=row["text"],
                        confidence=float(row["conf"]) / 100,
                        box=[left, top, left + width, top + height],
                    )
                )
            lines = []
            for words in grouped.values():
                boxes = [word.box for word in words if word.box is not None]
                lines.append(
                    OCRLine(
                        text=" ".join(word.text for word in words),
                        confidence=min(word.confidence for word in words if word.confidence is not None),
                        box=[
                            min(b[0] for b in boxes),
                            min(b[1] for b in boxes),
                            max(b[2] for b in boxes),
                            max(b[3] for b in boxes),
                        ],
                    )
                )
            return lines, None
        rapidocr = dependency("rapidocr", "ocr-rapid")
        if self.engine is None:
            self.engine = rapidocr.RapidOCR(
                params={
                    "Det.model_path": self.paths["det_model"],
                    "Rec.model_path": self.paths["rec_model"],
                    "Rec.rec_keys_path": self.paths.get("rec_keys"),
                    "Global.use_cls": False,
                    "Global.text_score": 0.0,
                    "EngineConfig.onnxruntime.intra_op_num_threads": 1,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                }
            )
        result = self.engine(png, use_cls=False)
        if result.txts is None:
            return [], None
        lines = []
        for text, score, box in zip(result.txts, result.scores, result.boxes, strict=True):
            xs, ys = [float(point[0]) for point in box], [float(point[1]) for point in box]
            lines.append(OCRLine(text=text, confidence=float(score), box=[min(xs), min(ys), max(xs), max(ys)]))
        return lines, None

    def _remote(self, png: bytes) -> tuple[list[OCRLine], dict[str, Any] | None]:
        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()},
                        },
                        {"type": "text", "text": self.config.prompt},
                    ],
                }
            ],
        }
        key = os.environ.get("FORGE_OCR_API_KEY", "")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            with (
                httpx.Client(timeout=self.config.timeout_seconds, follow_redirects=False, trust_env=False) as client,
                client.stream(
                    "POST", str(self.config.base_url).rstrip("/") + "/chat/completions", json=payload, headers=headers
                ) as response,
            ):
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 4 * MAX_SOURCE_BYTES:
                        raise ValueError("OCR response exceeds the size limit")
            import json

            body = json.loads(data)
            usage = body.get("usage")
            safe_usage = (
                {
                    k: usage[k]
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if isinstance(usage.get(k), int)
                }
                if isinstance(usage, dict)
                else None
            )
            self.last_usage = safe_usage
            choice = body["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("OCR response is incomplete; adjust the explicit output limit")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("OCR service returned no transcription")
            return [OCRLine(text=content)], safe_usage
        except (httpx.HTTPError, KeyError, IndexError, TypeError):
            raise ValueError("OCR service request failed or returned an invalid response") from None


def image_png(image: Any, config: OCRConfig) -> bytes:
    if image.width * image.height > config.max_pixels:
        raise ValueError("OCR image exceeds the configured pixel limit")
    output = io.BytesIO()
    pil = dependency("PIL.Image", "ocr")
    with (
        dependency("PIL.ImageOps", "ocr").exif_transpose(image) as oriented,
        oriented.convert("RGBA") as rgba,
        pil.new("RGB", oriented.size, "white") as converted,
        rgba.getchannel("A") as alpha,
    ):
        converted.paste(rgba, mask=alpha)
        converted.save(output, format="PNG")
    return output.getvalue()
