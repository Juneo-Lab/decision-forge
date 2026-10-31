"""Offline decision replay and portable decision tasks for Harbor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from decision_forge.exporters import model_input
from decision_forge.io import atomic_text, read_json, write_json
from decision_forge.pipeline import load_run


def replay(run: Path, predictions: Path) -> dict[str, Any]:
    episodes = load_run(run)
    known = {e.id for e in episodes}
    choices = read_json(predictions)
    if not isinstance(choices, dict) or any(not isinstance(v, str) for v in choices.values()):
        raise ValueError("Predictions must map episode IDs to candidate ID strings")
    unknown = set(choices) - known
    if unknown:
        raise ValueError(f"Unknown episode IDs: {sorted(unknown)}")
    events: list[dict[str, Any]] = []
    for episode in episodes:
        choice = choices.get(episode.id)
        allowed = {v.candidate_id for v in episode.verdicts if v.allowed}
        events.append(
            {
                "episode_id": episode.id,
                "kind": episode.kind.value,
                "split": episode.split,
                "choice": choice,
                "answered": choice is not None,
                "permitted": choice in allowed,
                "best_choice": choice in episode.valid_choices,
            }
        )
    answered = sum(event["answered"] for event in events)
    correct = sum(event["best_choice"] for event in events)
    permitted = sum(event["permitted"] for event in events)
    return {
        "scope": "offline_single_step_policy_replay",
        "episodes": len(events),
        "answered": answered,
        "correct": correct,
        "permitted": permitted,
        "accuracy": correct / len(events) if events else None,
        "coverage": answered / len(events) if events else None,
        "events": events,
    }


def export_harbor(run: Path, output: Path, limit: int = 20, split: str = "test") -> int:
    if limit < 1:
        raise ValueError("Task limit must be positive")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Task output directory must be empty")
    episodes = [e for e in load_run(run) if e.split == split][:limit]
    if not episodes:
        raise ValueError(f"No episodes in split {split}")
    for episode in episodes:
        task = output / episode.id
        write_json(task / "environment/context.json", model_input(episode))
        atomic_text(
            task / "environment/Dockerfile",
            "FROM python:3.12-slim\nWORKDIR /workspace\nCOPY context.json /workspace/context.json\n",
        )
        atomic_text(
            task / "instruction.md",
            "Read /workspace/context.json and select the best permitted candidate.\n"
            "Write /workspace/answer.json as a JSON object with a single string field named choice.\n",
        )
        atomic_text(
            task / "task.toml",
            'version = "1.0"\n\n[metadata]\n'
            'author_name = "Decision Forge contributors"\n'
            'difficulty = "medium"\ncategory = "organizational-decisions"\n'
            f'tags = ["{episode.kind.value}", "{episode.split}"]\n\n'
            "[verifier]\ntimeout_sec = 60.0\n\n[agent]\ntimeout_sec = 180.0\n\n"
            "[environment]\nbuild_timeout_sec = 600.0\ncpus = 1\nmemory_mb = 1024\nstorage_mb = 2048\n",
        )
        # The Docker build context contains only environment/, so labels are not baked into it.
        verifier = f"""import json
from pathlib import Path

valid_choices = {episode.valid_choices!r}
try:
    answer = json.loads(Path("/workspace/answer.json").read_text())
    correct = isinstance(answer, dict) and answer.get("choice") in valid_choices
except (OSError, ValueError, TypeError):
    correct = False
output = Path("/logs/verifier")
output.mkdir(parents=True, exist_ok=True)
(output / "reward.txt").write_text("1" if correct else "0")
"""
        atomic_text(task / "tests/verify.py", verifier)
        atomic_text(task / "tests/test.sh", "#!/bin/sh\nset -eu\npython /tests/verify.py\n", mode=0o755)
        solution = json.dumps({"choice": episode.valid_choices[0]})
        atomic_text(
            task / "solution/solve.sh",
            "#!/bin/sh\nset -eu\ncat > /workspace/answer.json <<'ANSWER'\n" + solution + "\nANSWER\n",
            mode=0o755,
        )
    write_json(
        output / "manifest.json",
        {
            "format": "harbor-single-step",
            "split": split,
            "tasks": len(episodes),
            "scope": "Decision tasks; full business workflow environments are planned.",
        },
    )
    return len(episodes)
