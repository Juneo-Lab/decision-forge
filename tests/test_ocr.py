import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from test_corpus import POLICY, ScriptedProvider
from test_sources import create_pdf
from typer.testing import CliRunner

from decision_forge.cli import app
from decision_forge.corpus import compile_corpus
from decision_forge.io import read_json, write_json
from decision_forge.ocr import OCRConfig, OCRLine, OCRRunner
from decision_forge.sources import ingest_manifest


def image_fixture(tmp_path, *, name="scan.png"):
    pil = pytest.importorskip("PIL.Image")
    with pil.new("RGB", (600, 200), "white") as image:
        image.save(tmp_path / name)
    return tmp_path / name


def manifest(tmp_path, name="scan.png", **options):
    path = tmp_path / "corpus.json"
    write_json(path, {"sources": [{"id": "policy", "path": name, "version": "v1", **options}]})
    return path


def remote_config(**options):
    return {"backend": "openai", "base_url": "http://127.0.0.1:9999/v1", "model": "protocol-fixture", **options}


@pytest.fixture
def scripted_ocr(monkeypatch):
    calls = []

    def recognize(self, png):
        calls.append(png)
        return [OCRLine(text=POLICY, confidence=0.98, box=[0, 0, 100, 20])], None

    monkeypatch.setattr(OCRRunner, "_recognize", recognize)
    return calls


def test_remote_ocr_requires_explicit_cli_opt_in(tmp_path, scripted_ocr):
    image_fixture(tmp_path)
    path = manifest(tmp_path, ocr=remote_config())
    with pytest.raises(ValueError, match="allow-external-ocr"):
        ingest_manifest(path)
    result = CliRunner().invoke(app, ["ingest", str(path), "--output", str(tmp_path / "result.json")])
    assert result.exit_code == 1 and not scripted_ocr
    result = CliRunner().invoke(
        app, ["ingest", str(path), "--output", str(tmp_path / "result.json"), "--allow-external-ocr"]
    )
    assert result.exit_code == 0
    assert read_json(tmp_path / "result.json")[0]["spans"][0]["method"] == "ocr"


def test_ocr_cache_reuses_pages_and_rejects_tampering(tmp_path, scripted_ocr):
    image_fixture(tmp_path)
    path = manifest(tmp_path, ocr=remote_config())
    first = ingest_manifest(path, allow_external_ocr=True)[0]
    second = ingest_manifest(path, allow_external_ocr=True)[0]
    assert first == second and len(scripted_ocr) == 1
    assert first.spans[0].locator == "frame:1;ocr:openai;line:1"
    assert first.spans[0].confidence == 0.98
    assert first.extraction["review_required"] is True
    cache = next(p for p in (tmp_path / ".forge-ocr-cache").glob("*.json") if ".attempt." not in p.name)
    data = read_json(cache)
    data["result"][0]["text"] = "Unverified alteration"
    write_json(cache, data)
    with pytest.raises(ValueError, match="integrity"):
        ingest_manifest(path, allow_external_ocr=True)


def test_ocr_revision_change_invalidates_cache(tmp_path, scripted_ocr):
    image_fixture(tmp_path)
    ingest_manifest(manifest(tmp_path, ocr=remote_config(revision="v1")), allow_external_ocr=True)
    ingest_manifest(manifest(tmp_path, ocr=remote_config(revision="v2")), allow_external_ocr=True)
    assert len(scripted_ocr) == 2


def test_mixed_pdf_only_ocrs_pages_without_text(tmp_path, scripted_ocr):
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("pypdfium2")
    image_fixture(tmp_path)
    create_pdf(tmp_path / "native.pdf", "Native rule")
    pil = pytest.importorskip("PIL.Image")
    with pil.open(tmp_path / "scan.png") as image:
        image.save(tmp_path / "scan.pdf")
    writer = pypdf.PdfWriter()
    writer.append(tmp_path / "native.pdf")
    writer.append(tmp_path / "scan.pdf")
    writer.write(tmp_path / "mixed.pdf")
    result = ingest_manifest(manifest(tmp_path, "mixed.pdf", ocr=remote_config(max_pages=1)), allow_external_ocr=True)[
        0
    ]
    assert "Native rule" in result.text and POLICY in result.text
    assert result.spans[0].locator == "page:1"
    assert result.spans[1].locator == "page:2;ocr:openai;line:1"
    assert len(scripted_ocr) == 1


def test_force_ocr_replaces_native_text_and_preserves_review_gate(tmp_path, scripted_ocr):
    pytest.importorskip("pypdfium2")
    create_pdf(tmp_path / "policy.pdf", "Native text layer")
    path = manifest(tmp_path, "policy.pdf", ocr=remote_config(mode="always"), access={"classification": "confidential"})
    output = tmp_path / "compiled"
    compile_corpus(path, output, ScriptedProvider(), "fixture", allow_external_ocr=True)
    project = read_json(output / "project.json")
    assert project["sources"][0]["access"]["classification"] == "confidential"
    assert "ocr:openai" in project["organization"]["rules"][0]["evidence"][0]["locator"]
    from decision_forge.review import review_status

    assert not review_status(output / "project.json")["ready"]
    compile_corpus(path, output, ScriptedProvider(), "fixture", allow_external_ocr=True)
    assert len(scripted_ocr) == 1


def test_image_pixel_and_frame_limits_precede_inference(tmp_path, scripted_ocr):
    image_fixture(tmp_path)
    with pytest.raises(ValueError, match="pixel limit"):
        ingest_manifest(manifest(tmp_path, ocr=remote_config(max_pixels=1000)), allow_external_ocr=True)
    assert not scripted_ocr
    pil = pytest.importorskip("PIL.Image")
    with pil.new("RGB", (50, 50), "white") as image:
        image.save(tmp_path / "pages.tiff", save_all=True, append_images=[image])
    with pytest.raises(ValueError, match="frame limit"):
        ingest_manifest(manifest(tmp_path, "pages.tiff", ocr=remote_config(max_pages=1)), allow_external_ocr=True)
    assert not scripted_ocr


def test_blank_ocr_is_not_cached_as_success(tmp_path, monkeypatch):
    image_fixture(tmp_path)
    monkeypatch.setattr(OCRRunner, "_recognize", lambda self, png: ([], None))
    path = manifest(tmp_path, ocr=remote_config())
    for _ in range(2):
        with pytest.raises(ValueError, match="no text"):
            ingest_manifest(path, allow_external_ocr=True)
    journal = read_json(next((tmp_path / ".forge-ocr-cache").glob("*.attempt.json")))
    assert [e["status"] for e in journal] == ["failed", "failed"]
    assert len(list((tmp_path / ".forge-ocr-cache").glob("*.json"))) == 1


@pytest.mark.parametrize(
    "options",
    [
        {"backend": "rapidocr", "det_model": "det.onnx"},
        {"backend": "tesseract", "tessdata_dir": "models", "language": "../../bad"},
        remote_config(base_url="http://remote.example/v1"),
        remote_config(base_url="https://user:secret@remote.example/v1"),
        remote_config(min_confidence=0.9),
    ],
)
def test_invalid_ocr_configuration_fails(options):
    with pytest.raises(ValueError):
        OCRConfig.model_validate(options)


def test_real_http_vision_contract_usage_and_truncation(tmp_path, monkeypatch):
    image_fixture(tmp_path)
    requests = []
    finish = ["stop"]
    monkeypatch.setenv("FORGE_OCR_API_KEY", "fixture-secret")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, payload, self.headers.get("Authorization")))
            body = json.dumps(
                {
                    "choices": [{"message": {"content": POLICY}, "finish_reason": finish[0]}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        options = remote_config(base_url=f"http://127.0.0.1:{server.server_port}/v1")
        path = manifest(tmp_path, ocr=options)
        result = ingest_manifest(path, allow_external_ocr=True)[0]
        assert result.text.strip() == POLICY and result.spans[0].confidence is None
        route, body, auth = requests[0]
        assert route == "/v1/chat/completions" and auth == "Bearer fixture-secret"
        encoded = body["messages"][0]["content"][0]["image_url"]["url"].split(",")[1]
        assert base64.b64decode(encoded).startswith(b"\x89PNG")
        journal = read_json(next((tmp_path / ".forge-ocr-cache").glob("*.attempt.json")))
        assert journal[0]["usage"]["total_tokens"] == 30
        assert "fixture-secret" not in json.dumps(result.model_dump())
        assert all("fixture-secret" not in p.read_text() for p in (tmp_path / ".forge-ocr-cache").glob("*.json"))
        finish[0] = "length"
        with pytest.raises(ValueError, match="incomplete"):
            ingest_manifest(manifest(tmp_path, ocr={**options, "revision": "new"}), allow_external_ocr=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_rapidocr_bundled_models_run_offline(tmp_path, monkeypatch):
    pytest.importorskip("rapidocr")
    pytest.importorskip("onnxruntime")
    from PIL import Image, ImageDraw, ImageFont
    from rapidocr.utils.download_file import DownloadFile

    def no_download(*args, **kwargs):
        raise AssertionError("Ingestion must not download model files")

    monkeypatch.setattr(DownloadFile, "run", no_download)
    with Image.new("RGB", (1400, 180), "white") as image:
        ImageDraw.Draw(image).text(
            (30, 40),
            "Only managers approve refunds above 100 dollars.",
            font=ImageFont.load_default(size=32),
            fill="black",
        )
        image.save(tmp_path / "scan.png")
    result = ingest_manifest(manifest(tmp_path, ocr={"backend": "rapidocr"}))[0]
    assert "managers approve refunds above 100" in result.text
    assert all(s.method == "ocr" and s.confidence > 0.9 for s in result.spans)
    assert result.extraction["ocr"]["models"]["rec_model"]


def test_tesseract_command_contract_and_confidence_gate(tmp_path, monkeypatch):
    import subprocess

    import decision_forge.ocr as module

    image_fixture(tmp_path)
    binary = tmp_path / "tesseract"
    binary.write_bytes(b"binary identity fixture")
    (tmp_path / "eng.traineddata").write_bytes(b"language model identity fixture")
    monkeypatch.setattr(module.shutil, "which", lambda name: str(binary))
    commands = []

    def execute(command, **kwargs):
        commands.append(command)
        assert kwargs["timeout"] == 120 and kwargs["check"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
                b"5\t1\t1\t1\t1\t1\t10\t20\t80\t20\t95\tApprove\n"
                b"5\t1\t1\t1\t1\t2\t100\t20\t80\t20\t96\trefunds\n"
            ),
        )

    monkeypatch.setattr(module.subprocess, "run", execute)
    options = {"backend": "tesseract", "tessdata_dir": ".", "min_confidence": 0.9}
    result = ingest_manifest(manifest(tmp_path, ocr=options))[0]
    assert result.text == "Approve refunds\n" and result.spans[0].box == [10, 20, 180, 40]
    assert "--tessdata-dir" in commands[0]
    with pytest.raises(ValueError, match="confidence"):
        ingest_manifest(manifest(tmp_path, ocr={**options, "min_confidence": 0.99}))


def test_force_pdf_ocr_does_not_read_broken_native_text(tmp_path, scripted_ocr, monkeypatch):
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("pypdfium2")
    create_pdf(tmp_path / "policy.pdf", "Native text")

    def fail(*args, **kwargs):
        raise AssertionError("Forced OCR must bypass native text extraction")

    monkeypatch.setattr(pypdf.PageObject, "get_contents", fail)
    monkeypatch.setattr(pypdf.PageObject, "extract_text", fail)
    result = ingest_manifest(
        manifest(tmp_path, "policy.pdf", ocr=remote_config(mode="always")), allow_external_ocr=True
    )[0]
    assert result.text.strip() == POLICY and len(scripted_ocr) == 1


def test_transparent_images_are_composited_on_white():
    import io

    pil = pytest.importorskip("PIL.Image")
    from decision_forge.ocr import image_png

    with pil.new("RGBA", (20, 20), (0, 0, 0, 0)) as source:
        source.putpixel((2, 2), (0, 0, 0, 255))
        data = image_png(source, OCRConfig(backend="rapidocr"))
    with pil.open(io.BytesIO(data)) as result:
        assert result.getpixel((0, 0)) == (255, 255, 255)
        assert result.getpixel((2, 2)) == (0, 0, 0)
