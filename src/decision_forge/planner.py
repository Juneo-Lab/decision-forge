"""Persistent recipe planning through discoverable typed tools.

The planner closes the loop between "what distribution do we want" and "what can the declared
project produce offline". It measures the real deterministic pool, proposes exact cell quotas,
reports shortfalls, and emits the expansion requests that would fill them. Every tool call is
appended to a ledger so a plan can be audited and resumed instead of being re-derived in memory.
"""

from __future__ import annotations

import json
from collections import Counter
from math import ceil
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, JsonValue

from decision_forge.distribution import cell_key, episode_cell
from decision_forge.generation import generate_family, split_plan
from decision_forge.io import canonical, digest, load_project, load_sources, read_json, write_json
from decision_forge.models import (
    CandidateIdentifier,
    Contract,
    DistributionTarget,
    Episode,
    Identifier,
    Project,
)
from decision_forge.providers import StructuredProvider

PLANNER_VERSION = "recipe-planner-v1"


class PlanObjective(Contract):
    """What the planner should target. `explicit` passes targets through unchanged."""

    mode: Literal["uniform_cells", "explicit"] = "uniform_cells"
    strata: list[Literal["routine", "boundary", "change", "feedback"]] = Field(default_factory=list)
    per_cell: int = Field(default=50, ge=1, le=100000)
    total_examples: int | None = Field(default=None, ge=1)
    targets: list[DistributionTarget] = Field(default_factory=list)


class PlanOptions(Contract):
    states_per_call: int = Field(default=8, ge=1, le=100)
    expand_cell_limit: int = Field(default=2000, ge=1, le=100000)


class ToolCall(Contract):
    tool: str
    arguments: dict[str, JsonValue]
    result_digest: str
    note: str = ""


class AdvisorWeight(Contract):
    node_id: Identifier
    stratum: Literal["routine", "boundary", "change", "feedback"]
    split: Literal["train", "development", "test"]
    valid_choices: list[CandidateIdentifier] = Field(min_length=1, max_length=16)
    weight: float = Field(gt=0, le=1)


class AdvisorWeights(Contract):
    """A model may only re-weight cells the planner already measured."""

    weights: list[AdvisorWeight] = Field(min_length=1, max_length=500)


def measure_pool(project: Project, hashes: dict[str, str]) -> tuple[Counter[str], list[Episode]]:
    """Dry-run the deterministic pool without writing artifacts.

    Planning must observe what the project can actually emit, including rejection of states with
    no permitted candidate, otherwise quotas are fiction.
    """
    plan = split_plan(project)
    cells: Counter[str] = Counter()
    episodes: list[Episode] = []
    for scenario in project.scenarios:
        for episode in generate_family(project, scenario, hashes, plan[scenario.family_id]):
            episodes.append(episode)
            if episode.valid_choices:
                cells[episode_cell(episode)] += 1
    return cells, episodes


def _decode(cell: str) -> tuple[str, str, str, list[str]]:
    node, stratum, split, choices = canonical_loads(cell)
    return node, stratum, split, list(choices)


def canonical_loads(value: str) -> Any:
    return json.loads(value)


def _target(cell: str, count: int) -> DistributionTarget:
    node, stratum, split, choices = _decode(cell)
    return DistributionTarget(
        node_id=node,
        stratum=stratum,  # type: ignore[arg-type]
        split=split,  # type: ignore[arg-type]
        valid_choices=choices,
        count=count,
    )


def validate_targets(project: Project, targets: list[DistributionTarget]) -> None:
    """Reject targets that name a fictional node, candidate, or unreachable split.

    A split is decided per family, so a target for a split the node never reaches can never be
    satisfied by generation and must not be silently planned as a permanent shortfall.
    """
    splits_by_node: dict[str, set[str]] = {}
    plan = split_plan(project)
    for scenario in project.scenarios:
        splits_by_node.setdefault(scenario.node_id, set()).add(plan[scenario.family_id])
    for target in targets:
        node = next((n for n in project.nodes if n.id == target.node_id), None)
        if node is None:
            raise ValueError(f"Distribution target references an unknown node: {target.node_id}")
        unknown = set(target.valid_choices) - {candidate.id for candidate in node.candidates}
        if unknown:
            raise ValueError(f"Distribution target references unknown candidates: {sorted(unknown)}")
        if target.split not in splits_by_node.get(node.id, set()):
            raise ValueError(f"Distribution target asks for split {target.split}, which node {node.id} never reaches")


def propose_quotas(
    project: Project, available: Counter[str], objective: PlanObjective
) -> tuple[dict[str, int], dict[str, Any]]:
    """Choose exact quotas and report what the deterministic pool cannot supply."""
    if objective.mode == "explicit":
        validate_targets(project, objective.targets)
        explicit = {cell_key(t.node_id, t.stratum, t.split, t.valid_choices): t.count for t in objective.targets}
        return explicit, {"mode": "explicit", "cells_requested": len(explicit), "advisor": None}
    candidates = sorted(available)
    if objective.strata:
        candidates = [cell for cell in candidates if _decode(cell)[1] in set(objective.strata)]
    if not candidates:
        raise ValueError("No candidate pool cells match the planning objective")
    per_cell = objective.per_cell
    if objective.total_examples is not None:
        per_cell = max(1, objective.total_examples // len(candidates))
    # Held-out data cannot be synthesized, so a derived quota never exceeds what generation produced
    # for a test cell. The clamp is reported, because it is the reason a test split stays small.
    wanted: dict[str, int] = {}
    clamped: list[dict[str, Any]] = []
    for cell in candidates:
        quoted = per_cell
        if _decode(cell)[2] == "test" and available[cell] < quoted:
            clamped.append({"cell": cell, "requested": quoted, "held_out_available": available[cell]})
            quoted = available[cell]
        wanted[cell] = quoted
    return wanted, {
        "mode": "uniform_cells",
        "cells_requested": len(candidates),
        "per_cell": per_cell,
        "held_out_clamped": clamped,
        "advisor": None,
    }


def apply_advisor(
    weights: AdvisorWeights, wanted: dict[str, int], available: Counter[str]
) -> tuple[dict[str, int], dict[str, Any]]:
    """Scale quotas by model-proposed weights, rejecting cells the planner did not measure."""
    adjusted: dict[str, int] = {}
    unknown: list[str] = []
    for row in weights.weights:
        cell = cell_key(row.node_id, row.stratum, row.split, row.valid_choices)
        if cell not in wanted:
            unknown.append(cell)
            continue
        adjusted[cell] = max(1, round(wanted[cell] * row.weight))
    if unknown:
        raise ValueError(f"Advisor proposed {len(unknown)} cells outside the measured pool")
    merged = {cell: adjusted.get(cell, 0) for cell in wanted}
    return merged, {"mode": "advisor_scaled", "cells_adjusted": len(adjusted)}


def feasibility(wanted: dict[str, int], available: Counter[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for cell in sorted(set(wanted) | set(available)):
        target = wanted.get(cell, 0)
        supply = available.get(cell, 0)
        rows.append(
            {
                "cell": cell,
                "requested": target,
                "available": supply,
                "shortfall": max(0, target - supply),
                "surplus": max(0, supply - target),
            }
        )
    shortfall = sum(row["shortfall"] for row in rows)
    return {
        "protocol": "exact-cell-quota-v1",
        "cells": rows,
        "requested": sum(wanted.values()),
        "available": sum(available.values()),
        "shortfall": shortfall,
        "feasible": shortfall == 0,
    }


def expansion_requests(
    wanted: dict[str, int],
    available: Counter[str],
    options: PlanOptions,
    synthesizable: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the shortfall into work synthesis can do and work it cannot.

    Held-out cells are excluded: synthesizing them would reveal the target answer for the split that
    is supposed to measure generalization. Cells on nodes whose state space is already exhausted are
    also excluded, because a model can only restate situations the pool already holds.
    """
    requests: list[dict[str, Any]] = []
    unfillable: list[dict[str, Any]] = []
    for cell in sorted(wanted):
        shortfall = wanted[cell] - available.get(cell, 0)
        if shortfall <= 0:
            continue
        node, stratum, split, choices = _decode(cell)
        if split == "test":
            unfillable.append({"cell": cell, "shortfall": shortfall, "reason": "held_out_split"})
            continue
        if synthesizable is not None and not synthesizable.get(node, {}).get("synthesizable", False):
            unfillable.append(
                {
                    "cell": cell,
                    "shortfall": shortfall,
                    "reason": synthesizable.get(node, {}).get("reason", "state space exhausted"),
                }
            )
            continue
        requests.append(
            {
                "node_id": node,
                "stratum": stratum,
                "split": split,
                "valid_choices": choices,
                "count": min(shortfall, options.expand_cell_limit),
            }
        )
    return requests, unfillable


def synthesizability(project: Project, pool: list[Episode]) -> dict[str, dict[str, Any]]:
    """Decide whether a node's state space can still grow.

    Synthesis can only add situations the deterministic pool cannot already reach. That happens when
    a rule reads a state path that is not a declared dimension (an open domain), or when declared
    dimensions describe more combinations than sampling produced. Otherwise the pool already contains
    every reachable state, and asking a model for more would only produce duplicates.
    """
    from decision_forge.expansion import allowed_state_paths

    observed: dict[str, set[str]] = {}
    for episode in pool:
        observed.setdefault(episode.node_id, set()).add(canonical(episode.state))
    result: dict[str, dict[str, Any]] = {}
    for node in project.nodes:
        domains = allowed_state_paths(project, node)
        open_paths = sorted(name for name, entry in domains.items() if entry["kind"] == "predicate")
        population = 1
        for dimension in node.dimensions:
            population *= max(1, len(dimension.values))
        seen = len(observed.get(node.id, set()))
        if open_paths:
            result[node.id] = {
                "synthesizable": True,
                "reason": f"rules read undeclared state paths: {open_paths}",
            }
        elif seen < population:
            result[node.id] = {
                "synthesizable": True,
                "reason": f"{seen} of {population} declared state combinations were generated",
            }
        else:
            result[node.id] = {
                "synthesizable": False,
                "reason": f"all {population} declared state combinations already exist",
            }
    return result


def held_out_shortfall(wanted: dict[str, int], available: Counter[str]) -> dict[str, Any]:
    """Held-out shortfall that must be closed by new source material, never by synthesis."""
    cells = []
    for cell in sorted(wanted):
        shortfall = wanted[cell] - available.get(cell, 0)
        if shortfall > 0 and _decode(cell)[2] == "test":
            cells.append({"cell": cell, "shortfall": shortfall})
    return {"cells": cells, "shortfall": sum(row["shortfall"] for row in cells)}


def estimate_cost(requests: list[dict[str, Any]], options: PlanOptions) -> dict[str, Any]:
    wanted = sum(int(request["count"]) for request in requests)
    return {
        "missing_examples": wanted,
        "states_per_call": options.states_per_call,
        "model_calls": ceil(wanted / options.states_per_call) if wanted else 0,
        "scope": "estimate_only; actual usage is recorded by the expansion journal",
    }


TOOLS: dict[str, str] = {
    "inspect_project": "Summarize nodes, candidates, strata, families, and declared quotas.",
    "measure_pool": "Generate the deterministic pool in memory and count examples per exact cell.",
    "propose_quotas": "Derive exact per-cell quotas from the objective, optionally model-weighted.",
    "check_feasibility": "Compare requested quotas with measured supply and list shortfalls.",
    "plan_expansion": "Turn shortfalls into verified-expansion requests with a call estimate.",
    "commit_plan": "Persist the plan, ledger, quota table, and expansion requests atomically.",
}


def _inspect(project: Project) -> dict[str, Any]:
    return {
        "organization": project.organization.id,
        "policy_version": project.organization.policy_version,
        "nodes": [
            {
                "id": node.id,
                "kind": node.kind.value,
                "candidates": [candidate.id for candidate in node.candidates],
                "dimensions": [dimension.path for dimension in node.dimensions],
            }
            for node in project.nodes
        ],
        "families": len({scenario.family_id for scenario in project.scenarios}),
        "strata": sorted({scenario.stratum for scenario in project.scenarios}),
        "declared_quotas": len(project.recipe.distribution),
    }


def _advisor_request(cells: list[str], wanted: dict[str, int]) -> str:
    return canonical(
        {
            "task": "Re-weight exact decision-count cells so the dataset covers each node, stratum and "
            "answer set without inflating trivially easy cells.",
            "cells": [
                {
                    "node_id": _decode(cell)[0],
                    "stratum": _decode(cell)[1],
                    "split": _decode(cell)[2],
                    "valid_choices": _decode(cell)[3],
                    "proposed": wanted[cell],
                }
                for cell in cells
            ],
            "response_schema": "weights: [{node_id, stratum, split, valid_choices, weight in (0, 1]}]",
        }
    )


def plan_recipe(
    spec: Path,
    output: Path,
    objective: PlanObjective,
    *,
    options: PlanOptions | None = None,
    provider: StructuredProvider | None = None,
) -> dict[str, Any]:
    """Plan, audit, and persist an exact distribution for a project specification."""
    options = options or PlanOptions()
    project = load_project(spec)
    _, hashes = load_sources(project, spec.parent)
    fingerprint = digest(
        {
            "planner": PLANNER_VERSION,
            "project": project.model_dump(mode="json"),
            "sources": hashes,
            "objective": objective.model_dump(mode="json"),
            "options": options.model_dump(mode="json"),
            "advisor": getattr(provider, "configuration", None),
        }
    )
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    plan_path = output / "plan.json"
    if plan_path.exists():
        previous = read_json(plan_path)
        if previous["fingerprint"] != fingerprint:
            raise ValueError("Planning inputs changed; choose a new output directory")
        if previous["status"] == "complete":
            return {**previous, "resumed_complete": True}
    elif any(output.iterdir()):
        raise ValueError("A new plan requires an empty output directory")

    ledger: list[ToolCall] = []

    def record(tool: str, arguments: dict[str, JsonValue], result: Any, note: str = "") -> None:
        ledger.append(ToolCall(tool=tool, arguments=arguments, result_digest=digest(result), note=note))
        write_json(
            plan_path,
            {
                "status": "planning",
                "fingerprint": fingerprint,
                "ledger": [call.model_dump(mode="json") for call in ledger],
            },
        )

    inspected = _inspect(project)
    record("inspect_project", {}, inspected)

    available, _pool = measure_pool(project, hashes)
    record(
        "measure_pool",
        {"scenarios": len(project.scenarios)},
        dict(available),
        note=f"{sum(available.values())} usable examples in {len(available)} cells",
    )

    wanted, proposal_note = propose_quotas(project, available, objective)
    if provider is not None:
        proposal = provider.generate(
            "You adjust training-data quotas. Return only JSON matching the requested schema.",
            _advisor_request(sorted(wanted), wanted),
            AdvisorWeights.model_json_schema(),
        )
        wanted, proposal_note = apply_advisor(AdvisorWeights.model_validate(proposal), wanted, available)
        record("propose_quotas", {"mode": "advisor"}, wanted, note=str(proposal_note["mode"]))
    else:
        record("propose_quotas", {"mode": objective.mode}, wanted, note=str(proposal_note["mode"]))

    report = feasibility(wanted, available)
    record(
        "check_feasibility",
        {"cells": len(wanted)},
        report,
        note=f"shortfall {report['shortfall']} of {report['requested']}",
    )

    space = synthesizability(project, _pool)
    requests, unfillable = expansion_requests(wanted, available, options, space)
    cost = estimate_cost(requests, options)
    record("plan_expansion", {"cells": len(requests)}, {"requests": requests, "cost": cost})

    distribution = [_target(cell, count) for cell, count in sorted(wanted.items())]
    plan: dict[str, Any] = {
        "schema_version": "1.0",
        "planner_version": PLANNER_VERSION,
        "status": "complete",
        "fingerprint": fingerprint,
        "project_fingerprint": digest({"project": project.model_dump(mode="json"), "sources": hashes}),
        "objective": objective.model_dump(mode="json"),
        "proposal": proposal_note,
        "distribution": [target.model_dump(mode="json") for target in distribution],
        "feasibility": report,
        "expansion_requests": requests,
        "unfillable": unfillable,
        "held_out_shortfall": held_out_shortfall(wanted, available),
        "held_out_clamped": proposal_note.get("held_out_clamped", []),
        "synthesizability": space,
        "cost_estimate": cost,
        "ledger": [call.model_dump(mode="json") for call in ledger],
        "tools": {name: description for name, description in sorted(TOOLS.items())},
    }
    record(
        "commit_plan",
        {"cells": len(distribution)},
        {"distribution_digest": digest([t.model_dump(mode="json") for t in distribution])},
    )
    plan["ledger"] = [call.model_dump(mode="json") for call in ledger]
    write_json(plan_path, plan)
    write_json(output / "expansion-requests.json", requests)
    _write_table(output / "quotas.md", report)
    return plan


def _write_table(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Recipe plan quotas",
        "",
        f"Requested: {report['requested']} | available offline: {report['available']} | "
        f"shortfall: {report['shortfall']}",
        "",
        "| cell | requested | available | shortfall |",
        "| --- | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| `{row['cell']}` | {row['requested']} | {row['available']} | {row['shortfall']} |" for row in report["cells"]
    )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o644)


def load_plan(path: Path, project: Project, hashes: dict[str, str]) -> list[DistributionTarget]:
    """Load a plan for a build, refusing plans produced for different inputs."""
    data = read_json(path)
    if data.get("status") != "complete":
        raise ValueError("Recipe plan is incomplete; rerun plan-recipe")
    expected = digest({"project": project.model_dump(mode="json"), "sources": hashes})
    if data.get("project_fingerprint") != expected:
        raise ValueError("Recipe plan was produced for different project or source content")
    return [DistributionTarget.model_validate(row) for row in data["distribution"]]
