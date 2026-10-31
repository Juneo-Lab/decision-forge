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

Measured on 2026-11-01. The [unedited JSON report](https://github.com/Juneo-Lab/decision-forge/blob/main/experiments/2026-11-offline/report.json)
records the engine fingerprint and dataset digests. MB and KB below use decimal units.

| Component | Value |
| --- | --- |
| Host | macOS 26.6, arm64 |
| Python | 3.12.12 |
| Decision Forge | 0.3.0 |
| pydantic / filelock | 2.14.2 / 4.2.4 |

| Families | Scenarios | Accepted episodes | Cold (s) | Warm (s) | Episodes/s (cold) | Run artifacts | Specification |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 25 | 100 | 700 | 0.221 | 0.140 | 3,168 | 1.18 MB | 43.9 KB |
| 100 | 400 | 2,800 | 0.835 | 0.570 | 3,352 | 4.60 MB | 130.8 KB |
| 400 | 1,600 | 11,200 | 3.340 | 2.380 | 3,354 | 18.30 MB | 481.8 KB |

Cold throughput ranged from 3,168 to 3,354 accepted episodes per second on these inputs. Each size
has one cold and one warm measurement; these results characterize the recorded fixture and host.

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
successful document-compilation runs at $0.293428 (Claude Opus 5.5) and $0.1505675 (GPT-6.1 Sol),
and multi-step workflows at $0.036412 for 22 calls. The compilation ledger totals $1.08587197 in
known costs across attempts plus five attempts with unknown cost; total billed cost is unknown. See [`experiments/`](https://github.com/Juneo-Lab/decision-forge/tree/main/experiments)
for the preserved journals, including failed attempts whose cost is unknown rather than zero.
