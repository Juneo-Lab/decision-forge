# Benchmarks

Offline pipeline measurements for the `cold-and-warm-offline-v1` protocol. The protocol measures
deterministic generation, policy verification, deduplication, quota selection, and artifact writing.
It makes no model calls, so no provider latency or cost is included.

## Protocol

1. Start from the shipped fictional example and replicate its four seed scenarios across `N` synthetic
   scenario families.
2. Build twice into a fresh output directory: the first run is a cold cache, the second reuses it.
3. Assert that both runs produce the same dataset digest before reporting timings, so a speedup can
   never come from a silently different dataset.
4. Record the engine fingerprint, dependency versions, host platform, accepted episode count, artifact
   size, and specification size with the timings.

```bash
uv run python scripts/benchmark_pipeline.py \
  --workspace runs/benchmark-workspace \
  --report runs/benchmark.json \
  --families 25 100 400
```

## Reference run

| Component | Value |
| --- | --- |
| Host | macOS 26.6, arm64 (Apple silicon), local SSD |
| Python | 3.13.5 |
| Decision Forge | 0.3.0 |
| pydantic / filelock | 2.14.2 / 3.32.7 |

| Families | Scenarios | Accepted episodes | Cold (s) | Warm (s) | Episodes/s (cold) | Run artifacts | Specification |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 25 | 100 | 700 | 0.20 | 0.33 | 3,566 | 1.2 MB | 43 KB |
| 100 | 400 | 2,800 | 0.75 | 0.55 | 3,710 | 4.6 MB | 128 KB |
| 400 | 1,600 | 11,200 | 2.98 | 2.20 | 3,756 | 18.3 MB | 470 KB |

Throughput is close to linear in scenario count (~3.7k accepted episodes per second), so the
deterministic path is not a scaling bottleneck. Warm runs are faster per episode only once the
per-scenario cache holds enough work to amortize the read cost, which is why the 25-family case is
slower warm than cold.

## Reading these numbers

- Timings are single-machine and single-process. They describe the offline compiler, not a service.
- Model-backed steps (`compile-corpus`, `expand`) are excluded: their latency and cost are dominated by
  the endpoint and are recorded per run in `calls.json` and `expansion.json` instead.
- The dataset digest is asserted equal across cold and warm runs; a benchmark that changes the dataset
  is a failure, not a speedup.
- Streaming, parallel workers, and multi-node execution are not implemented, so no such numbers are
  claimed.

## Cost

Generation is free of model calls. The two archived model experiments report their own costs:
document compilation at $0.29 (Claude Opus 5.5) and $0.15 (GPT-6.1 Sol), multi-step workflows at
$0.036 for 22 calls. See [`experiments/`](https://github.com/Juneo-Lab/decision-forge/tree/main/experiments)
for the preserved journals, including failed attempts whose cost is unknown rather than zero.
