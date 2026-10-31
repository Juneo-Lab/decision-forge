"""Content-bound review decisions and a fail-closed gate for compiled projects."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock

from decision_forge.io import canonical, digest, load_project, load_sources, read_json, write_json
from decision_forge.models import Rule
from decision_forge.policy import matches


def _rule_items(project: Any, hashes: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Review items for single rules, each bound to the sources its evidence cites."""
    items = {}
    for rule in project.organization.rules:
        sources = {evidence.source_id for evidence in rule.evidence}
        items[f"rule:{rule.id}"] = {
            "kind": "rule",
            "content": rule.model_dump(mode="json"),
            "sources": {source: hashes[source] for source in sorted(sources) if source in hashes},
        }
    return items


def item_fingerprint(item: dict[str, Any]) -> str:
    return digest({"content": item["content"], "sources": item.get("sources", {})})


def _shadowing(left: Rule, right: Rule) -> bool:
    """Report whether one rule can never govern because the other always outranks it.

    A higher-priority rule with the same effect whose condition is implied by the lower rule
    makes the lower rule dead. This is the failure mode that silently defeats 'deny wins at
    equal priority' when a generated allow rule simply outranks the deny.
    """
    if left.effect != right.effect or left.priority == right.priority:
        return False
    higher, lower = (left, right) if left.priority > right.priority else (right, left)
    if canonical(higher.when.model_dump()) == canonical(lower.when.model_dump()):
        return True
    if higher.when.any_of or lower.when.any_of:
        return False
    # Every context satisfying the lower rule must also satisfy the higher rule.
    lower_predicates = {canonical(predicate.model_dump()) for predicate in lower.when.all_of}
    return all(canonical(predicate.model_dump()) in lower_predicates for predicate in higher.when.all_of)


def review_snapshot(spec: Path) -> dict[str, Any]:
    project = load_project(spec)
    _, hashes = load_sources(project, spec.parent)
    fingerprint = digest({"project": project.model_dump(), "sources": hashes})
    rule_items = _rule_items(project, hashes)
    rule_fingerprints = {item_id.removeprefix("rule:"): item_fingerprint(item) for item_id, item in rule_items.items()}
    items: list[dict[str, Any]] = [{"id": item_id, **item} for item_id, item in sorted(rule_items.items())]
    rules = {r.id: r for r in project.organization.rules}
    for node in project.nodes:
        node_rules = [rules[i] for i in node.rule_ids]
        items.append(
            {
                "id": f"node:{node.id}",
                "kind": "node",
                "content": node.model_dump(mode="json"),
                "sources": {
                    source: hashes[source]
                    for source in sorted({e.source_id for r in node_rules for e in r.evidence})
                    if source in hashes
                },
            }
        )
        for left, right in combinations(node_rules, 2):
            opposite = left.effect != right.effect
            shadow = _shadowing(left, right)
            if not opposite and not shadow:
                continue
            identical = canonical(left.when.model_dump()) == canonical(right.when.model_dump())
            priority_decides = opposite and left.priority != right.priority
            overlaps: list[dict[str, str]] = []
            if opposite:
                for scenario in project.scenarios:
                    if scenario.node_id != node.id:
                        continue
                    for candidate in node.candidates:
                        context = {
                            "state": scenario.state,
                            "candidate": {**candidate.attributes, "id": candidate.id},
                        }
                        if matches(left.when, context) and matches(right.when, context):
                            overlaps.append({"scenario": scenario.id, "candidate": candidate.id})
            if not opposite or identical or overlaps:
                ids = sorted([left.id, right.id])
                kind = "shadowed" if shadow and not opposite else ("override" if priority_decides else "conflict")
                items.append(
                    {
                        "id": f"conflict:{node.id}:{ids[0]}:{ids[1]}",
                        "kind": kind,
                        "content": {
                            "rules": ids,
                            "rule_fingerprints": [rule_fingerprints[left.id], rule_fingerprints[right.id]],
                            "identical_condition": identical,
                            "rule_shadowing": shadow,
                            "priority_decides": priority_decides,
                            "seed_witnesses": overlaps,
                        },
                        "sources": {},
                    }
                )
    for item in items:
        item["fingerprint"] = item_fingerprint(item)
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
    # Decisions are bound to one item's content, so unrelated edits no longer invalidate them.
    current: dict[str, dict[str, Any]] = {}
    for event in _events(spec.parent / "reviews.json"):
        current[event["item_id"]] = event
    items = []
    for item in snapshot["items"]:
        recorded = current.get(item["id"])
        current_event = recorded if recorded and recorded["fingerprint"] == item["fingerprint"] else None
        status = current_event["decision"] if current_event else "pending"
        items.append({**item, "status": status, "review": current_event})
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
        items = {item["id"]: item for item in snapshot["items"]}
        if item_id not in items:
            raise ValueError("Unknown review item")
        # Accept either the item's own fingerprint or the queue fingerprint taken as a whole,
        # so an operator can still confirm "this exact specification" in one gesture.
        allowed = {items[item_id]["fingerprint"], snapshot["fingerprint"]}
        if expected_fingerprint not in allowed:
            raise ValueError("Specification changed since review; inspect the current queue")
        path = spec.parent / "reviews.json"
        events = _events(path)
        body = {
            "fingerprint": items[item_id]["fingerprint"],
            "queue_fingerprint": snapshot["fingerprint"],
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
