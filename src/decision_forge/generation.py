"""Seeded scenario families with stable split assignment before augmentation."""

from __future__ import annotations

import copy
import math
import random
from typing import Any

from decision_forge.io import digest
from decision_forge.models import Episode, Node, Project, Scenario
from decision_forge.policy import best_choices, evaluate


def split_for(family_id: str, project: Project) -> str:
    # Recipe sampling seeds must never move an existing family into another split.
    key = f"{project.organization.id}:{family_id}:split-v1"
    value = int(digest(key)[:16], 16) / 2**64
    splits = project.recipe.splits
    if value < splits.train:
        return "train"
    if value < splits.train + splits.development:
        return "development"
    return "test"


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


def generate_family(project: Project, scenario: Scenario, source_hashes: dict[str, str]) -> list[Episode]:
    node: Node = next(n for n in project.nodes if n.id == scenario.node_id)
    rules = [r for r in project.organization.rules if r.id in node.rule_ids]
    widths = [len(dim.values) for dim in node.dimensions]
    population = math.prod(widths)
    if population > 10**12:
        raise ValueError("Scenario space exceeds the supported sampling range")
    count = min(population, project.recipe.variants_per_scenario)
    rng = random.Random(digest([project.recipe.seed, scenario.id]))
    indices = sorted(rng.sample(range(population), count))
    episodes: dict[str, Episode] = {}
    referenced_sources = set(scenario.source_ids)
    referenced_sources.update(e.source_id for r in rules for e in r.evidence)
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
                candidate = max(
                    (c for c in candidates if dimension.path.startswith(f"candidates.{c.id}.")),
                    key=lambda c: len(c.id),
                )
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
            split=split_for(scenario.family_id, project),  # type: ignore[arg-type]
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
        )
    return list(episodes.values())
