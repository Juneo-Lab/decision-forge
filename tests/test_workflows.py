import copy
from pathlib import Path

import pytest

from decision_forge.io import read_json
from decision_forge.workflows import WorkflowEnvironment, WorkflowTask, grade_workflow, solve_workflow

ROOT = Path(__file__).parents[1] / "examples/customer_support"


def task(name="routine"):
    return WorkflowTask.model_validate(read_json(ROOT / f"workflow-{name}.json"))


@pytest.mark.parametrize(
    "name,choices",
    [
        ("routine", ["agent", "summary", "execute", "accept"]),
        ("escalation", ["agent", "summary", "request_approval"]),
        ("revision", ["agent", "summary", "execute", "revise"]),
    ],
)
def test_four_surface_workflow_resets_and_grades_independently(project, name, choices):
    spec = task(name)
    env = WorkflowEnvironment(project, spec)
    initial = env.observe()
    assert "valid_choices" not in initial and "verdicts" not in initial
    for choice in choices:
        assert env.call(env.observe()["tool"], choice)["permitted"]
    assert env.done
    result = grade_workflow(project, spec, env.events)
    assert result["trace_valid"] and result["task_utility"] == 1 and result["policy_compliance"] == 1
    assert result["escalations"] == (1 if name == "escalation" else 0)
    with pytest.raises(ValueError, match="ended"):
        env.call("crm.assign", "human")
    assert env.reset() == initial and not env.events


def test_permission_and_threshold_mutations_are_detected(project):
    spec = task("escalation")
    env = WorkflowEnvironment(project, spec)
    env.call("crm.assign", "agent")
    before = copy.deepcopy(env.state)
    assert not env.call("documents.share", "full")["permitted"]
    assert env.state == before
    env.call("documents.share", "summary")
    assert not env.call("approvals.act", "execute")["permitted"]
    env.call("approvals.act", "request_approval")
    result = grade_workflow(project, spec, env.events)
    assert result["trace_valid"] and result["policy_compliance"] == 3 / 5
    for index in (1, 3):
        mutation = copy.deepcopy(env.events)
        mutation[index]["permitted"] = True
        assert not grade_workflow(project, spec, mutation)["trace_valid"]
    mutation = copy.deepcopy(env.events)
    mutation[0]["after"]["amount"] = 0
    assert not grade_workflow(project, spec, mutation)["trace_valid"]


def test_budget_termination_and_wrong_tools_have_no_side_effect(project):
    spec = task()
    spec.max_steps = 1
    env = WorkflowEnvironment(project, spec)
    env.call("approvals.act", "agent")
    assert env.done and env.state == spec.initial_state
    result = grade_workflow(project, spec, env.events)
    assert result["task_utility"] == 0 and result["policy_compliance"] == 0
    assert not grade_workflow(project, spec, env.events * 2)["trace_valid"]


def test_provider_runner_records_unknown_cost_without_claiming_free_calls(project):
    class Scripted:
        def __init__(self):
            self.choices = iter(["agent", "summary", "execute", "accept"])

        def generate(self, system, user, schema):
            assert "valid_choices" not in user and "success_states" not in user
            return {"choice": next(self.choices)}

    result = solve_workflow(project, task(), Scripted())
    assert result["grading"]["task_utility"] == 1
    assert result["unmetered_calls"] == 4

    class Failing:
        def generate(self, *args):
            raise ValueError("SECRET_ERROR")

    failed = solve_workflow(project, task(), Failing())
    assert failed["solver_error"] == "ValueError" and failed["unmetered_calls"] == 1


def test_task_must_bind_every_choice_to_matching_tool(project):
    spec = task()
    spec.steps[0].tool = "documents.share"
    with pytest.raises(ValueError, match="matching decision"):
        WorkflowEnvironment(project, spec)
    spec = task()
    del spec.steps[0].next_steps["human"]
    with pytest.raises(ValueError, match="every candidate"):
        WorkflowEnvironment(project, spec)


def test_published_flagship_workflow_traces_replay_without_model_calls(project):
    from decision_forge.io import digest

    root = ROOT.parents[1] / "experiments/2026-10-workflows"
    results = sorted(root.glob("*/support-*/result.json"))
    assert len(results) == 6
    total_calls = 0
    cost = 0
    task_sets = {}
    for path in results:
        saved = read_json(path)
        name = saved["task_id"].removeprefix("support-")
        assert grade_workflow(project, task(name), saved["trace"]) == saved["grading"]
        calls = read_json(path.parent / "calls.json")
        assert all(c["status"] == "returned" and c["usage"] for c in calls)
        total_calls += len(calls)
        cost += sum(c["usage"]["cost"] for c in calls)
        configuration = read_json(path.parent / "configuration.json")
        task_sets.setdefault(configuration["model"], set()).add(configuration["task_digest"])
        assert configuration["task_digest"] == digest(task(name).model_dump(mode="json"))
    assert total_calls == 22 and cost == pytest.approx(0.036412)
    assert len(task_sets) == 2 and len({frozenset(tasks) for tasks in task_sets.values()}) == 1
