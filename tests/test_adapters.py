import shutil

import pytest
from test_review import accept_fixture

from decision_forge.adapters import designer_process, orchestrate
from decision_forge.io import load_project, read_json, write_json
from decision_forge.pipeline import build


def test_real_langgraph_pauses_for_review_then_resumes_without_duplicate_export(example_path, tmp_path):
    pytest.importorskip("langgraph.graph")
    pytest.importorskip("langgraph.checkpoint.sqlite")
    shutil.copytree(example_path.parent, tmp_path / "input")
    spec = tmp_path / "input/project.yaml"
    write_json(spec.parent / "compilation.json", {"status": "draft"})
    database = tmp_path / "jobs.sqlite"
    result = orchestrate(spec, tmp_path / "job", database, "test-job")
    assert result["stage"] == "review_required"
    assert not (tmp_path / "job/run").exists()
    accept_fixture(spec)
    result = orchestrate(spec, tmp_path / "job", database, "test-job")
    assert result["stage"] == "complete"
    again = orchestrate(spec, tmp_path / "job", database, "test-job")
    assert again == result
    project = load_project(spec)
    project.recipe.seed += 1
    write_json(spec, project.model_dump(mode="json"))
    with pytest.raises(ValueError, match="inputs changed"):
        orchestrate(spec, tmp_path / "job", database, "test-job")


def test_real_designer_preserves_ordered_seed_contract_and_recovers(example_path, tmp_path):
    pytest.importorskip("data_designer.interface")
    shutil.copytree(example_path.parent, tmp_path / "input")
    spec = tmp_path / "input/project.yaml"
    project = load_project(spec)
    project.scenarios = project.scenarios[:2]
    project.recipe.variants_per_scenario = 2
    write_json(spec, project.model_dump(mode="json"))
    build(spec, tmp_path / "run")
    result = designer_process(tmp_path / "run", tmp_path / "designer")
    assert result["records"] == 4 and result["provider_calls"] == 0
    assert result == designer_process(tmp_path / "run", tmp_path / "designer")
    # Simulate a process crash after engine publication but before adapter publication.
    state = read_json(tmp_path / "designer/adapter.json")
    state["status"] = "running"
    write_json(tmp_path / "designer/adapter.json", state)
    assert designer_process(tmp_path / "run", tmp_path / "designer")["records_digest"] == result["records_digest"]
    rows = read_json(tmp_path / "designer/records.json")
    rows[0]["training_input"] = "tampered"
    write_json(tmp_path / "designer/records.json", rows)
    with pytest.raises(ValueError, match="integrity"):
        designer_process(tmp_path / "run", tmp_path / "designer")


def test_designer_directory_protects_sdk_files_on_failure(example_path, tmp_path, monkeypatch):
    interface = pytest.importorskip("data_designer.interface")
    build(example_path, tmp_path / "run")

    def fail_after_partial_write(self, *args, **kwargs):
        artifact = tmp_path / "designer/engine/partial.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("source-derived SDK artifact")
        artifact.chmod(0o644)
        raise RuntimeError("Simulated interrupted engine")

    monkeypatch.setattr(interface.DataDesigner, "create", fail_after_partial_write)
    with pytest.raises(RuntimeError, match="interrupted engine"):
        designer_process(tmp_path / "run", tmp_path / "designer")
    assert (tmp_path / "designer").stat().st_mode & 0o077 == 0
