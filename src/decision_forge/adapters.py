"""Lazy optional integrations with pinned, executable backend contracts."""

from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, TypedDict

from decision_forge.exporters import export_run, model_input
from decision_forge.io import canonical, digest, load_project, load_sources, read_json, write_json
from decision_forge.pipeline import build, load_run
from decision_forge.review import require_review, review_status

SUPPORTED = {"data-designer": "0.9.7", "langgraph": "1.2.19", "langgraph-checkpoint-sqlite": "3.1.3"}


def require_backend(name: str, extra: str) -> None:
    try:
        installed = version(name)
    except PackageNotFoundError:
        raise ValueError(f"Install decision-forge[{extra}] to use {name}") from None
    if installed != SUPPORTED[name]:
        raise ValueError(f"Unsupported {name} version {installed}; supported version is {SUPPORTED[name]}")


def designer_process(run: Path, output: Path) -> dict[str, Any]:
    """Process ordered Forge seeds using the actual Data Designer engine.

    This adapter uses deterministic expression columns; LLM synthesis is not enabled implicitly.
    Forge retains label verification, distribution quotas and split ownership.
    """
    require_backend("data-designer", "designer")
    dd = importlib.import_module("data_designer.config")
    interface = importlib.import_module("data_designer.interface")
    episodes = load_run(run)
    if not episodes:
        raise ValueError("Data Designer requires at least one accepted episode")
    manifest = read_json(run / "manifest.json")
    identity = digest(
        {
            "dataset": manifest["dataset_digest"],
            "adapter": "ordered-seed-expressions-v1",
            "backend": SUPPORTED["data-designer"],
        }
    )
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    record = output / "adapter.json"
    if not record.exists() and any(output.iterdir()):
        raise ValueError("A new Data Designer job requires an empty output directory")
    if record.exists():
        previous = read_json(record)
        if previous["identity"] != identity:
            raise ValueError("Data Designer adapter inputs changed; choose a new output directory")
        if previous["status"] == "complete":
            if digest(read_json(output / "records.json")) != previous["records_digest"]:
                raise ValueError("Data Designer output integrity check failed")
            return dict(previous)
    else:
        write_json(record, {"status": "running", "identity": identity})
    # A stable row ordering guarantees matching seed IDs across resumed engine batches.
    seeds = [
        {
            "episode_id": e.id,
            "family_id": e.family_id,
            "split": e.split,
            "state_json": canonical(e.state),
            "input_json": canonical(model_input(e)),
            "input_text": "Decision input:\n" + canonical(model_input(e)),
            "valid_choices_json": canonical(e.valid_choices),
        }
        for e in episodes
    ]
    seed_path = output.resolve() / "seed.jsonl"
    from decision_forge.io import atomic_text

    atomic_text(seed_path, "".join(canonical(row) + "\n" for row in seeds))
    builder = dd.DataDesignerConfigBuilder()
    builder.with_seed_dataset(
        dd.LocalFileSeedSource(path=str(seed_path)), sampling_strategy=dd.SamplingStrategy.ORDERED
    )
    builder.add_column(dd.ExpressionColumnConfig(name="training_input", expr="{{ input_text }}"))
    designer = interface.DataDesigner(
        artifact_path=output / "engine",
        model_providers=[dd.ModelProvider(name="offline-disabled", endpoint="http://127.0.0.1:1/v1")],
        auto_configure_logging=False,
    )
    engine_path = output / "engine/forge"
    mode = dd.ResumeMode.ALWAYS if engine_path.exists() and any(engine_path.iterdir()) else dd.ResumeMode.NEVER
    result = designer.create(builder, num_records=len(seeds), dataset_name="forge", resume=mode)
    rows = result.load_dataset().to_dict(orient="records")
    expected = {e.id: e for e in episodes}
    if len(rows) != len(expected) or len({r["episode_id"] for r in rows}) != len(expected):
        raise ValueError("Data Designer changed the seed population")
    for row in rows:
        episode = expected[row["episode_id"]]
        if (
            row["training_input"] != "Decision input:\n" + canonical(model_input(episode))
            or row["family_id"] != episode.family_id
            or row["split"] != episode.split
            or row["valid_choices_json"] != canonical(episode.valid_choices)
        ):
            raise ValueError("Data Designer violated source, split or label invariants")
    write_json(output / "records.json", rows)
    report = {
        "status": "complete",
        "identity": identity,
        "backend": "data-designer",
        "version": SUPPORTED["data-designer"],
        "scope": "ordered_seed_expression_processing",
        "records": len(rows),
        "records_digest": digest(rows),
        "provider_calls": 0,
        "cost_per_record_usd": 0,
        "dataset_digest": manifest["dataset_digest"],
    }
    write_json(record, report)
    # SDKs choose their own permissions; retain a private artifact tree.
    for path in output.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
    return report


class PipelineState(TypedDict, total=False):
    spec: str
    output: str
    fingerprint: str
    stage: str
    dataset_digest: str


def orchestrate(spec: Path, output: Path, database: Path, thread_id: str) -> dict[str, Any]:
    """Durable LangGraph stages; review pauses persist and resume only after actual acceptance."""
    require_backend("langgraph", "orchestration")
    require_backend("langgraph-checkpoint-sqlite", "orchestration")
    graph_api = importlib.import_module("langgraph.graph")
    sqlite_api = importlib.import_module("langgraph.checkpoint.sqlite")
    types = importlib.import_module("langgraph.types")
    serde = importlib.import_module("langgraph.checkpoint.serde.jsonplus")
    project = load_project(spec)
    _, hashes = load_sources(project, spec.parent)
    fingerprint = digest(
        {"project": project.model_dump(mode="json"), "sources": hashes, "output": str(output.resolve())}
    )
    database.parent.mkdir(parents=True, exist_ok=True)
    database.touch(mode=0o600, exist_ok=True)

    def check_review(state: PipelineState) -> dict[str, str]:
        path = Path(state["spec"])
        if (path.parent / "compilation.json").exists() and not review_status(path)["ready"]:
            types.interrupt({"status": "review_required", "spec": str(path)})
        require_review(path)
        return {"stage": "reviewed"}

    def generate(state: PipelineState) -> dict[str, str]:
        result = build(Path(state["spec"]), Path(state["output"]) / "run")
        return {"stage": "built", "dataset_digest": result["dataset_digest"]}

    def export(state: PipelineState) -> dict[str, str]:
        run, target = Path(state["output"]) / "run", Path(state["output"]) / "training"
        if (target / "export.json").exists():
            metadata = read_json(target / "export.json")
            if metadata.get("dataset_digest") != state["dataset_digest"]:
                raise ValueError("Existing export does not match the durable job")
            for filename, checksum in metadata["file_hashes"].items():
                if digest((target / filename).read_text()) != checksum:
                    raise ValueError("Existing export integrity check failed")
        else:
            # A crashed export can leave partial files; only known output files are recoverable.
            if target.exists():
                for path in target.iterdir():
                    if path.name not in {"train.jsonl", "development.jsonl", "test.jsonl"} or not path.is_file():
                        raise ValueError("Unexpected file in interrupted export")
                for path in target.iterdir():
                    path.unlink()
            export_run(run, target, "canonical")
        return {"stage": "complete"}

    builder = graph_api.StateGraph(PipelineState)
    builder.add_node("review", check_review)
    builder.add_node("build", generate)
    builder.add_node("export", export)
    builder.add_edge(graph_api.START, "review")
    builder.add_edge("review", "build")
    builder.add_edge("build", "export")
    builder.add_edge("export", graph_api.END)
    config = {"configurable": {"thread_id": thread_id}}
    with sqlite_api.SqliteSaver.from_conn_string(str(database)) as saver:
        saver.serde = serde.JsonPlusSerializer(allowed_msgpack_modules=[])
        graph = builder.compile(checkpointer=saver)
        prior = graph.get_state(config)
        if prior.values and prior.values.get("fingerprint") != fingerprint:
            raise ValueError("Durable job inputs changed; use a new thread ID")
        if prior.values and prior.values.get("stage") == "complete":
            # Verify persisted outputs even when the graph needs no additional work.
            load_run(output / "run")
            export(prior.values)
            return dict(prior.values)
        initial: Any = {
            "spec": str(spec.resolve()),
            "output": str(output.resolve()),
            "fingerprint": fingerprint,
            "stage": "pending",
        }
        if prior.values:
            initial = types.Command(resume=True) if any(t.interrupts for t in prior.tasks) else None
        result = dict(graph.invoke(initial, config))
        if "__interrupt__" in result:
            result.pop("__interrupt__")
            result["stage"] = "review_required"
        return result
