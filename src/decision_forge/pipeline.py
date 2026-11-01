"""Content-addressed scenario builds and integrity-checked run manifests."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from filelock import FileLock

from decision_forge import __version__
from decision_forge.dedup import NEAR_DUPLICATE_THRESHOLD, deduplicate
from decision_forge.distribution import select_distribution
from decision_forge.generation import generate_family, referenced_sources, requested_variants
from decision_forge.io import (
    atomic_text,
    canonical,
    digest,
    load_project,
    load_sources,
    read_json,
    read_jsonl,
    write_json,
)
from decision_forge.models import Episode, Project, Scenario
from decision_forge.review import require_review
from decision_forge.splits import read_lock, require_current_lock, resolve_splits, write_lock


def engine_fingerprint() -> str:
    """Invalidate editable-install caches when implementation semantics change."""
    package = Path(__file__).parent
    return digest({path.name: path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py"))})


def scenario_fingerprint(
    project: Project,
    scenario: Scenario,
    hashes: dict[str, str],
    *,
    engine: str,
    resolved_split: str,
) -> str:
    node = project.node(scenario.node_id)
    rules = project.select_rules(node.rule_ids)
    source_ids = referenced_sources(scenario, rules)
    return digest(
        {
            "engine": __version__,
            "engine_fingerprint": engine,
            "organization": project.organization.id,
            "policy_version": project.organization.policy_version,
            "scenario": scenario.model_dump(),
            "resolved_split": resolved_split,
            "node": node.model_dump(),
            "rules": [r.model_dump() for r in rules],
            "recipe": {
                "seed": project.recipe.seed,
                "variants_per_scenario": project.recipe.variants_per_scenario,
                "candidate_pool_per_scenario": project.recipe.candidate_pool_per_scenario,
            },
            "sources": {
                s.id: {"hash": hashes[s.id], "version": s.version, "access": s.access}
                for s in project.sources
                if s.id in source_ids
            },
        }
    )


def build(
    spec: Path,
    output: Path,
    cache: Path | None = None,
    recipe_plan: Path | None = None,
    expansion: Path | None = None,
    near_duplicate_threshold: float = NEAR_DUPLICATE_THRESHOLD,
    locked_splits: bool = False,
) -> dict[str, Any]:
    project = load_project(spec)
    require_review(spec)
    lock = resolve_splits(project, spec)
    if locked_splits:
        require_current_lock(spec, lock)
    contents, hashes = load_sources(project, spec.parent)
    if recipe_plan is not None:
        # A plan is an overlay, never an edit: the specification on disk stays the reviewed source.
        from decision_forge.planner import load_plan

        project.recipe.distribution = load_plan(recipe_plan, project, hashes)
    expanded: list[Episode] = []
    expansion_summary: dict[str, Any] | None = None
    if expansion is not None:
        from decision_forge.expansion import load_expansion

        expanded, expansion_summary = load_expansion(expansion, project, hashes)
    output.mkdir(parents=True, exist_ok=True)
    cache = cache or output.parent / ".forge-cache"
    cache.mkdir(parents=True, exist_ok=True)
    with FileLock(str(output / ".build.lock"), timeout=0):
        manifest = _build(
            project,
            contents,
            hashes,
            output,
            cache,
            expanded,
            expansion_summary,
            near_duplicate_threshold,
        )
    # Record new assignments only once a dataset that uses them has been published.
    if read_lock(spec) != lock:
        write_lock(spec, lock)
    return manifest


def _build(
    project: Project,
    contents: dict[str, str],
    hashes: dict[str, str],
    output: Path,
    cache: Path,
    expanded: list[Episode] | None = None,
    expansion_summary: dict[str, Any] | None = None,
    near_duplicate_threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> dict[str, Any]:
    # Callers resolve splits first, so every family is pinned and the plan is the recipe's own record.
    plan = dict(project.recipe.family_splits)
    if set(plan) != {scenario.family_id for scenario in project.scenarios}:
        raise ValueError("Family splits must be resolved before a build")
    engine = engine_fingerprint()
    fingerprint = digest(
        {
            "engine": __version__,
            "engine_fingerprint": engine,
            "project": project.model_dump(),
            "sources": hashes,
        }
    )
    manifest_path = output / "manifest.json"
    if manifest_path.exists() and read_json(manifest_path)["fingerprint"] != fingerprint:
        raise ValueError("Inputs changed. Use a new output directory; compatible cached families are reused.")
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "engine_version": __version__,
        "engine_fingerprint": engine,
        "fingerprint": fingerprint,
        "status": "running",
        "source_hashes": hashes,
        "scenario_fingerprints": {},
        "recipe": project.recipe.model_dump(mode="json"),
        "family_splits": plan,
        "family_registry": plan,
    }
    write_json(manifest_path, manifest)
    accepted: list[Episode] = []
    rejected: list[Episode] = []
    cache_hits = 0
    requested = 0
    shortfalls: dict[str, int] = {}
    for scenario in project.scenarios:
        key = scenario_fingerprint(project, scenario, hashes, engine=engine, resolved_split=plan[scenario.family_id])
        manifest["scenario_fingerprints"][scenario.id] = key
        cache_path = cache / f"{key}.json"
        wanted = requested_variants(project, scenario)
        requested += wanted
        if cache_path.exists():
            record = read_json(cache_path)
            if record.get("fingerprint") != key or digest(record["episodes"]) != record.get("digest"):
                raise ValueError(f"Cache integrity failure for {scenario.id}; remove {cache_path}")
            episodes = [Episode.model_validate(row) for row in record["episodes"]]
            cache_hits += 1
        else:
            episodes = generate_family(project, scenario, hashes, plan[scenario.family_id])
            rows = [episode.model_dump(mode="json") for episode in episodes]
            write_json(cache_path, {"fingerprint": key, "digest": digest(rows), "episodes": rows})
        if len(episodes) != wanted:
            shortfalls[scenario.id] = wanted - len(episodes)
        for episode in episodes:
            (accepted if episode.valid_choices else rejected).append(episode)
    generated_accepted = len(accepted)
    synthesized = list(expanded or [])
    accepted.extend(synthesized)
    # Duplicate inputs are removed before quotas are applied, so a quota can never be satisfied by
    # the same example twice; cross-split duplicates keep the held-out copy and drop the leak.
    accepted, dedup_report = deduplicate(accepted, threshold=near_duplicate_threshold)
    merged_accepted = len(accepted)
    accepted, distribution = select_distribution(project, accepted, rejected)
    write_json(output / "distribution.json", distribution)
    write_json(output / "dedup.json", dedup_report)
    if not distribution["feasible"] and project.recipe.require_full_distribution:
        manifest.update(status="distribution_shortfall", distribution=distribution)
        write_json(manifest_path, manifest)
        raise ValueError("Distribution quotas are infeasible in the candidate pool; inspect distribution.json")
    accepted.sort(key=lambda e: e.id)
    rejected.sort(key=lambda e: e.id)
    for name, records in [("episodes", accepted), ("rejected", rejected)]:
        atomic_text(output / f"{name}.jsonl", "".join(canonical(e.model_dump()) + "\n" for e in records))
    report: dict[str, Any] = {
        "accepted": len(accepted),
        "rejected_no_valid_choice": len(rejected),
        "ambiguous_best_choice": sum(len(e.valid_choices) > 1 for e in accepted),
        "families": len({e.family_id for e in accepted}),
        "requested_episodes": requested,
        "realized_episodes": generated_accepted + len(rejected),
        "episodes_collapsed_as_duplicates": requested - generated_accepted - len(rejected),
        "filtered_by_distribution": merged_accepted - len(accepted),
        "synthesized": len(synthesized),
        "distribution": distribution,
        "dedup": {
            "protocol": dedup_report["protocol"],
            "threshold": dedup_report["threshold"],
            "removed": dedup_report["removed"],
            "split_leaks": dedup_report["cross_split_pairs"],
        },
        "generation_cost": _generation_cost(expansion_summary, generated_accepted, len(synthesized)),
        "scenarios_below_requested_variants": shortfalls,
        "cache_hits": cache_hits,
        "by_kind": dict(Counter(e.kind.value for e in accepted)),
        "by_split": dict(Counter(e.split for e in accepted)),
        "by_stratum": dict(Counter(e.stratum for e in accepted)),
        "coverage": dict(Counter(f"{e.kind.value}/{e.stratum}" for e in accepted)),
        "label_basis": "declarative_policy; source quotes checked; semantic review external",
    }
    write_json(output / "report.json", report)
    snapshot = project.model_copy(deep=True)
    for source in snapshot.sources:
        source.path = f"sources/{source.id}.txt"
        source.content_hash = hashes[source.id]
        atomic_text(output / source.path, contents[source.id])
    write_json(output / "project.snapshot.json", snapshot.model_dump(mode="json"))
    manifest["project_snapshot_digest"] = digest(snapshot.model_dump(mode="json"))
    lines = [
        "# Decision Forge build report",
        "",
        f"Accepted episodes: {len(accepted)}",
        f"Rejected episodes (no valid choice): {len(rejected)}",
        "",
        "| Decision kind | Accepted episodes |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {kind} | {count} |" for kind, count in sorted(report["by_kind"].items()))
    lines += [
        "",
        "Labels are derived from declared policies. Evidence quotes are checked against",
        "source files; policy interpretation requires independent business validation.",
        "",
    ]
    atomic_text(output / "report.md", "\n".join(lines))
    manifest["dedup_digest"] = digest(dedup_report)
    manifest["expansion"] = {
        "published": len(synthesized),
        "accepted_digest": (expansion_summary or {}).get("accepted_digest"),
    }
    manifest.update(status="complete", dataset_digest=digest([e.model_dump() for e in accepted]), report=report)
    write_json(manifest_path, manifest)
    return manifest


def _generation_cost(expansion: dict[str, Any] | None, offline: int, synthesized: int) -> dict[str, Any]:
    expansion = expansion or {}
    usage = expansion.get("usage") or {}
    cost = usage.get("cost_usd")
    # A missing price stays unknown; reporting 0 would read as free data.
    unpriced = "unreported_by_endpoint" if synthesized else "no_model_calls"
    basis = "provider_reported" if cost is not None else unpriced
    return {
        "provider_calls": int(expansion.get("calls") or 0),
        "provider_cost_usd": cost,
        "cost_basis": basis,
        "per_accepted_example_usd": usage.get("per_published_example_usd"),
        "offline_examples": offline,
        "synthesized_examples": synthesized,
        "scope": "offline_generation_plus_verified_synthesis" if synthesized else "offline_generation_only",
    }


def load_run(path: Path) -> list[Episode]:
    manifest = read_json(path / "manifest.json")
    if manifest["status"] != "complete":
        raise ValueError("Build is incomplete; rerun the build before exporting")
    rows = read_jsonl(path / "episodes.jsonl")
    if digest(rows) != manifest["dataset_digest"]:
        raise ValueError("Dataset integrity check failed")
    return [Episode.model_validate(row) for row in rows]


def load_run_project(path: Path) -> Project:
    """Require a hash-bound source snapshot for policy-dependent operations."""
    manifest = read_json(path / "manifest.json")
    snapshot = read_json(path / "project.snapshot.json")
    if not manifest.get("project_snapshot_digest") or digest(snapshot) != manifest["project_snapshot_digest"]:
        raise ValueError(
            "Project snapshot integrity check failed; rebuild legacy runs before policy-dependent operations"
        )
    project = Project.model_validate(snapshot)
    load_sources(project, path)
    return project


def impact(spec: Path, previous_run: Path) -> dict[str, list[str]]:
    project = load_project(spec)
    resolve_splits(project, spec)
    _, hashes = load_sources(project, spec.parent)
    previous = read_json(previous_run / "manifest.json")["scenario_fingerprints"]
    plan, engine = project.recipe.family_splits, engine_fingerprint()
    current = {
        s.id: scenario_fingerprint(project, s, hashes, engine=engine, resolved_split=plan[s.family_id])
        for s in project.scenarios
    }
    return {
        "changed": sorted(k for k in current if k in previous and current[k] != previous[k]),
        "added": sorted(set(current) - set(previous)),
        "removed": sorted(set(previous) - set(current)),
        "reusable": sorted(k for k in current if current[k] == previous.get(k)),
    }
