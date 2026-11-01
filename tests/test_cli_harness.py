import json
import tomllib

from typer.testing import CliRunner

from decision_forge.cli import app
from decision_forge.harness import export_harbor, replay
from decision_forge.io import write_json
from decision_forge.pipeline import build, load_run


def test_cli_full_offline_workflow(example_path, tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["validate", str(example_path)]).exit_code == 0
    result = runner.invoke(app, ["build", str(example_path), "--output", str(tmp_path / "run")])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["export", str(tmp_path / "run"), "--output", str(tmp_path / "export")])
    assert result.exit_code == 0, result.output
    assert (
        runner.invoke(
            app,
            [
                "export",
                str(tmp_path / "run"),
                "--output",
                str(tmp_path / "bad"),
                "--format",
                "unknown",
            ],
        ).exit_code
        == 1
    )


def test_harbor_tasks_separate_model_context_and_labels(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    assert export_harbor(tmp_path / "run", tmp_path / "tasks", 3, "train") == 3
    tasks = list((tmp_path / "tasks").glob("ep-*"))
    assert len(tasks) == 3
    for task in tasks:
        assert "gold" not in json.loads((task / "environment/context.json").read_text())
        assert "valid_choices" not in (task / "environment/context.json").read_text()
        assert "tests" not in (task / "environment/Dockerfile").read_text()
        assert tomllib.loads((task / "task.toml").read_text())["version"] == "1.0"
        compile((task / "tests/verify.py").read_text(), "verify.py", "exec")


def test_replay_records_missing_and_wrong_choices(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    episodes = load_run(tmp_path / "run")
    write_json(
        tmp_path / "predictions.json",
        {episodes[0].id: episodes[0].valid_choices[0], episodes[1].id: "unknown"},
    )
    result = replay(tmp_path / "run", tmp_path / "predictions.json")
    assert result["answered"] == 2
    assert result["correct"] == 1
    assert result["coverage"] == 2 / 336


def test_evaluate_decisions_rejects_non_object_bindings(example_path, tmp_path):
    bindings = tmp_path / "bindings.json"
    write_json(bindings, [])
    reference = example_path.parent / "decision-reference.json"
    result = CliRunner().invoke(app, ["evaluate-decisions", str(example_path), str(reference), str(bindings)])
    assert result.exit_code == 1
    assert "JSON object keyed by node ID" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
