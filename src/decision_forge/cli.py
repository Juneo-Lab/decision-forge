"""Command-line entry points with explicit inputs and structured results."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, NoReturn, get_args

import typer
from filelock import Timeout
from pydantic import ValidationError

from decision_forge import __version__
from decision_forge.compiler import compile_documents
from decision_forge.corpus import CompilationOptions, compile_corpus, extraction_metrics
from decision_forge.dedup import NEAR_DUPLICATE_THRESHOLD
from decision_forge.evaluation import DecisionReference, NodeBinding, evaluate_decisions
from decision_forge.exporters import ExportFormat, export_run
from decision_forge.harness import export_harbor, replay
from decision_forge.io import digest, load_project, load_sources, read_json, write_json
from decision_forge.models import Episode, Project, Split
from decision_forge.pipeline import build, impact, load_run
from decision_forge.providers import OpenAICompatibleProvider, ResponseMode, env_api_key
from decision_forge.review import ReviewDecision, record_review, require_review, review_status
from decision_forge.sources import ingest_manifest
from decision_forge.traces import audit_trace, load_mapping, read_trace

app = typer.Typer(no_args_is_help=True, help="Compile organizational workflows into verifiable decision data.")


def display(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False))


def fail(exc: Exception) -> NoReturn:
    # Validation errors may contain private source excerpts; display only their class here.
    if isinstance(exc, ValidationError):
        typer.echo("Error: specification validation failed; check field types and references.", err=True)
    else:
        typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=1)


def choice(value: str, allowed: Any, message: str) -> Any:
    """Return ``value`` if it is one of the ``Literal`` type's options, otherwise fail with ``message``."""
    if value not in get_args(allowed):
        raise ValueError(message)
    return value


def _episode(run: Path, episode_id: str) -> Episode:
    episode = next((e for e in load_run(run) if e.id == episode_id), None)
    if episode is None:
        raise ValueError("Unknown episode ID")
    return episode


@contextmanager
def cli_errors(*expected: type[Exception]) -> Iterator[None]:
    """Report expected failures as a one-line error and exit code 1 instead of a traceback."""
    handled: tuple[type[Exception], ...] = (ValueError, OSError, *expected)
    try:
        yield
    except handled as exc:
        fail(exc)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def schema(output: Path | None = None) -> None:
    """Export the versioned project JSON Schema."""
    if output:
        write_json(output, Project.model_json_schema())
    else:
        display(Project.model_json_schema())


@app.command()
def validate(spec: Path) -> None:
    """Validate project references and exact evidence quotations."""
    with cli_errors():
        project = load_project(spec)
        _, hashes = load_sources(project, spec.parent)
        display(
            {
                "valid": True,
                "organization": project.organization.id,
                "source_hashes": hashes,
                "nodes": len(project.nodes),
                "scenarios": len(project.scenarios),
            }
        )


@app.command("build")
def build_command(
    spec: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    cache: Path | None = None,
    recipe_plan: Annotated[Path | None, typer.Option(help="Plan produced by plan-recipe.")] = None,
    expansion: Annotated[Path | None, typer.Option(help="Verified synthesis directory.")] = None,
    near_duplicate_threshold: Annotated[
        float, typer.Option(help="Presentation similarity above which a duplicate is dropped.")
    ] = NEAR_DUPLICATE_THRESHOLD,
    locked_splits: Annotated[
        bool, typer.Option(help="Fail instead of creating or updating the split lock next to SPEC.")
    ] = False,
) -> None:
    """Generate, verify, deduplicate, checkpoint, and report decision episodes. Runs offline.

    The first build records every family's split in SPEC's split lock; later builds keep it.
    """
    with cli_errors(Timeout):
        display(
            build(
                spec,
                output,
                cache,
                recipe_plan=recipe_plan,
                expansion=expansion,
                near_duplicate_threshold=near_duplicate_threshold,
                locked_splits=locked_splits,
            )["report"]
        )


@app.command("export")
def export_command(run: Path, output: Annotated[Path, typer.Option("--output", "-o")], format: str = "typed") -> None:
    """Export split-aware decision-probability, typed, SFT, or canonical training data."""
    with cli_errors():
        display(
            export_run(run, output, choice(format, ExportFormat, "Format must be typed, sft, canonical, or decision"))
        )


@app.command("impact")
def impact_command(spec: Path, previous: Path) -> None:
    """Identify changed and reusable scenario families before rebuilding."""
    with cli_errors():
        display(impact(spec, previous))


@app.command("audit-trace")
def audit_trace_command(
    spec: Path,
    trace: Path,
    mapping: Annotated[Path, typer.Option(help="YAML mapping from tool names to decision nodes and state.")],
    output: Path | None = None,
) -> None:
    """Replay recorded agent tool calls against the declared policy. Runs offline.

    TRACE is JSON Lines tool calls or an OTLP JSON export of OpenTelemetry GenAI execute_tool spans.
    """
    with cli_errors(KeyError, TypeError):
        project = load_project(spec)
        require_review(spec)
        load_sources(project, spec.parent)
        report = audit_trace(project, read_trace(trace), load_mapping(mapping, project))
        if output:
            write_json(output, report)
        display({key: report[key] for key in ("organization_id", "calls", "summary", "coverage_gaps")})


@app.command("compile")
def compile_command(
    sources: list[Path],
    output: Annotated[Path, typer.Option("--output", "-o")],
    base_url: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
) -> None:
    """Send the listed documents to the configured model and write an evidence-linked draft spec.

    Set FORGE_API_KEY for authenticated endpoints. The endpoint must support JSON responses;
    fixed records use native strict schemas.
    """
    with cli_errors():
        provider = OpenAICompatibleProvider(base_url, model, env_api_key())
        project = compile_documents(sources, output, provider)
        display(
            {
                "status": "draft",
                "spec": str(output / "project.json"),
                "nodes": len(project.nodes),
                "evidence": "exact quotes checked; review policy interpretation before training",
            }
        )


@app.command("replay")
def replay_command(run: Path, predictions: Path, output: Path | None = None) -> None:
    """Score recorded candidate choices against a verified decision dataset."""
    with cli_errors():
        result = replay(run, predictions)
        if output:
            write_json(output, result)
        display({key: value for key, value in result.items() if key != "events"})


@app.command("harbor-export")
def harbor_export_command(
    run: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    limit: int = 20,
    split: str = "test",
) -> None:
    """Create single-step decision task directories for Harbor."""
    with cli_errors():
        choice(split, Split, "Choose train, development, or test")
        display({"tasks": export_harbor(run, output, limit, split), "output": str(output)})


@app.command("ingest")
def ingest_command(
    manifest: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    allow_external_ocr: Annotated[
        bool, typer.Option(help="Allow image transmission to the OCR endpoint in the manifest.")
    ] = False,
) -> None:
    """Normalize source documents with versions, access metadata, and source spans."""
    with cli_errors():
        if output.exists():
            raise ValueError("Ingestion output already exists")
        documents = ingest_manifest(manifest, allow_external_ocr=allow_external_ocr)
        write_json(output, [d.model_dump(mode="json") for d in documents])
        display({"sources": len(documents), "output": str(output)})


@app.command("compile-corpus")
def compile_corpus_command(
    manifest: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    base_url: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
    max_calls: int = 100,
    validation_retries: int = 2,
    response_mode: str = "auto",
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    max_output_tokens: int = 32000,
    timeout_seconds: float = 300,
    max_input_bytes: int = 4_000_000,
    max_total_tokens: int | None = None,
    chunk_characters: int = 8000,
    overlap_characters: int = 400,
    allow_external_ocr: Annotated[
        bool, typer.Option(help="Allow image transmission to the OCR endpoint in the manifest.")
    ] = False,
) -> None:
    """Send an explicit source corpus to a model, checkpoint extraction, and assemble a review draft."""
    with cli_errors(Timeout):
        # One journal reservation equals one HTTP attempt; retries require an explicit resume.
        mode = choice(response_mode, ResponseMode, "Choose auto, json_schema, or json_object response mode")
        provider = OpenAICompatibleProvider(
            base_url,
            model,
            env_api_key(),
            retries=0,
            response_mode=mode,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        options = CompilationOptions(
            validation_retries=validation_retries,
            max_calls=max_calls,
            max_input_bytes=max_input_bytes,
            max_total_tokens=max_total_tokens,
            chunk_characters=chunk_characters,
            overlap_characters=overlap_characters,
        )
        # The identity records temperature 0 whatever --temperature says. It keys existing caches and
        # compilation fingerprints, so correcting it would orphan them; change it only with a version bump.
        identity = digest({"endpoint": provider.base_url, "model": model, "temperature": 0})
        display(compile_corpus(manifest, output, provider, identity, options, allow_external_ocr=allow_external_ocr))


@app.command("review-status")
def review_status_command(spec: Path, output: Path | None = None) -> None:
    """Inspect current rule/node reviews and observed rule conflicts; export details locally."""
    with cli_errors():
        status = review_status(spec)
        if output:
            write_json(output, status)
        display(
            {
                **{k: status[k] for k in ("fingerprint", "ready", "pending", "rejected")},
                "items": [
                    {"id": i["id"], "kind": i["kind"], "status": i["status"], "fingerprint": i["fingerprint"]}
                    for i in status["items"]
                ],
            }
        )


@app.command("review-record")
def review_record_command(
    spec: Path,
    item: Annotated[str, typer.Option()],
    decision: Annotated[str, typer.Option()],
    reviewer: Annotated[str, typer.Option()],
    reason: Annotated[str, typer.Option()],
    fingerprint: Annotated[str, typer.Option()],
) -> None:
    """Record a named review against the exact fingerprint shown by review-status."""
    with cli_errors(Timeout):
        verdict = choice(decision, ReviewDecision, "Decision must be accepted or rejected")
        event = record_review(spec, item, verdict, reviewer, reason, fingerprint)
        display({"recorded": event["item_id"], "decision": event["decision"], "hash": event["hash"]})


@app.command("evaluate-extraction")
def evaluate_extraction_command(predicted: Path, reference: Path, output: Path | None = None) -> None:
    """Compute exact source-span/kind metrics against a separately authored reference."""
    with cli_errors(KeyError, TypeError):
        result = extraction_metrics(read_json(predicted), read_json(reference))
        if output:
            write_json(output, result)
        display(result)


@app.command("evaluate-decisions")
def evaluate_decisions_command(spec: Path, reference: Path, bindings: Path, output: Path | None = None) -> None:
    """Check compiled rule semantics against fixed model-neutral cases and explicit field bindings."""
    with cli_errors():
        project = load_project(spec)
        load_sources(project, spec.parent)
        raw_bindings = read_json(bindings)
        if not isinstance(raw_bindings, dict):
            raise ValueError("Decision bindings must be a JSON object keyed by node ID")
        result = evaluate_decisions(
            project,
            DecisionReference.model_validate(read_json(reference)),
            {key: NodeBinding.model_validate(value) for key, value in raw_bindings.items()},
        )
        if output:
            write_json(output, result)
        display({key: value for key, value in result.items() if key != "events"})


@app.command("workflow-run")
def workflow_run_command(
    spec: Path,
    task: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    base_url: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
    reasoning_effort: str = "high",
    max_calls: int = 20,
    max_total_tokens: int = 500000,
) -> None:
    """Run a declared business task against an explicit model endpoint and independently grade its trace."""
    from decision_forge.corpus import CallJournal
    from decision_forge.review import require_review
    from decision_forge.workflows import WorkflowTask, solve_workflow

    with cli_errors():
        if output.exists():
            raise ValueError("Workflow output already exists; choose a new directory")
        project = load_project(spec)
        load_sources(project, spec.parent)
        require_review(spec)
        task_spec = WorkflowTask.model_validate(read_json(task))
        output.mkdir(parents=True)
        provider = OpenAICompatibleProvider(
            base_url,
            model,
            env_api_key(),
            retries=0,
            temperature=None,
            reasoning_effort=reasoning_effort,
            max_output_tokens=4096,
            timeout_seconds=300,
        )
        journal = CallJournal(
            output / "calls.json", CompilationOptions(max_calls=max_calls, max_total_tokens=max_total_tokens)
        )
        result = solve_workflow(project, task_spec, provider, journal)
        write_json(output / "result.json", result)
        write_json(
            output / "configuration.json",
            {"provider": provider.configuration, "task": task_spec.model_dump(mode="json")},
        )
        display({k: v for k, v in result.items() if k not in {"trace", "calls"}})


@app.command("workflow-grade")
def workflow_grade_command(spec: Path, task: Path, trace: Path, output: Path | None = None) -> None:
    """Recompute workflow utility and policy compliance from a recorded trace."""
    from decision_forge.workflows import WorkflowTask, grade_workflow

    with cli_errors(KeyError, TypeError):
        project = load_project(spec)
        load_sources(project, spec.parent)
        result = grade_workflow(project, WorkflowTask.model_validate(read_json(task)), read_json(trace))
        if output:
            write_json(output, result)
        display(result)


@app.command("events-ingest")
def events_ingest_command(events: Path, database: Annotated[Path, typer.Option()]) -> None:
    """Append versioned runtime events atomically; repeated identical IDs are idempotent."""
    from decision_forge.feedback import RuntimeEvent, ingest_events

    with cli_errors():
        display(ingest_events(database, [RuntimeEvent.model_validate(row) for row in read_json(events)]))


@app.command("feedback-plan")
def feedback_plan_command(run: Path, database: Path, output: Annotated[Path, typer.Option()]) -> None:
    """Create generation requests only from explicitly reviewed development errors."""
    from decision_forge.feedback import feedback_requests

    with cli_errors():
        result = feedback_requests(run, database)
        write_json(output, result)
        display({"requests": len(result["requests"]), "excluded": result["excluded"]})


@app.command("update")
def update_command(
    spec: Path, previous: Path, output: Annotated[Path, typer.Option()], cache: Path | None = None
) -> None:
    """Rebuild affected scenarios while freezing published train/development/test membership."""
    from decision_forge.feedback import update_dataset

    with cli_errors(Timeout):
        display(update_dataset(spec, previous, output, cache))


@app.command("lineage")
def lineage_command(run: Path, output: Annotated[Path, typer.Option()]) -> None:
    """Export source-span, rule, scenario, and episode lineage for a completed dataset."""
    from decision_forge.feedback import lineage

    with cli_errors():
        result = lineage(run)
        write_json(output, result)
        display({"dataset_digest": result["dataset_digest"], "episodes": len(result["episodes"])})


@app.command("orchestrate")
def orchestrate_command(
    spec: Path,
    output: Annotated[Path, typer.Option()],
    database: Annotated[Path, typer.Option()],
    thread_id: Annotated[str, typer.Option()],
) -> None:
    """Run or resume a durable LangGraph review/build/export job (requires orchestration extra)."""
    from decision_forge.adapters import orchestrate

    with cli_errors(Timeout):
        display(orchestrate(spec, output, database, thread_id))


@app.command("designer-process")
def designer_process_command(run: Path, output: Annotated[Path, typer.Option()]) -> None:
    """Process ordered dataset seeds through the pinned Data Designer expression engine."""
    from decision_forge.adapters import designer_process

    with cli_errors():
        display(designer_process(run, output))


@app.command("transform")
def transform_command(run: Path, episode_id: str, intervention: Path, output: Annotated[Path, typer.Option()]) -> None:
    """Check a counterfactual or candidate-order rewrite and save its verified parent/child pair."""
    from decision_forge.pipeline import load_run_project
    from decision_forge.transformations import Intervention, transform_episode

    with cli_errors():
        parent = _episode(run, episode_id)
        request = Intervention.model_validate(read_json(intervention))
        child = transform_episode(load_run_project(run), parent, request)
        write_json(
            output,
            {
                "parent": parent.model_dump(mode="json"),
                "child": child.model_dump(mode="json"),
                "intervention": request.model_dump(mode="json"),
            },
        )
        display({"parent": parent.id, "child": child.id, "relation": request.relation, "verified": True})


@app.command("plan-recipe")
def plan_recipe_command(
    spec: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    mode: str = "uniform_cells",
    per_cell: int = 50,
    total: int | None = None,
    stratum: Annotated[list[str] | None, typer.Option(help="Restrict planning to a stratum.")] = None,
    targets: Annotated[Path | None, typer.Option(help="JSON list of explicit distribution targets.")] = None,
    base_url: Annotated[str | None, typer.Option(help="Optional advisor model endpoint.")] = None,
    model: Annotated[str | None, typer.Option(help="Optional advisor model name.")] = None,
    states_per_call: int = 8,
) -> None:
    """Plan exact per-cell quotas from the measured deterministic pool, offline by default."""
    from decision_forge.models import DistributionTarget
    from decision_forge.planner import PlanObjective, PlanOptions, plan_recipe

    with cli_errors(Timeout):
        if mode not in {"uniform_cells", "explicit"}:
            raise ValueError("Choose uniform_cells or explicit")
        objective = PlanObjective.model_validate(
            {
                "mode": mode,
                "per_cell": per_cell,
                "total_examples": total,
                "strata": [value for value in (stratum or []) if value],
                "targets": [DistributionTarget.model_validate(row) for row in read_json(targets)] if targets else [],
            }
        )
        provider = None
        if base_url or model:
            if not (base_url and model):
                raise ValueError("An advisor model requires both --base-url and --model")
            provider = OpenAICompatibleProvider(base_url, model, env_api_key())
        plan = plan_recipe(
            spec,
            output,
            objective,
            options=PlanOptions(states_per_call=states_per_call),
            provider=provider,
        )
        display(
            {
                "status": plan["status"],
                "cells": len(plan["distribution"]),
                "requested": plan["feasibility"]["requested"],
                "available": plan["feasibility"]["available"],
                "shortfall": plan["feasibility"]["shortfall"],
                "feasible": plan["feasibility"]["feasible"],
                "expansion_requests": len(plan["expansion_requests"]),
                "held_out_shortfall": plan["held_out_shortfall"]["shortfall"],
                "model_calls_estimate": plan["cost_estimate"]["model_calls"],
                "output": str(output),
            }
        )


@app.command("expand")
def expand_command(
    run: Path,
    requests: Path,
    output: Annotated[Path, typer.Option("--output", "-o")],
    base_url: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
    states_per_call: int = 8,
    max_calls: int = 20,
    max_input_bytes: int = 4_000_000,
    max_output_tokens: int = 4096,
    near_duplicate_threshold: float = NEAR_DUPLICATE_THRESHOLD,
    reasoning_effort: str | None = None,
) -> None:
    """Synthesize extra examples with an explicit model call and verify every label by replay."""
    from decision_forge.expansion import ExpansionOptions, ProposalBatch, expand

    with cli_errors(Timeout):
        provider = OpenAICompatibleProvider(
            base_url,
            model,
            env_api_key(),
            retries=0,
            temperature=None,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
        )
        try:
            batch = ProposalBatch.model_validate({"requests": read_json(requests)})
        except ValueError as invalid:
            # Request files hold no source content, so the field location is safe to report.
            detail: list[dict[str, Any]] = getattr(invalid, "errors", lambda: [])()
            location = detail[0]["loc"] if detail else "unknown field"
            raise ValueError(f"Invalid expansion request file; first problem at {location}") from None
        summary = expand(
            run,
            batch.requests,
            output,
            provider,
            ExpansionOptions(
                states_per_call=states_per_call,
                max_calls=max_calls,
                max_input_bytes=max_input_bytes,
                near_duplicate_threshold=near_duplicate_threshold,
            ),
            # Unlike compile-corpus, this hashes the URL as typed (a trailing slash changes it). Kept
            # as is because it keys resumable expansion artifacts.
            provider_id=digest({"endpoint": base_url, "model": model}),
        )
        display(summary)


@app.command("dedup")
def dedup_command(run: Path, output: Path | None = None, threshold: float = NEAR_DUPLICATE_THRESHOLD) -> None:
    """Report duplicate inputs and held-out leaks in a completed run."""
    from decision_forge.dedup import find_duplicates

    with cli_errors():
        report = find_duplicates(load_run(run), threshold=threshold)
        if output:
            write_json(output, report)
        display(
            {key: report[key] for key in ("protocol", "threshold", "examples", "kept", "removed", "cross_split_pairs")}
        )


@app.command("rewrite")
def rewrite_command(
    run: Path,
    episode_id: str,
    output: Annotated[Path, typer.Option("--output", "-o")],
    base_url: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
    verifier_model: Annotated[str | None, typer.Option(help="Independent verifier model name.")] = None,
) -> None:
    """Paraphrase one presentation and publish it only after independent verification."""
    from decision_forge.pipeline import load_run_project
    from decision_forge.transformations import rewrite_presentation

    with cli_errors():
        provider = OpenAICompatibleProvider(base_url, model, env_api_key(), temperature=None)
        verifier = (
            OpenAICompatibleProvider(base_url, verifier_model, env_api_key(), temperature=None)
            if verifier_model
            else None
        )
        parent = _episode(run, episode_id)
        child = rewrite_presentation(load_run_project(run), parent, provider, verifier)
        write_json(output, {"parent": parent.model_dump(mode="json"), "child": child.model_dump(mode="json")})
        display({"parent": parent.id, "child": child.id, "verified": True})


if __name__ == "__main__":
    app()
