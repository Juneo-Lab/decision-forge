import json
import runpy
from pathlib import Path

import pytest

from decision_forge.io import digest, read_json, write_json
from decision_forge.pipeline import build, load_run

loop = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/finetune_evaluation.py"))
prepare = loop["prepare"]
evaluate = loop["evaluate"]
compare = loop["compare"]


class ScriptedChoices:
    """Answers with the first candidate always, then with an invalid ID."""

    base_url = "https://model.example/v1"
    model = "fixture"

    def __init__(self, answer=None):
        self.answer = answer
        self.calls = 0
        self.last_usage = {"prompt_tokens": 50, "completion_tokens": 5, "cost": 0.001}
        self.last_response_metadata = {"id": "fixture"}

    def generate(self, system, user, schema):
        self.calls += 1
        payload = json.loads(user)
        return {"choice": self.answer or payload["candidates"][0]}


def test_prepare_freezes_the_dataset_and_writes_a_pinned_plan(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    config = prepare(tmp_path / "run", tmp_path / "finetune", budget_examples=2000)
    assert config["status"] == "prepared"
    assert config["examples"]["test"] > 0
    assert (tmp_path / "finetune/training/train.jsonl").exists()
    assert not (tmp_path / "finetune/train.sh").exists()
    assert config["training_launched"] is False
    assert config["trainer"]["loss"] == "candidate_restricted_cross_entropy"
    assert read_json(tmp_path / "finetune/plan.json")["trainer"]["method"] == "lora"


def test_prepare_applies_the_example_budget(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    config = prepare(tmp_path / "run", tmp_path / "finetune", budget_examples=50)
    assert config["examples"]["train"] == 50
    training = tmp_path / "finetune/training"
    rows = [json.loads(line) for line in (training / "train.jsonl").read_text().splitlines()]
    assert len(rows) == 50
    assert [r["id"] for r in rows] == config["selected_ids"]["train"]
    assert all(row["objective"] == "candidate_readout" for row in rows)
    for split in ("train", "development", "test"):
        assert config["file_hashes"][f"{split}.jsonl"] == digest((training / f"{split}.jsonl").read_text())
    for split in ("development", "test"):
        expected = [e.id for e in load_run(tmp_path / "run") if e.split == split and len(e.valid_choices) == 1]
        assert config["selected_ids"][split] == expected


def test_prepare_refuses_a_dataset_without_a_held_out_split(project, example_path, tmp_path):
    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "sources").mkdir()
    (spec / "sources/policies.md").write_text((example_path.parent / "sources/policies.md").read_text())
    single = project.model_dump(mode="json")
    single["scenarios"] = single["scenarios"][:1]
    single["recipe"]["family_splits"] = {single["scenarios"][0]["family_id"]: "train"}
    write_json(spec / "project.json", single)
    build(spec / "project.json", tmp_path / "run")
    with pytest.raises(ValueError, match="no held-out examples"):
        prepare(tmp_path / "run", tmp_path / "finetune", budget_examples=100)


def test_evaluate_scores_only_the_held_out_split(example_path, tmp_path):
    build(example_path, tmp_path / "run")
    report = evaluate(tmp_path / "run", tmp_path / "report.json", ScriptedChoices(), "base_model")
    held_out = [e for e in load_run(tmp_path / "run") if e.split == "test"]
    assert report["cases"] == len(held_out)
    assert report["arm"] == "base_model"
    assert 0.0 <= report["accuracy"] <= 1.0
    assert read_json(tmp_path / "report.json")["dataset_digest"] == report["dataset_digest"]


def test_compare_rejects_arms_scored_on_different_datasets(tmp_path):
    left = tmp_path / "a.json"
    right = tmp_path / "b.json"
    write_json(
        left,
        {
            "arm": "a",
            "model": "m",
            "accuracy": 0.5,
            "coverage": 1.0,
            "permitted_rate": 1.0,
            "dataset_digest": "x",
            "by_kind": {},
        },
    )
    write_json(
        right,
        {
            "arm": "b",
            "model": "m",
            "accuracy": 0.6,
            "coverage": 1.0,
            "permitted_rate": 1.0,
            "dataset_digest": "y",
            "by_kind": {},
        },
    )
    with pytest.raises(ValueError, match="different datasets"):
        compare(left, right, tmp_path / "out.json")


def test_compare_reports_a_measured_delta(tmp_path):
    cases = {
        "allocate_work": {"cases": 4, "correct": 1, "accuracy": 0.25},
        "select_actions": {"cases": 4, "correct": 2, "accuracy": 0.5},
    }
    tuned = {
        "allocate_work": {"cases": 4, "correct": 3, "accuracy": 0.75},
        "select_actions": {"cases": 4, "correct": 4, "accuracy": 1.0},
    }
    base = {
        "arm": "base",
        "model": "m",
        "accuracy": 0.375,
        "coverage": 1.0,
        "permitted_rate": 1.0,
        "dataset_digest": "d",
        "cases": 8,
        "events": [{"episode_id": str(i)} for i in range(8)],
        "prompt": "fixed",
        "by_kind": cases,
    }
    cand = {
        "arm": "tuned",
        "model": "m",
        "accuracy": 0.875,
        "coverage": 1.0,
        "permitted_rate": 1.0,
        "dataset_digest": "d",
        "cases": 8,
        "events": [{"episode_id": str(i)} for i in range(8)],
        "prompt": "fixed",
        "by_kind": tuned,
    }
    write_json(tmp_path / "a.json", base)
    write_json(tmp_path / "b.json", cand)
    report = compare(tmp_path / "a.json", tmp_path / "b.json", tmp_path / "cmp.json")
    assert report["overall_delta"] == pytest.approx(0.5)
    assert {row["kind"] for row in report["by_kind"]} == {"allocate_work", "select_actions"}
    assert all(row["delta"] == pytest.approx(0.5) for row in report["by_kind"])


@pytest.mark.parametrize("budget,override", [(0, {}), (-1, {}), (5, {"loss": "sft"}), (5, {"epochs": 0})])
def test_prepare_rejects_invalid_contract_before_writing(tmp_path, budget, override):
    with pytest.raises(ValueError):
        prepare(tmp_path / "nonexistent", tmp_path / "output", budget, override)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"events": [{"episode_id": "different"}]},
        {"prompt": "changed"},
        {"cases": 2},
        {"events": []},
    ],
)
def test_compare_requires_matching_case_records_and_prompts(tmp_path, change):
    base = {"dataset_digest": "d", "cases": 1, "events": [{"episode_id": "a"}], "prompt": "fixed"}
    write_json(tmp_path / "base.json", base)
    write_json(tmp_path / "candidate.json", {**base, **change})
    with pytest.raises(ValueError, match="case|prompt|counts"):
        compare(tmp_path / "base.json", tmp_path / "candidate.json", tmp_path / "result.json")
    assert not (tmp_path / "result.json").exists()
