"""The tutorial notebook must keep running against the current code and example data."""

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "examples/agent_delegation/tutorial.ipynb"


def test_every_notebook_cell_runs_in_order(tmp_path, monkeypatch, capsys):
    # A copy of the repository content the notebook reads, so its runs/ directory stays out of the tree.
    for name in ("examples", "scripts"):
        shutil.copytree(ROOT / name, tmp_path / name)
    monkeypatch.chdir(tmp_path / "examples/agent_delegation")
    namespace: dict[str, object] = {"__name__": "__main__"}
    cells = [cell for cell in json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"] if cell["cell_type"] == "code"]
    for cell in cells:
        exec(compile("".join(cell["source"]), str(NOTEBOOK), "exec"), namespace)  # noqa: S102
    output = capsys.readouterr().out
    assert "Refused: Evidence quote rejected for rule refund-proceed" in output
    assert "Before: (['hand-back'], 'train')" in output
    assert "After:  (['proceed'], 'train')" in output
    assert "17 situations, 8 decided differently" in output


def test_the_notebook_is_committed_without_outputs():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert all(not cell.get("outputs") for cell in notebook["cells"] if cell["cell_type"] == "code")
