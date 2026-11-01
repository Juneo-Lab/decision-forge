"""Text files are exchanged between hosts, so their encoding must not depend on the platform locale."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_IO = {"open", "read_text", "write_text"}
IMAGE_READERS = {"pil", "Image"}


def unspecified_encodings(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name not in TEXT_IO or any(keyword.arg == "encoding" for keyword in node.keywords):
            continue
        modes = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
        modes += [kw.value.value for kw in node.keywords if kw.arg == "mode" and isinstance(kw.value, ast.Constant)]
        # Binary handles and image readers such as PIL.Image.open take no text encoding.
        receiver = getattr(node.func, "value", None)
        if any("b" in mode for mode in modes) or (isinstance(receiver, ast.Name) and receiver.id in IMAGE_READERS):
            continue
        found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def test_package_and_scripts_use_explicit_text_encodings():
    paths = sorted([*ROOT.glob("src/decision_forge/*.py"), *ROOT.glob("scripts/*.py")])
    assert [location for path in paths for location in unspecified_encodings(path)] == []
