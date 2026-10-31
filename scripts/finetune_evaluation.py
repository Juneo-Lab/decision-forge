"""Decision-only training handoff and separate generated-choice endpoint evaluation.

This script does not train a model by itself. Training needs a GPU runtime and a spending decision,
so `prepare` writes verified candidate-probability rows and an explicit trainer contract. `evaluate` and
`compare` run without a trainer: they score any OpenAI-compatible endpoint on the frozen held-out
split, so the baseline can be measured before spending anything.

The point is the loop, not the trainer: one dataset digest in, one comparable accuracy-per-surface
report out, with the held-out families identical for every arm.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Annotated, Any

import typer

from decision_forge.exporters import export_run
from decision_forge.inputs import model_input
from decision_forge.io import atomic_text, canonical, read_json, write_json
from decision_forge.pipeline import load_run
from decision_forge.providers import OpenAICompatibleProvider

app = typer.Typer(no_args_is_help=True, help="Fixed-budget fine-tuning and held-out evaluation loop.")

TRAINER = {
    "interface": "candidate_readout_v1",
    "method": "lora",
    "base_model": None,
    "epochs": 2,
    "max_seq_length": 8192,
    "seed": 1015,
    "thinking": False,
    "row_weight": 1.0,
    "loss": "candidate_restricted_cross_entropy",
    "target": "preserve_target_probs",
    "logit_position": "last_prompt_token",
    "world_objective": False,
}

CHOICE_PROMPT = """You select the best permitted action for an organizational decision.
Answer with one JSON object containing exactly one field, "choice", whose value is one candidate ID
from the supplied list. Output JSON only, with no explanation.
"""


def dataset_digest(run: Path) -> str:
    value = read_json(run / "manifest.json").get("dataset_digest")
    if not isinstance(value, str):
        raise ValueError("Run manifest has no dataset digest; rebuild the run")
    return value


def prepare(run: Path, output: Path, budget_examples: int, trainer: dict[str, Any] | None = None) -> dict[str, Any]:
    """Materialise a pinned training plan for a frozen dataset digest."""
    if budget_examples < 1:
        raise ValueError("Training example budget must be positive")
    overrides = trainer or {}
    if set(overrides) - {"base_model", "epochs", "max_seq_length", "seed"}:
        raise ValueError("Trainer overrides may not change the decision objective")
    for field in ("epochs", "max_seq_length"):
        value = overrides.get(field, TRAINER[field])
        if type(value) is not int or value < 1:
            raise ValueError(f"Trainer {field} must be a positive integer")
    episodes = load_run(run)
    if not any(e.split == "train" and len(e.valid_choices) == 1 for e in episodes):
        raise ValueError("The run contains no training examples with a unique verified answer")
    if not any(e.split == "test" and len(e.valid_choices) == 1 for e in episodes):
        raise ValueError("The run contains no held-out examples with a unique verified answer")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Training plan directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    export_dir = output / "training"
    counts = export_run(run, export_dir, "decision", train_limit=budget_examples)
    exported = read_json(export_dir / "export.json")
    config: dict[str, Any] = {
        "schema_version": "2.0",
        "dataset_digest": dataset_digest(run),
        "budget_examples": budget_examples,
        "examples": {split: counts[split] for split in ["train", "development", "test"]},
        "skipped_multiple_valid_choices": counts["skipped_multiple_valid_choices"],
        "trainer": {**TRAINER, **overrides},
        "export_format": "decision",
        "file_hashes": exported["file_hashes"],
        "selected_ids": exported["selected_ids"],
        "selection": "deterministic episode-ID prefix of exportable training rows",
        "status": "prepared",
        "training_launched": False,
        "required_trainer": "Native non-thinking candidate-label logits with explicit target probabilities",
        "reporting": {
            "metrics": ["candidate_nll", "accuracy", "per_kind_accuracy"],
            "held_out": "Original family split assignments; never relabel or move held-out rows",
        },
    }
    write_json(output / "plan.json", config)
    atomic_text(
        output / "README.md",
        "# Decision training handoff\n\n"
        "Load training/train.jsonl with the candidate-readout trainer. Preserve target_probs and unit weights.\n"
        "Use decision_forge.training.encode_decision with the selected base tokenizer. It rejects truncation.\n"
        "Compute -sum(target_probs * log_softmax(candidate_logits)) at the last prompt position.\n"
        "Keep source/group metadata outside model inputs. Training is not launched by this command.\n"
        "The endpoint evaluate/compare commands measure generated choices separately from candidate NLL.\n",
    )
    return config


def evaluate(
    run: Path,
    output: Path,
    provider: OpenAICompatibleProvider,
    arm: str,
    limit: int | None = None,
) -> dict[str, Any]:
    """Score one endpoint on the frozen held-out split with a fixed prompt."""
    if limit is not None and limit < 1:
        raise ValueError("Evaluation limit must be positive")
    episodes = [e for e in load_run(run) if e.split == "test"]
    if limit is not None:
        episodes = episodes[:limit]
    if not episodes:
        raise ValueError("No held-out episodes to evaluate")
    rows = []
    for episode in episodes:
        request = canonical({"input": model_input(episode), "candidates": [c.id for c in episode.candidates]})
        started = time.monotonic()
        choice: str | None = None
        error: str | None = None
        try:
            answer = provider.generate(
                CHOICE_PROMPT,
                request,
                {
                    "type": "object",
                    "properties": {"choice": {"type": "string"}},
                    "required": ["choice"],
                    "additionalProperties": False,
                },
            )
            candidate = answer.get("choice")
            choice = candidate if isinstance(candidate, str) else None
        except ValueError as exc:
            error = str(exc)[:200]
        rows.append(
            {
                "episode_id": episode.id,
                "kind": episode.kind.value,
                "choice": choice,
                "correct": choice in episode.valid_choices,
                "permitted": choice in {v.candidate_id for v in episode.verdicts if v.allowed},
                "latency_seconds": time.monotonic() - started,
                "error": error,
                "usage": getattr(provider, "last_usage", None),
            }
        )
    answered = [row for row in rows if row["choice"] is not None]
    by_kind: dict[str, dict[str, float | int]] = {}
    for row in rows:
        bucket = by_kind.setdefault(row["kind"], {"cases": 0, "correct": 0})
        bucket["cases"] = int(bucket["cases"]) + 1
        bucket["correct"] = int(bucket["correct"]) + int(row["correct"])
    for bucket in by_kind.values():
        bucket["accuracy"] = bucket["correct"] / bucket["cases"]
    cost = sum(
        float(row["usage"].get("cost", 0))
        for row in rows
        if isinstance(row["usage"], dict) and isinstance(row["usage"].get("cost"), (int, float))
    )
    report = {
        "schema_version": "1.0",
        "arm": arm,
        "endpoint": provider.base_url,
        "model": provider.model,
        "dataset_digest": dataset_digest(run),
        "cases": len(rows),
        "answered": len(answered),
        "coverage": len(answered) / len(rows),
        "accuracy": sum(row["correct"] for row in rows) / len(rows),
        "permitted_rate": sum(row["permitted"] for row in rows) / len(rows),
        "errors": sum(1 for row in rows if row["error"]),
        "by_kind": by_kind,
        "latency_seconds": sum(row["latency_seconds"] for row in rows),
        "cost_usd": cost or None,
        "prompt": CHOICE_PROMPT,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, {**report, "events": rows})
    return report


def compare(baseline: Path, candidate: Path, output: Path) -> dict[str, Any]:
    """Compare two arm reports that scored the same frozen dataset."""
    left, right = read_json(baseline), read_json(candidate)
    if left["dataset_digest"] != right["dataset_digest"]:
        raise ValueError("Arms were evaluated on different datasets; the comparison is not valid")
    left_ids = [event["episode_id"] for event in left.get("events", [])]
    right_ids = [event["episode_id"] for event in right.get("events", [])]
    if not left_ids or left_ids != right_ids or len(left_ids) != len(set(left_ids)):
        raise ValueError("Arms must contain the same ordered, unique evaluation case IDs")
    if left.get("cases") != len(left_ids) or right.get("cases") != len(right_ids):
        raise ValueError("Evaluation counts must match the recorded case IDs")
    if not left.get("prompt") or left["prompt"] != right.get("prompt"):
        raise ValueError("Arms must use the same evaluation prompt")
    kinds = sorted(set(left["by_kind"]) | set(right["by_kind"]))
    rows = []
    for kind in kinds:
        base = left["by_kind"].get(kind, {"cases": 0, "correct": 0, "accuracy": 0.0})
        tuned = right["by_kind"].get(kind, {"cases": 0, "correct": 0, "accuracy": 0.0})
        rows.append(
            {
                "kind": kind,
                "cases": tuned["cases"],
                "baseline_accuracy": base["accuracy"],
                "candidate_accuracy": tuned["accuracy"],
                "delta": tuned["accuracy"] - base["accuracy"],
            }
        )
    report = {
        "schema_version": "1.0",
        "dataset_digest": left["dataset_digest"],
        "baseline": {"arm": left["arm"], "model": left["model"], "accuracy": left["accuracy"]},
        "candidate": {"arm": right["arm"], "model": right["model"], "accuracy": right["accuracy"]},
        "overall_delta": right["accuracy"] - left["accuracy"],
        "coverage_delta": right["coverage"] - left["coverage"],
        "permitted_rate_delta": right["permitted_rate"] - left["permitted_rate"],
        "by_kind": rows,
        "scope": "one frozen held-out split; identical prompt and case set for both arms",
    }
    write_json(output, report)
    return report


@app.command("prepare")
def prepare_command(
    run: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    budget_examples: int = 2000,
    base_model: str | None = None,
    epochs: int = 2,
) -> None:
    """Export a frozen dataset and write a pinned training plan. Does not train."""
    override: dict[str, Any] = {}
    if base_model:
        override["base_model"] = base_model
    override["epochs"] = epochs
    try:
        config = prepare(run, output, budget_examples, override or None)
        typer.echo(json.dumps({key: config[key] for key in ("dataset_digest", "examples", "status")}, indent=2))
        typer.echo(
            f"Decision training handoff written to {output / 'plan.json'}; use a compatible candidate-readout trainer."
        )
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command("evaluate")
def evaluate_command(
    run: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    base_url: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
    arm: str = "base_model",
    limit: int | None = None,
    temperature: float = 0.0,
    max_output_tokens: int = 256,
) -> None:
    """Score one endpoint on the frozen held-out split, without any training."""
    try:
        provider = OpenAICompatibleProvider(
            base_url,
            model,
            os.environ.get("FORGE_API_KEY", ""),
            retries=1,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        report = evaluate(run, output, provider, arm, limit)
        typer.echo(
            json.dumps({key: report[key] for key in ("arm", "cases", "accuracy", "coverage", "cost_usd")}, indent=2)
        )
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command("compare")
def compare_command(baseline: Path, candidate: Path, output: Annotated[Path, typer.Option("--output", "-o")]) -> None:
    """Report the measured difference between two arms on the same frozen split."""
    try:
        report = compare(baseline, candidate, output)
        typer.echo(json.dumps({key: report[key] for key in ("overall_delta", "coverage_delta", "by_kind")}, indent=2))
        typer.echo("A positive delta is only evidence of improvement when both arms ran on this dataset digest.")
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()
