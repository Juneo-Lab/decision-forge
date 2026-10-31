"""Measure cold/warm offline builds with explicit fixture size, hardware and versions."""

from __future__ import annotations

import argparse
import platform
import shutil
import time
from importlib.metadata import version
from pathlib import Path

from decision_forge.io import load_project, write_json
from decision_forge.pipeline import build, engine_fingerprint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--families", type=int, nargs="+", default=[100, 1000])
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.workspace.exists():
        raise ValueError("Benchmark workspace must be new to measure a cold cache")
    example = Path(__file__).resolve().parents[1] / "examples/customer_support/project.yaml"
    measurements = []
    for families in args.families:
        if not 1 <= families <= 1000:
            raise ValueError("Benchmark supports 1 through 1000 families per case")
        folder = args.workspace / str(families)
        shutil.copytree(example.parent, folder / "input")
        spec = folder / "input/project.yaml"
        project = load_project(spec)
        seeds = project.scenarios[:4]
        project.scenarios = []
        for family in range(families):
            for original in seeds:
                scenario = original.model_copy(deep=True)
                scenario.id = f"synthetic-{scenario.node_id}-{family}"
                scenario.family_id = f"scale-family-{family}"
                scenario.state["case_id"] = scenario.family_id
                project.scenarios.append(scenario)
        write_json(spec, project.model_dump(mode="json"))
        timings = []
        results = []
        for _ in range(2):
            started = time.perf_counter()
            results.append(build(spec, folder / "run"))
            timings.append(time.perf_counter() - started)
        assert results[0]["dataset_digest"] == results[1]["dataset_digest"]
        measurements.append(
            {
                "families": families,
                "scenarios": len(project.scenarios),
                "accepted": results[0]["report"]["accepted"],
                "cold_seconds": timings[0],
                "warm_seconds": timings[1],
                "cold_examples_per_second": results[0]["report"]["accepted"] / timings[0],
                "warm_cache_hits": results[1]["report"]["cache_hits"],
                "dataset_digest": results[0]["dataset_digest"],
                "run_bytes": sum(p.stat().st_size for p in (folder / "run").rglob("*") if p.is_file()),
                "specification_bytes": spec.stat().st_size,
                "provider_calls": 0,
                "provider_cost_usd": 0,
            }
        )
        print({"families": families, "cold_seconds": timings[0], "warm_seconds": timings[1]}, flush=True)
    write_json(
        args.report,
        {
            "protocol": "cold-and-warm-offline-v1",
            "engine_fingerprint": engine_fingerprint(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "dependencies": {name: version(name) for name in ["decision-forge", "pydantic", "filelock"]},
            "measurements": measurements,
            "quality_scope": "Declarative-policy verification; no downstream model training.",
        },
    )


if __name__ == "__main__":
    main()
