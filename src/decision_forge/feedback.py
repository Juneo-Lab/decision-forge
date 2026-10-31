"""Idempotent runtime observations, explicit review labels, and held-out-safe update requests."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, JsonValue

from decision_forge.io import canonical, digest, load_project, read_json, write_json
from decision_forge.models import Contract, Identifier
from decision_forge.pipeline import load_run, load_run_project


class RuntimeEvent(Contract):
    schema_version: Literal["1.0"] = "1.0"
    id: Identifier
    workflow_id: Identifier
    decision_id: Identifier
    sequence: int = Field(ge=0)
    kind: Literal["decision", "action", "tool_result", "outcome", "review"]
    episode_id: Identifier
    payload: dict[str, JsonValue]


def ingest_events(database: Path, events: list[RuntimeEvent]) -> dict[str, int]:
    """Atomically append immutable events; retries are idempotent, conflicting IDs fail."""
    database.parent.mkdir(parents=True, exist_ok=True)
    # Create privately before SQLite opens the file.
    database.touch(mode=0o600, exist_ok=True)
    inserted = duplicate = 0
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, workflow TEXT, "
            "sequence INTEGER, body TEXT, UNIQUE(workflow, sequence))"
        )
        for event in events:
            body = canonical(event.model_dump(mode="json"))
            existing = connection.execute("SELECT body FROM events WHERE id=?", (event.id,)).fetchone()
            if existing:
                if existing[0] != body:
                    raise ValueError("Runtime event ID conflicts with previously ingested content")
                duplicate += 1
                continue
            prior = connection.execute(
                "SELECT body FROM events WHERE workflow=? ORDER BY sequence", (event.workflow_id,)
            ).fetchall()
            parsed = [RuntimeEvent.model_validate_json(r[0]) for r in prior]
            if event.sequence != len(parsed):
                raise ValueError("Workflow event sequence must be contiguous and start at zero")
            associated = [e for e in parsed if e.decision_id == event.decision_id]
            if event.kind == "decision":
                if associated:
                    raise ValueError("Decision ID already exists in this workflow")
            elif (
                not associated
                or associated[0].kind != "decision"
                or any(e.episode_id != event.episode_id for e in associated)
            ):
                raise ValueError("Runtime event must associate with an existing decision and episode")
            if event.kind == "review" and not all(
                isinstance(event.payload.get(k), str) and str(event.payload[k]).strip()
                for k in ("reviewer", "reason", "choice")
            ):
                raise ValueError("A verified review requires reviewer, reason, and choice")
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?)", (event.id, event.workflow_id, event.sequence, body)
            )
            inserted += 1
    return {"inserted": inserted, "duplicates": duplicate}


def feedback_requests(run: Path, database: Path) -> dict[str, Any]:
    """Only explicit reviews of development examples can influence the next training recipe.

    Outcomes remain observations. A reviewer identity is attribution, not authentication; deployments
    must enforce access to this local event store at their application boundary.
    """
    episodes = {e.id: e for e in load_run(run)}
    with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT body FROM events ORDER BY workflow, sequence").fetchall()
    events = [RuntimeEvent.model_validate_json(row[0]) for row in rows]
    latest: dict[tuple[str, str], RuntimeEvent] = {}
    decisions: dict[tuple[str, str], RuntimeEvent] = {}
    for event in events:
        key = (event.workflow_id, event.decision_id)
        if event.kind == "decision":
            decisions[key] = event
        elif event.kind == "review":
            latest[key] = event
    requests = []
    excluded: dict[str, int] = {"train": 0, "test": 0, "unknown_episode": 0, "no_error": 0}
    for key, event in sorted(latest.items()):
        episode = episodes.get(event.episode_id)
        if episode is None:
            excluded["unknown_episode"] += 1
            continue
        if episode.split != "development":
            excluded[episode.split] += 1
            continue
        choice = event.payload["choice"]
        if choice not in {c.id for c in episode.candidates}:
            raise ValueError("Review names an unknown candidate")
        if decisions[key].payload.get("choice") == choice:
            excluded["no_error"] += 1
            continue
        requests.append(
            {
                "id": digest([event.id, episode.id]),
                "review_event_id": event.id,
                "origin_episode_id": episode.id,
                "origin_family_id": episode.family_id,
                "node_id": episode.node_id,
                "stratum": "feedback",
                "reviewed_choice": choice,
                "policy_disagreement": choice not in episode.valid_choices,
                "label_basis": "explicit_review",
                "state": episode.state,
                "status": "policy_review_required" if choice not in episode.valid_choices else "ready_for_planning",
            }
        )
    return {
        "protocol": "development-review-feedback-v1",
        "dataset_digest": read_json(run / "manifest.json")["dataset_digest"],
        "requests": requests,
        "excluded": excluded,
        "observations_used_as_labels": 0,
    }


def lineage(run: Path) -> dict[str, Any]:
    project = load_run_project(run)
    episodes = load_run(run)
    manifest = read_json(run / "manifest.json")
    return {
        "schema_version": "1.0",
        "dataset_digest": manifest["dataset_digest"],
        "sources": manifest["source_hashes"],
        "rules": {r.id: [e.model_dump(mode="json") for e in r.evidence] for r in project.organization.rules},
        "episodes": {
            e.id: {
                "family_id": e.family_id,
                "split": e.split,
                "scenario_id": e.scenario_id,
                "rule_ids": e.rule_ids,
                "source_hashes": e.source_hashes,
            }
            for e in episodes
        },
    }


def update_dataset(spec: Path, previous: Path, output: Path, cache: Path | None = None) -> dict[str, Any]:
    """Rebuild after policy changes while preserving all published family assignments."""
    from filelock import FileLock

    from decision_forge.io import load_sources
    from decision_forge.pipeline import _build
    from decision_forge.review import require_review

    project = load_project(spec)
    require_review(spec)
    previous_manifest = read_json(previous / "manifest.json")
    load_run(previous)
    previous_project = load_run_project(previous)
    if project.organization.id != previous_project.organization.id:
        raise ValueError("A dataset update must retain organization identity")
    old_splits = (
        previous_manifest.get("family_registry")
        or previous_manifest.get("family_splits")
        or {e.family_id: e.split for e in load_run(previous)}
    )
    for family, split in project.recipe.family_splits.items():
        if family in old_splits and split != old_splits[family]:
            raise ValueError("A published family cannot move between training and held-out splits")
    active = {s.family_id for s in project.scenarios}
    project.recipe.family_splits = {k: v for k, v in old_splits.items() if k in active} | project.recipe.family_splits
    contents, hashes = load_sources(project, spec.parent)
    output.mkdir(parents=True, exist_ok=True)
    cache = cache or output.parent / ".forge-cache"
    cache.mkdir(parents=True, exist_ok=True)
    with FileLock(str(output / ".build.lock"), timeout=0):
        result = _build(project, contents, hashes, output, cache)
        result["family_registry"] = old_splits | result["family_splits"]
        result["previous_dataset_digest"] = previous_manifest["dataset_digest"]
        write_json(output / "manifest.json", result)
        report = {
            "previous_dataset_digest": previous_manifest["dataset_digest"],
            "dataset_digest": result["dataset_digest"],
            "accepted_delta": result["report"]["accepted"] - previous_manifest["report"]["accepted"],
            "reused_scenarios": result["report"]["cache_hits"],
            "provider_cost_usd": 0,
            "family_splits_preserved": True,
        }
        write_json(output / "update.json", report)
    return report
