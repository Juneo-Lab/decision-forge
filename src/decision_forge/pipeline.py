"""Content-addressed scenario builds and integrity-checked run manifests."""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from filelock import FileLock

from decision_forge import __version__
from decision_forge.generation import generate_family
from decision_forge.io import (
    atomic_text,
    canonical,
    digest,
    load_project,
    load_sources,
    read_json,
    write_json,
)
from decision_forge.models import Episode, Project, Scenario


@lru_cache(maxsize=1)
def engine_fingerprint() -> str:
    """Invalidate editable-install caches when implementation semantics change."""
    package = Path(__file__).parent
    return digest({path.name: path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py"))})


def scenario_fingerprint(project: Project, scenario: Scenario, hashes: dict[str, str]) -> str:
    node = next(n for n in project.nodes if n.id == scenario.node_id)
    rules = [r for r in project.organization.rules if r.id in node.rule_ids]
    source_ids = set(scenario.source_ids) | {e.source_id for r in rules for e in r.evidence}
    return digest(
        {
            "engine": __version__,
            "engine_fingerprint": engine_fingerprint(),
            "organization": project.organization.id,
            "policy_version": project.organization.policy_version,
            "scenario": scenario.model_dump(),
            "node": node.model_dump(),
            "rules": [r.model_dump() for r in rules],
            "recipe": project.recipe.model_dump(),
            "sources": {key: hashes[key] for key in sorted(source_ids)},
        }
    )


def build(spec: Path, output: Path, cache: Path | None = None) -> dict[str, Any]:
    project = load_project(spec)
    _, hashes = load_sources(project, spec.parent)
    output.mkdir(parents=True, exist_ok=True)
    cache = cache or output.parent / ".forge-cache"
    cache.mkdir(parents=True, exist_ok=True)
    with FileLock(str(output / ".build.lock"), timeout=0):
        return _build(project, hashes, output, cache)


def _build(project: Project, hashes: dict[str, str], output: Path, cache: Path) -> dict[str, Any]:
    fingerprint = digest(
        {
            "engine": __version__,
            "engine_fingerprint": engine_fingerprint(),
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
        "engine_fingerprint": engine_fingerprint(),
        "fingerprint": fingerprint,
        "status": "running",
        "source_hashes": hashes,
        "scenario_fingerprints": {},
    }
    write_json(manifest_path, manifest)
    accepted: list[Episode] = []
    rejected: list[Episode] = []
    cache_hits = 0
    for scenario in project.scenarios:
        key = scenario_fingerprint(project, scenario, hashes)
        manifest["scenario_fingerprints"][scenario.id] = key
        cache_path = cache / f"{key}.json"
        if cache_path.exists():
            record = read_json(cache_path)
            if record.get("fingerprint") != key or digest(record["episodes"]) != record.get("digest"):
                raise ValueError(f"Cache integrity failure for {scenario.id}; remove {cache_path}")
            episodes = [Episode.model_validate(row) for row in record["episodes"]]
            cache_hits += 1
        else:
            episodes = generate_family(project, scenario, hashes)
            rows = [episode.model_dump(mode="json") for episode in episodes]
            write_json(cache_path, {"fingerprint": key, "digest": digest(rows), "episodes": rows})
        for episode in episodes:
            (accepted if episode.valid_choices else rejected).append(episode)
    accepted.sort(key=lambda e: e.id)
    rejected.sort(key=lambda e: e.id)
    for name, records in [("episodes", accepted), ("rejected", rejected)]:
        atomic_text(output / f"{name}.jsonl", "".join(canonical(e.model_dump()) + "\n" for e in records))
    report: dict[str, Any] = {
        "accepted": len(accepted),
        "rejected_no_valid_choice": len(rejected),
        "ambiguous_best_choice": sum(len(e.valid_choices) > 1 for e in accepted),
        "families": len({e.family_id for e in accepted}),
        "cache_hits": cache_hits,
        "by_kind": dict(Counter(e.kind.value for e in accepted)),
        "by_split": dict(Counter(e.split for e in accepted)),
        "by_stratum": dict(Counter(e.stratum for e in accepted)),
        "coverage": dict(Counter(f"{e.kind.value}/{e.stratum}" for e in accepted)),
        "label_basis": "declarative_policy; source quotes checked; semantic review external",
    }
    write_json(output / "report.json", report)
    write_json(output / "project.snapshot.json", project.model_dump(mode="json"))
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
    manifest.update(status="complete", dataset_digest=digest([e.model_dump() for e in accepted]), report=report)
    write_json(manifest_path, manifest)
    return manifest


def load_run(path: Path) -> list[Episode]:
    manifest = read_json(path / "manifest.json")
    if manifest["status"] != "complete":
        raise ValueError("Build is incomplete; rerun the build before exporting")
    episodes = [
        Episode.model_validate_json(line)
        for line in (path / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if digest([e.model_dump() for e in episodes]) != manifest["dataset_digest"]:
        raise ValueError("Dataset integrity check failed")
    return episodes


def impact(spec: Path, previous_run: Path) -> dict[str, list[str]]:
    project = load_project(spec)
    _, hashes = load_sources(project, spec.parent)
    previous = read_json(previous_run / "manifest.json")["scenario_fingerprints"]
    current = {s.id: scenario_fingerprint(project, s, hashes) for s in project.scenarios}
    return {
        "changed": sorted(k for k in current if k in previous and current[k] != previous[k]),
        "added": sorted(set(current) - set(previous)),
        "removed": sorted(set(previous) - set(current)),
        "reusable": sorted(k for k in current if current[k] == previous.get(k)),
    }
