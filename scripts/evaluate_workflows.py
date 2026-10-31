"""Reproduce explicit-endpoint workflow evaluations; preserve failed attempts and returned usage."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from decision_forge.corpus import CallJournal, CompilationOptions
from decision_forge.io import digest, load_project, load_sources, read_json, write_json
from decision_forge.providers import OpenAICompatibleProvider
from decision_forge.review import require_review
from decision_forge.workflows import WorkflowTask, solve_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, nargs="+", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = load_project(args.spec)
    load_sources(project, args.spec.parent)
    require_review(args.spec)
    for model in args.models:
        for path in args.tasks:
            task = WorkflowTask.model_validate(read_json(path))
            output = args.output / digest(model)[:12] / task.id
            identity = {
                "model": model,
                "task_digest": digest(task.model_dump(mode="json")),
                "project_digest": digest(project.model_dump(mode="json")),
                "reasoning_effort": "high",
            }
            if output.exists():
                raise ValueError("Evaluation output exists; inspect its evidence and choose a new directory")
            output.mkdir(parents=True)
            write_json(output / "configuration.json", identity)
            provider = OpenAICompatibleProvider(
                args.base_url,
                model,
                os.environ.get("FORGE_API_KEY", ""),
                retries=0,
                max_output_tokens=4096,
                temperature=None,
                reasoning_effort="high",
                timeout_seconds=300,
            )
            journal = CallJournal(output / "calls.json", CompilationOptions(max_calls=8, max_total_tokens=100000))
            result = solve_workflow(project, task, provider, journal)
            write_json(output / "result.json", result)
            print(
                {
                    "model": model,
                    "task": task.id,
                    "utility": result["grading"]["task_utility"],
                    "compliance": result["grading"]["policy_compliance"],
                    "cost": result["known_cost_usd"],
                    "error": result["solver_error"],
                },
                flush=True,
            )


if __name__ == "__main__":
    main()
