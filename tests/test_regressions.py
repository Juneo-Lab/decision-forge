import subprocess
import sys

import pytest

from decision_forge.io import load_project, load_sources
from decision_forge.models import Predicate
from decision_forge.pipeline import build
from decision_forge.policy import matches_predicate


@pytest.mark.parametrize("op", ["eq", "ne", "in", "contains"])
@pytest.mark.parametrize("value", [{"approved": 1}, [1], {"nested": [1]}])
def test_nested_boolean_numeric_types_do_not_match(op, value):
    def to_bool(x):
        if isinstance(x, dict):
            return {k: to_bool(v) for k, v in x.items()}
        if isinstance(x, list):
            return [to_bool(v) for v in x]
        return True

    expected = to_bool(value)
    predicate = Predicate(path="state.value", op=op, value=[expected] if op == "in" else expected)
    actual = [value] if op == "contains" else value
    assert matches_predicate(predicate, {"state": {"value": actual}}) is (op == "ne")
    actual = [expected] if op == "contains" else expected
    assert matches_predicate(predicate, {"state": {"value": actual}}) is (op != "ne")


def test_build_snapshot_is_self_contained(example_path, tmp_path):
    run = tmp_path / "run"
    manifest = build(example_path, run)
    snapshot = load_project(run / "project.snapshot.json")
    contents, hashes = load_sources(snapshot, run)
    assert hashes == manifest["source_hashes"]
    assert all(contents.values())


def test_malformed_yaml_cli_does_not_leak_source(tmp_path):
    path = tmp_path / "private.yaml"
    path.write_text("secret: [PRIVATE_CUSTOMER_TOKEN\n")
    result = subprocess.run(
        [sys.executable, "-m", "decision_forge.cli", "validate", str(path)], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "PRIVATE_CUSTOMER_TOKEN" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert "Invalid YAML/JSON" in result.stderr
