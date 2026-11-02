"""Finite-pool conditional sampling with explicit shortfalls and deterministic quotas."""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any, NamedTuple

from decision_forge.io import canonical, digest
from decision_forge.models import DistributionTarget, Episode, Project, Split, Stratum


class Cell(NamedTuple):
    """One exact quota cell: a node, stratum, split and full answer set."""

    node_id: str
    stratum: Stratum
    split: Split
    valid_choices: list[str]

    @classmethod
    def parse(cls, key: str) -> Cell:
        node_id, stratum, split, choices = json.loads(key)
        return cls(node_id, stratum, split, list(choices))


def cell_key(node: str, stratum: str, split: str, choices: list[str]) -> str:
    return canonical([node, stratum, split, sorted(choices)])


def target_cell(target: DistributionTarget) -> str:
    return cell_key(target.node_id, target.stratum, target.split, target.valid_choices)


def episode_cell(episode: Episode) -> str:
    return cell_key(episode.node_id, episode.stratum, episode.split, episode.valid_choices)


def select_distribution(
    project: Project, accepted: list[Episode], rejected: list[Episode]
) -> tuple[list[Episode], dict[str, Any]]:
    """Sample without replacement; never fabricate examples to hide infeasible quotas.

    Every cell includes the full set-valued label, so ties cannot silently become single labels.
    Explicit quotas select only listed cells. Sampling uses IDs and a seed, independent of input order.
    """
    available = Counter(episode_cell(e) for e in accepted)
    invalid = Counter(episode_cell(e) for e in rejected)
    wanted = {target_cell(t): t.count for t in project.recipe.distribution} or dict(available)
    selected: list[Episode] = []
    buckets: dict[str, list[Episode]] = {}
    for episode in accepted:
        buckets.setdefault(episode_cell(episode), []).append(episode)
    for key, count in sorted(wanted.items()):
        ranked = sorted(buckets.get(key, []), key=lambda e: digest([project.recipe.seed, key, e.id]))
        selected.extend(ranked[:count])
    realized = Counter(episode_cell(e) for e in selected)
    keys = sorted(set(wanted) | set(available) | set(invalid))
    cells = [
        {
            "cell": key,
            "requested": wanted.get(key, 0),
            "generated": available[key] + invalid[key],
            "rejected_no_valid_choice": invalid[key],
            "filtered": available[key] - realized[key],
            "accepted": realized[key],
            "shortfall": max(0, wanted.get(key, 0) - realized[key]),
        }
        for key in keys
    ]
    expected_total = sum(wanted.values())
    realized_total = len(selected)
    # Jensen-Shannon divergence is finite even when requested cells have zero realizations.
    divergence = None
    if expected_total and realized_total:
        divergence = 0.0
        for key in keys:
            p, q = wanted.get(key, 0) / expected_total, realized[key] / realized_total
            mean = (p + q) / 2
            if p:
                divergence += p * math.log2(p / mean) / 2
            if q:
                divergence += q * math.log2(q / mean) / 2
    return selected, {
        "protocol": "exact-cell-quota-v1",
        "cell_dimensions": ["node_id", "stratum", "split", "valid_choices"],
        "explicit_targets": bool(project.recipe.distribution),
        "feasible": all(c["shortfall"] == 0 for c in cells),
        "requested": expected_total,
        "accepted": realized_total,
        "shortfall": sum(c["shortfall"] for c in cells),
        "jensen_shannon_divergence_bits": divergence,
        "cells": cells,
    }
