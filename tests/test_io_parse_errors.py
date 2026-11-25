"""Parse errors name the file and a 1-based position, never the file's content."""

import pytest

from decision_forge.io import read_json, read_jsonl, read_yaml

SECRET = "PRIVATE_CUSTOMER_TOKEN"


def _error(reader, path) -> str:
    with pytest.raises(ValueError) as excinfo:
        reader(path)
    message = str(excinfo.value)
    assert SECRET not in message
    return message


def test_json_syntax_error_names_the_file_line_and_column(tmp_path):
    path = tmp_path / "trace.json"
    path.write_text('{"a": 1,\n "b": ' + SECRET + "}\n", encoding="utf-8")

    message = _error(read_json, path)

    assert str(path) in message
    assert "line 2, column 7" in message
    assert message.startswith("Invalid JSON artifact")


def test_yaml_syntax_error_names_the_file_line_and_column(tmp_path):
    path = tmp_path / "project.yaml"
    path.write_text("a: 1\nsecret: [" + SECRET + "\n", encoding="utf-8")

    message = _error(read_yaml, path)

    assert str(path) in message
    assert "line 3, column 1" in message
    assert message.startswith("Invalid YAML/JSON input")


def test_jsonl_syntax_error_names_the_file_line_and_column(tmp_path):
    path = tmp_path / "episodes.jsonl"
    path.write_text('{"ok": 1}\n\n{"x": ' + SECRET + "}\n", encoding="utf-8")

    message = _error(read_jsonl, path)

    assert str(path) in message
    assert "line 3, column 7" in message


@pytest.mark.parametrize("reader", [read_json, read_yaml, read_jsonl])
def test_an_encoding_error_names_the_file(tmp_path, reader):
    path = tmp_path / "broken.bin"
    path.write_bytes(b"\xff\xfe" + SECRET.encode())

    message = _error(reader, path)

    assert str(path) in message
    assert "encoding" in message
