import json

import pytest

from decision_forge.io import digest, write_json
from decision_forge.sources import PDFAdapter, ingest_manifest, locate


def make_manifest(tmp_path, sources):
    manifest = tmp_path / "corpus.json"
    write_json(manifest, {"sources": sources})
    return manifest


def test_text_and_workflow_preserve_source_locations_and_permissions(tmp_path):
    (tmp_path / "policy.md").write_text("# Sharing\nShare aggregates with partners.\n", encoding="utf-8")
    (tmp_path / "flow.json").write_text(json.dumps({"steps": [{"decision": "Require approval above $100."}]}))
    manifest = make_manifest(
        tmp_path,
        [
            {
                "id": "policy",
                "path": "policy.md",
                "version": "v1",
                "access": {"classification": "confidential", "allowed_roles": ["ops"]},
            },
            {"id": "flow", "path": "flow.json", "version": "v2", "adapter": "workflow"},
        ],
    )
    policy, flow = ingest_manifest(manifest)
    assert policy.access.allowed_roles == ["ops"]
    assert policy.access.classification == "confidential"
    assert policy.version == "v1"
    assert policy.content_hash == digest(policy.text)
    start = policy.text.index("Share")
    assert locate(policy, start, len(policy.text)) == "line:2;section:# Sharing"
    assert "Require approval above $100." in flow.text
    assert flow.spans[0].locator == "json-pointer:/steps/0/decision"
    before = policy.raw_hash
    (tmp_path / "policy.md").write_text("# Sharing\nKeep reports internal.\n")
    assert ingest_manifest(manifest)[0].raw_hash != before
    assert ingest_manifest(manifest)[1] == flow


def test_source_path_symlink_cannot_escape_manifest(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("private")
    (root / "alias.md").symlink_to(outside)
    manifest = make_manifest(root, [{"id": "policy", "path": "alias.md", "version": "v1"}])
    with pytest.raises(ValueError, match="escapes"):
        ingest_manifest(manifest)


def create_pdf(path, text=None, encrypted=False):
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if text:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(content)
    if encrypted:
        writer.encrypt("fixture-password")
    writer.write(path)


def test_pdf_uses_real_parser_and_page_spans(tmp_path):
    create_pdf(tmp_path / "policy.pdf", "Only owners may approve refunds.")
    manifest = make_manifest(tmp_path, [{"id": "pdf", "path": "policy.pdf", "version": "v1", "adapter": "pdf"}])
    document = ingest_manifest(manifest)[0]
    assert "Only owners may approve refunds." in document.text
    assert locate(document, 0, 10) == "page:1"
    assert document.spans[0].end == len(document.text)


def test_scanned_and_encrypted_pdf_fail_explicitly(tmp_path):
    create_pdf(tmp_path / "blank.pdf")
    with pytest.raises(ValueError, match="OCR"):
        PDFAdapter().extract(tmp_path / "blank.pdf")
    create_pdf(tmp_path / "encrypted.pdf", "Text", encrypted=True)
    with pytest.raises(ValueError, match="Encrypted"):
        PDFAdapter().extract(tmp_path / "encrypted.pdf")


@pytest.mark.parametrize("sources", [[], [{"id": "x", "path": "x", "version": "1"}] * 2])
def test_empty_and_duplicate_source_registry_rejected(tmp_path, sources):
    with pytest.raises(ValueError):
        ingest_manifest(make_manifest(tmp_path, sources))
