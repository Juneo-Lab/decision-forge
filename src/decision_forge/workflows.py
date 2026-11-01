"""Resettable business-tool workflows and independent trace grading.

The runtime and grader consume the same versioned task, but the grader recomputes every decision
and transition from the initial snapshot. Solver observations contain no verdicts or target labels.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Literal, Self

from pydantic import Field, JsonValue, model_validator

from decision_forge.corpus import CallJournal
from decision_forge.generation import set_path
from decision_forge.io import digest
from decision_forge.models import Contract, Identifier, Project, Split
from decision_forge.policy import best_choices, evaluate
from decision_forge.providers import StructuredProvider


class WorkflowStep(Contract):
    id: Identifier
    node_id: Identifier
    tool: Literal["crm.assign", "documents.share", "approvals.act", "quality.submit"]
    next_steps: dict[str, str | None]
    effects: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    escalation_choices: list[str] = Field(default_factory=list)


class WorkflowTask(Contract):
    schema_version: Literal["1.0"] = "1.0"
    id: Identifier
    family_id: Identifier
    split: Split
    initial_state: dict[str, JsonValue]
    start: str
    steps: list[WorkflowStep] = Field(min_length=1)
    success_states: dict[str, JsonValue] = Field(default_factory=dict)
    max_steps: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def references(self) -> Self:
        ids = {s.id for s in self.steps}
        if len(ids) != len(self.steps) or self.start not in ids:
            raise ValueError("Workflow requires unique steps and a valid starting step")
        for step in self.steps:
            if any(n is not None and n not in ids for n in step.next_steps.values()):
                raise ValueError("Unknown workflow transition")
            if not set(step.effects) <= set(step.next_steps) or not set(step.escalation_choices) <= set(
                step.next_steps
            ):
                raise ValueError("Effects and escalation choices must reference declared transitions")
            for effects in step.effects.values():
                if any(not key or any(not part for part in key.split(".")) for key in effects):
                    raise ValueError("Effect paths must be nonempty dotted paths")
                if any(a != b and b.startswith(a + ".") for a in effects for b in effects):
                    raise ValueError("Effect paths must not overlap")
        return self


def validate_task(project: Project, task: WorkflowTask) -> None:
    nodes = {node.id: node for node in project.nodes}
    kinds = {
        "crm.assign": "allocate_work",
        "documents.share": "share_information",
        "approvals.act": "select_actions",
        "quality.submit": "evaluate_outcomes",
    }
    for step in task.steps:
        node = nodes.get(step.node_id)
        if node is None or node.kind != kinds[step.tool]:
            raise ValueError("Workflow tools must bind to a matching decision node")
        if set(step.next_steps) != {c.id for c in node.candidates}:
            raise ValueError("Workflow must declare a transition for every candidate")


def _apply(state: dict[str, JsonValue], effects: dict[str, JsonValue]) -> dict[str, JsonValue]:
    updated = copy.deepcopy(state)
    for path, value in effects.items():
        set_path(updated, path, value)
    return updated


class WorkflowEnvironment:
    """In-memory mock CRM, sharing, approval and quality tools; no external side effects."""

    def __init__(self, project: Project, task: WorkflowTask):
        validate_task(project, task)
        self.project = project.model_copy(deep=True)
        self.task = task.model_copy(deep=True)
        self.reset()

    def reset(self) -> dict[str, Any]:
        self.state = copy.deepcopy(self.task.initial_state)
        self.current: str | None = self.task.start
        self.events: list[dict[str, Any]] = []
        return self.observe()

    @property
    def done(self) -> bool:
        return self.current is None or len(self.events) >= self.task.max_steps

    def observe(self) -> dict[str, Any]:
        if self.done:
            return {"task_id": self.task.id, "done": True, "state": copy.deepcopy(self.state)}
        step = next(s for s in self.task.steps if s.id == self.current)
        node = next(n for n in self.project.nodes if n.id == step.node_id)
        return {
            "task_id": self.task.id,
            "step_id": step.id,
            "tool": step.tool,
            "state": copy.deepcopy(self.state),
            "question": node.question,
            "candidates": [c.model_dump(mode="json") for c in node.candidates],
            "policy_context": [r.description for r in self.project.organization.rules if r.id in node.rule_ids],
            "remaining_steps": self.task.max_steps - len(self.events),
        }

    def call(self, tool: str, choice: str) -> dict[str, Any]:
        if self.done:
            raise ValueError("Workflow has ended; reset before another action")
        step = next(s for s in self.task.steps if s.id == self.current)
        node = next(n for n in self.project.nodes if n.id == step.node_id)
        rules = [r for r in self.project.organization.rules if r.id in node.rule_ids]
        verdicts = evaluate(self.state, node.candidates, rules)
        permitted = tool == step.tool and any(v.candidate_id == choice and v.allowed for v in verdicts)
        before = copy.deepcopy(self.state)
        if permitted:
            self.state = _apply(self.state, step.effects.get(choice, {}))
            self.current = step.next_steps[choice]
        event = {
            "sequence": len(self.events),
            "step_id": step.id,
            "tool": tool,
            "choice": choice,
            "before": before,
            "after": copy.deepcopy(self.state),
            "next_step": self.current,
            "permitted": permitted,
            "escalated": permitted and choice in step.escalation_choices,
        }
        self.events.append(event)
        return {"permitted": permitted, "observation": self.observe()}


def grade_workflow(project: Project, task: WorkflowTask, trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay independently without trusting runtime verdicts, state claims or success flags."""
    validate_task(project, task)
    state = copy.deepcopy(task.initial_state)
    current: str | None = task.start
    permitted_count = best_count = escalations = 0
    invalid = None
    if len(trace) > task.max_steps:
        invalid = "Trace exceeds task step budget"
    for index, event in enumerate(trace):
        if invalid:
            break
        if current is None:
            invalid = "Trace continues after termination"
            break
        step = next(s for s in task.steps if s.id == current)
        node = next(n for n in project.nodes if n.id == step.node_id)
        verdicts = evaluate(state, node.candidates, [r for r in project.organization.rules if r.id in node.rule_ids])
        allowed = event.get("tool") == step.tool and any(
            v.candidate_id == event.get("choice") and v.allowed for v in verdicts
        )
        expected_after = _apply(state, step.effects.get(event["choice"], {})) if allowed else state
        expected_next = step.next_steps[event["choice"]] if allowed else current
        expected = {
            "sequence": index,
            "step_id": step.id,
            "before": state,
            "after": expected_after,
            "next_step": expected_next,
            "permitted": allowed,
            "escalated": allowed and event.get("choice") in step.escalation_choices,
        }
        if any(digest(event.get(k)) != digest(v) for k, v in expected.items()):
            invalid = f"Trace state or verdict mismatch at step {index}"
            break
        permitted_count += allowed
        best_count += allowed and event.get("choice") in best_choices(verdicts)
        escalations += bool(expected["escalated"])
        state, current = copy.deepcopy(expected_after), expected_next
    completed = current is None and invalid is None
    # Empty success criteria still require actual workflow termination.
    success = completed and all(k in state and digest(state[k]) == digest(v) for k, v in task.success_states.items())
    return {
        "protocol": "independent-workflow-replay-v1",
        "task_digest": digest(task.model_dump(mode="json")),
        "project_digest": digest(project.model_dump(mode="json")),
        "trace_digest": digest(trace),
        "trace_valid": invalid is None,
        "trace_error": invalid,
        "completed": completed,
        "task_utility": int(success),
        "policy_compliance": permitted_count / len(trace) if trace else None,
        "best_choice_rate": best_count / len(trace) if trace else None,
        "escalations": escalations,
        "steps": len(trace),
    }


class WorkflowChoice(Contract):
    choice: str


def solve_workflow(
    project: Project, task: WorkflowTask, provider: StructuredProvider, journal: CallJournal | None = None
) -> dict[str, Any]:
    """Run one explicitly supplied provider; no gold choices appear in requests."""
    from decision_forge.io import canonical

    env = WorkflowEnvironment(project, task)
    calls: list[dict[str, Any]] = []
    started = time.monotonic()
    error = None
    while not env.done:
        observation = env.observe()
        before = time.monotonic()
        try:
            system = (
                "Select the next permitted business action using the supplied policy and state. Return one choice ID."
            )
            if journal:
                output = journal.invoke(
                    provider, digest(observation), system, canonical(observation), WorkflowChoice.model_json_schema()
                )
            else:
                output = provider.generate(system, canonical(observation), WorkflowChoice.model_json_schema())
            choice = WorkflowChoice.model_validate(output).choice
            env.call(observation["tool"], choice)
        except (ValueError, OSError) as exc:
            error = type(exc).__name__
            break
        finally:
            calls.append({"usage": getattr(provider, "last_usage", None), "latency_seconds": time.monotonic() - before})
    metering = journal.events if journal else calls
    costs = [c["usage"].get("cost") if c["usage"] else None for c in metering]
    return {
        "task_id": task.id,
        "family_id": task.family_id,
        "split": task.split,
        "solver_error": error,
        "trace": env.events,
        "grading": grade_workflow(project, task, env.events),
        "calls": calls,
        "known_cost_usd": sum(c for c in costs if c is not None),
        "unmetered_calls": sum(c is None for c in costs),
        "latency_seconds": time.monotonic() - started,
    }
