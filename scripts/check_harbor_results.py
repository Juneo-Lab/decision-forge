"""Require complete, error-free Harbor trials with the expected control reward."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def check_results(manifest: Path, jobs: Path, agent: str, reward: int) -> dict[str, Any]:
    expected = set(json.loads(manifest.read_text(encoding="utf-8"))["episode_ids"])
    if not expected:
        raise ValueError("Task manifest is empty")
    trials = []
    for path in jobs.rglob("result.json"):
        result = json.loads(path.read_text(encoding="utf-8"))
        if "task_name" in result:
            trials.append(result)
    actual = [result["task_name"] for result in trials]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("Missing, duplicate, or unexpected task results")
    for result in trials:
        if result.get("exception_info") is not None or result.get("finished_at") is None:
            raise ValueError(f"Incomplete or failed trial: {result['task_name']}")
        if result.get("agent_info", {}).get("name") != agent:
            raise ValueError("Unexpected agent in result")
        verifier = result.get("verifier_result") or {}
        if verifier.get("rewards") != {"reward": reward}:
            raise ValueError(f"Unexpected verifier reward: {result['task_name']}")
    return {"agent": agent, "trials": len(trials), "expected_reward": reward, "errors": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--agent", choices=["oracle", "nop"], required=True)
    parser.add_argument("--reward", type=int, choices=[0, 1], required=True)
    args = parser.parse_args()
    print(json.dumps(check_results(args.manifest, args.jobs, args.agent, args.reward), indent=2))
