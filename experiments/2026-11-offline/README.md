# Offline pipeline benchmark

Measured on 2026-11-01 using the fictional customer-support fixture and the unchanged engine from
commit `2462cf41cf36501aad32b68c2e0ab201a250490a`. The pending PR #27 merge changes documentation and
packaging metadata; it does not modify benchmark or pipeline code. `report.json` is the unedited
output of the command below and records the engine fingerprint, environment, timings, and digests.

```bash
uv run python scripts/benchmark_pipeline.py \
  --workspace runs/benchmark-workspace \
  --report runs/benchmark.json \
  --families 25 100 400
```

Use a new workspace directory. Each size is measured once cold and once warm; dataset digests must
match. Inputs are synthetic and model calls are zero. See [the benchmark protocol](../../docs/benchmarks.md).
