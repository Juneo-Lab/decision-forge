import shutil
import sqlite3

import pytest

from decision_forge.feedback import RuntimeEvent, feedback_requests, ingest_events, lineage, update_dataset
from decision_forge.io import load_project, write_json
from decision_forge.pipeline import build, load_run


def test_ingestion_is_atomic_idempotent_and_associates_outcomes(example_path, tmp_path):
    database = tmp_path / "events.sqlite"
    event = RuntimeEvent(
        id="e1", workflow_id="w", decision_id="d", sequence=0, kind="decision", episode_id="ep", payload={"choice": "a"}
    )
    assert ingest_events(database, [event]) == {"inserted": 1, "duplicates": 0}
    assert ingest_events(database, [event]) == {"inserted": 0, "duplicates": 1}
    event.payload["choice"] = "other"
    with pytest.raises(ValueError, match="conflicts"):
        ingest_events(database, [event])
    outcome = event.model_copy(update={"id": "e2", "sequence": 1, "kind": "outcome"})
    bad = outcome.model_copy(update={"id": "e3", "sequence": 3})
    with pytest.raises(ValueError, match="contiguous"):
        ingest_events(database, [outcome, bad])
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    outcome.episode_id = "wrong"
    with pytest.raises(ValueError, match="associate"):
        ingest_events(database, [outcome])


def test_only_reviewed_development_errors_become_feedback(example_path, tmp_path):
    run = tmp_path / "run"
    build(example_path, run)
    episodes = load_run(run)
    events = []
    for split in ("train", "development", "test"):
        e = next(e for e in episodes if e.split == split)
        events.append(
            RuntimeEvent(
                id=f"{split}-decision",
                workflow_id=split,
                decision_id="d",
                sequence=0,
                kind="decision",
                episode_id=e.id,
                payload={"choice": "wrong"},
            )
        )
        events.append(
            RuntimeEvent(
                id=f"{split}-outcome",
                workflow_id=split,
                decision_id="d",
                sequence=1,
                kind="outcome",
                episode_id=e.id,
                payload={"success": True},
            )
        )
        events.append(
            RuntimeEvent(
                id=f"{split}-review",
                workflow_id=split,
                decision_id="d",
                sequence=2,
                kind="review",
                episode_id=e.id,
                payload={"choice": e.valid_choices[0], "reviewer": "fixture", "reason": "Test-only reviewed outcome"},
            )
        )
    database = tmp_path / "events.sqlite"
    ingest_events(database, events)
    result = feedback_requests(run, database)
    assert len(result["requests"]) == 1
    assert result["excluded"]["test"] == result["excluded"]["train"] == 1
    assert result["observations_used_as_labels"] == 0
    assert result["requests"][0]["status"] == "ready_for_planning"
    graph = lineage(run)
    assert len(graph["episodes"]) == 336 and graph["rules"] and graph["sources"]


def test_targeted_update_preserves_splits_and_reuses_unchanged_scenarios(example_path, tmp_path):
    shutil.copytree(example_path.parent, tmp_path / "input")
    spec = tmp_path / "input/project.yaml"
    build(spec, tmp_path / "first")
    before = {e.family_id: e.split for e in load_run(tmp_path / "first")}
    project = load_project(spec)
    project.nodes[0].question += " Use the current policy."
    write_json(spec, project.model_dump(mode="json"))
    report = update_dataset(spec, tmp_path / "first", tmp_path / "second")
    assert report["reused_scenarios"] == 36
    assert {e.family_id: e.split for e in load_run(tmp_path / "second")} == before
    family = next(iter(before))
    project.recipe.family_splits[family] = "test" if before[family] != "test" else "train"
    write_json(spec, project.model_dump(mode="json"))
    with pytest.raises(ValueError, match="cannot move"):
        update_dataset(spec, tmp_path / "first", tmp_path / "bad")


def test_removed_family_cannot_return_as_training_data(example_path, tmp_path):
    shutil.copytree(example_path.parent, tmp_path / "input")
    spec = tmp_path / "input/project.yaml"
    build(spec, tmp_path / "v1")
    original = load_project(spec)
    test_family = next(e.family_id for e in load_run(tmp_path / "v1") if e.split == "test")
    reduced = original.model_copy(deep=True)
    reduced.scenarios = [s for s in reduced.scenarios if s.family_id != test_family]
    write_json(spec, reduced.model_dump(mode="json"))
    update_dataset(spec, tmp_path / "v1", tmp_path / "v2")
    write_json(spec, original.model_dump(mode="json"))
    update_dataset(spec, tmp_path / "v2", tmp_path / "v3")
    assert {e.split for e in load_run(tmp_path / "v3") if e.family_id == test_family} == {"test"}


def test_event_store_paths_and_errors_are_reported_without_tracebacks(example_path, tmp_path):
    run = tmp_path / "run"
    build(example_path, run)
    episode = load_run(run)[0]
    with pytest.raises(ValueError, match="does not exist"):
        feedback_requests(run, tmp_path / "missing.sqlite")
    empty = tmp_path / "empty.sqlite"
    sqlite3.connect(empty).close()
    with pytest.raises(ValueError, match="cannot be read"):
        feedback_requests(run, empty)
    # URI metacharacters in the path must not redirect the read-only connection to another file.
    database = tmp_path / "a#b?c%d" / "events.sqlite"
    event = RuntimeEvent(
        id="e1", workflow_id="w", decision_id="d", sequence=0, kind="decision", episode_id=episode.id, payload={}
    )
    assert ingest_events(database, [event]) == {"inserted": 1, "duplicates": 0}
    assert feedback_requests(run, database)["requests"] == []
