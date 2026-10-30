"""Content-bound review decisions and a fail-closed gate for compiled projects."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock

from decision_forge.io import canonical, digest, load_project, load_sources, read_json, write_json
from decision_forge.policy import matches


def review_snapshot(spec: Path) -> dict[str, Any]:
    project = load_project(spec)
    _, hashes = load_sources(project, spec.parent)
    fingerprint = digest({"project": project.model_dump(), "sources": hashes})
    items: list[dict[str, Any]] = []
    rules = {r.id: r for r in project.organization.rules}
    for rule in rules.values():
        items.append({"id": f"rule:{rule.id}", "kind": "rule", "content": rule.model_dump(mode="json")})
    for node in project.nodes:
        items.append({"id": f"node:{node.id}", "kind": "node", "content": node.model_dump(mode="json")})
        for left, right in combinations((rules[i] for i in node.rule_ids), 2):
            if left.effect == right.effect or left.priority != right.priority:
                continue
            identical = canonical(left.when.model_dump()) == canonical(right.when.model_dump())
            overlaps = []
            for scenario in project.scenarios:
                if scenario.node_id != node.id:
                    continue
                for candidate in node.candidates:
                    context = {"state": scenario.state, "candidate": {**candidate.attributes, "id": candidate.id}}
                    if matches(left.when, context) and matches(right.when, context):
                        overlaps.append({"scenario": scenario.id, "candidate": candidate.id})
            if identical or overlaps:
                ids = sorted([left.id, right.id])
                items.append(
                    {
                        "id": f"conflict:{node.id}:{ids[0]}:{ids[1]}",
                        "kind": "conflict",
                        "content": {"rules": ids, "identical_condition": identical, "seed_witnesses": overlaps},
                    }
                )
    return {"schema_version": "1.0", "fingerprint": fingerprint, "source_hashes": hashes, "items": items}


def _events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = read_json(path)
    previous: str | None = None
    for event in events:
        body = {k: v for k, v in event.items() if k != "hash"}
        if event.get("previous") != previous or digest(body) != event.get("hash"):
            raise ValueError("Review history integrity check failed")
        previous = event["hash"]
    return events


def review_status(spec: Path) -> dict[str, Any]:
    snapshot = review_snapshot(spec)
    current = {
        e["item_id"]: e for e in _events(spec.parent / "reviews.json") if e["fingerprint"] == snapshot["fingerprint"]
    }
    items = []
    for item in snapshot["items"]:
        event = current.get(item["id"])
        items.append({**item, "status": event["decision"] if event else "pending", "review": event})
    return {
        **snapshot,
        "items": items,
        "ready": all(i["status"] == "accepted" for i in items),
        "pending": sum(i["status"] == "pending" for i in items),
        "rejected": sum(i["status"] == "rejected" for i in items),
    }


def record_review(
    spec: Path,
    item_id: str,
    decision: Literal["accepted", "rejected"],
    reviewer: str,
    reason: str,
    expected_fingerprint: str,
) -> dict[str, Any]:
    if decision not in {"accepted", "rejected"} or not reviewer.strip() or not reason.strip():
        raise ValueError("Review requires an accepted/rejected decision, reviewer identity, and reason")
    with FileLock(str(spec.parent / ".review.lock"), timeout=0):
        snapshot = review_snapshot(spec)
        if snapshot["fingerprint"] != expected_fingerprint:
            raise ValueError("Specification changed since review; inspect the current queue")
        if item_id not in {i["id"] for i in snapshot["items"]}:
            raise ValueError("Unknown review item")
        path = spec.parent / "reviews.json"
        events = _events(path)
        body = {
            "fingerprint": snapshot["fingerprint"],
            "item_id": item_id,
            "decision": decision,
            "reviewer": reviewer,
            "reason": reason,
            "recorded_at": datetime.now(UTC).isoformat(),
            "previous": events[-1]["hash"] if events else None,
        }
        event = {**body, "hash": digest(body)}
        write_json(path, [*events, event])
    return event


def require_review(spec: Path) -> None:
    """Authored specs stay usable; compiler outputs require current review decisions."""
    compilation = spec.parent / "compilation.json"
    if compilation.exists():
        status = review_status(spec)
        if not status["ready"]:
            raise ValueError("Compiled project requires accepted reviews for all current rules, nodes, and conflicts")
