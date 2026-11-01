"""Seeded scenario families, generated deterministically from a project's declared dimensions."""

from __future__ import annotations

import copy
import math
import random
from typing import Any

from decision_forge.io import digest
from decision_forge.models import Candidate, Episode, Node, Project, Scenario, Split
from decision_forge.policy import best_choices, evaluate


def requested_variants(project: Project, scenario: Scenario) -> int:
    """Number of distinct episodes a scenario is configured to contribute."""
    node: Node = next(n for n in project.nodes if n.id == scenario.node_id)
    population = math.prod(len(dim.values) for dim in node.dimensions)
    return min(population, project.recipe.candidate_pool_per_scenario or project.recipe.variants_per_scenario)


def candidate_for(node: Node, path: str) -> Candidate:
    """Resolve the single candidate a candidates.<id>.<attribute> path belongs to."""
    matches = [candidate for candidate in node.candidates if path.startswith(f"candidates.{candidate.id}.")]
    if len(matches) != 1:
        raise ValueError(f"Path must identify exactly one candidate: {path}")
    return matches[0]


def set_path(root: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = root
    for part in parts[:-1]:
        child = current.get(part)
        if child is None:
            child = {}
            current[part] = child
        if not isinstance(child, dict):
            raise ValueError(f"Dimension traverses a scalar: {path}")
        current = child
    current[parts[-1]] = copy.deepcopy(value)


def policy_fingerprint(project: Project, node_id: str, source_hashes: dict[str, str]) -> str:
    node = next(n for n in project.nodes if n.id == node_id)
    return digest(
        {
            "organization_id": project.organization.id,
            "policy_version": project.organization.policy_version,
            "node": node.model_dump(mode="json"),
            "rules": [r.model_dump(mode="json") for r in project.organization.rules if r.id in node.rule_ids],
            "sources": {
                s.id: {"hash": source_hashes[s.id], "version": s.version, "access": s.access}
                for s in project.sources
                if s.id in source_hashes
            },
        }
    )


def generate_family(project: Project, scenario: Scenario, source_hashes: dict[str, str], split: Split) -> list[Episode]:
    node: Node = next(n for n in project.nodes if n.id == scenario.node_id)
    rules = [r for r in project.organization.rules if r.id in node.rule_ids]
    widths = [len(dim.values) for dim in node.dimensions]
    population = math.prod(widths)
    if population > 10**12:
        raise ValueError("Scenario space exceeds the supported sampling range")
    count = requested_variants(project, scenario)
    rng = random.Random(digest([project.recipe.seed, scenario.id]))
    indices = sorted(rng.sample(range(population), count))
    episodes: dict[str, Episode] = {}
    referenced_sources = set(scenario.source_ids)
    referenced_sources.update(e.source_id for r in rules for e in r.evidence)
    grounding = policy_fingerprint(project, node.id, {key: source_hashes[key] for key in sorted(referenced_sources)})
    for index in indices:
        state = copy.deepcopy(scenario.state)
        candidates = [c.model_copy(deep=True) for c in node.candidates]
        intervention: dict[str, Any] = {}
        for dimension, width in zip(node.dimensions, widths, strict=True):
            value = dimension.values[index % width]
            index //= width
            intervention[dimension.path] = value
            if dimension.path.startswith("state."):
                set_path(state, dimension.path.removeprefix("state."), value)
            else:
                candidate_id = candidate_for(node, dimension.path).id
                candidate = next(c for c in candidates if c.id == candidate_id)
                attr_path = dimension.path.removeprefix(f"candidates.{candidate.id}.")
                if attr_path == "id":
                    raise ValueError("Candidate IDs cannot be mutated")
                set_path(candidate.attributes, attr_path, value)
        verdicts = evaluate(state, candidates, rules)
        identity = digest(
            [
                project.organization.id,
                project.organization.policy_version,
                node.question,
                [rule.model_dump() for rule in rules],
                scenario.id,
                state,
                [c.model_dump() for c in candidates],
            ]
        )[:24]
        episodes[identity] = Episode(
            id=f"ep-{identity}",
            organization_id=project.organization.id,
            policy_version=project.organization.policy_version,
            node_id=node.id,
            kind=node.kind,
            scenario_id=scenario.id,
            family_id=scenario.family_id,
            split=split,
            stratum=scenario.stratum,
            state=state,
            question=node.question,
            candidates=candidates,
            policy_context=[r.description for r in rules],
            valid_choices=best_choices(verdicts),
            verdicts=verdicts,
            source_ids=sorted(referenced_sources),
            source_hashes={key: source_hashes[key] for key in sorted(referenced_sources)},
            rule_ids=node.rule_ids,
            intervention=intervention,
            policy_fingerprint=grounding,
        )
    return list(episodes.values())
