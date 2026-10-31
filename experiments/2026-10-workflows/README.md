# Local workflow execution and pipeline scale

## Protocol

Three fictional, predeclared tasks exercise the four decision surfaces: normal fulfillment,
refund approval escalation, and delivery revision. Both models receive identical task observations
and the same policy descriptions. The solver outputs only a candidate ID; no reference label or
success predicate is sent to it. A separate replay grader recomputes permission and state transitions.

Models: `anthropic/claude-opus-5.5` and `openai/gpt-6.1-sol`, through OpenRouter, reasoning `high`,
omitted temperature, 4,096 output-token cap, no transport retries. Each task has an eight-attempt
call budget and a conservative 100,000-token reservation budget. The test date is 2026-10-31.
The model-specific directory name is the first 12 characters of the canonical model-ID digest.

| Model | Tasks completed | Permitted decisions | Known cost (USD) |
| --- | ---: | ---: | ---: |
| Claude Opus 5.5 | 3/3 | 11/11 | 0.026628 |
| GPT-6.1 Sol | 3/3 | 11/11 | 0.009784 |

All 22 attempts returned usage and cost. No failed calls occurred in this run. Total reported cost
was $0.036412. Escalation and revision count as successful outcomes for their respective tasks;
neither implies a completed refund or an accepted delivery. These fixtures establish compatibility
and execution behavior on three small scenarios. They are not a general-purpose decision benchmark
or evidence of downstream fine-tuning gains.

Each task directory preserves configuration, per-attempt call accounting, solver trace, independent
grading, cost, and measured latency. There are no raw prompts beyond fictional task observations,
provider credentials, or hidden model reasoning in the archive. Tests replay the traces offline.

## Reproduce model runs

```bash
export FORGE_API_KEY="your-openrouter-key"
uv run python scripts/evaluate_workflows.py \
  --spec examples/customer_support/project.yaml \
  --tasks examples/customer_support/workflow-routine.json \
    examples/customer_support/workflow-escalation.json \
    examples/customer_support/workflow-revision.json \
  --base-url https://openrouter.ai/api/v1 \
  --models anthropic/claude-opus-5.5 openai/gpt-6.1-sol \
  --output runs/repeated-workflow-study
```

The script requires a fresh output directory and retains failed attempts. Live reruns incur provider
charges. Exact trace choices and latency may vary.

## Scale measurements

`scale.json` records Python, dependency versions, platform, source specification sizes, output storage,
cold/warm build times, accepted counts, and cache hits. The workload repeats the fictional four-surface
scenario structure with distinct family/case IDs. The offline generator makes no model requests;
reported zero provider cost applies to this stage only. Timing includes verification and artifact writes.

```bash
uv run python scripts/benchmark_pipeline.py --families 100 1000 \
  --workspace runs/repeated-scale-study --report runs/repeated-scale-study.json
```

These are local measurements with one repetition per cold/warm condition. They provide a reproducible
baseline; they do not establish a cross-hardware performance claim.

## Harbor container controls

[The Docker CI job](https://github.com/Juneo-Lab/decision-forge/actions/runs/41175746817/job/124462826677)
ran Harbor 0.25.0 on eight stratified single-step tasks. Oracle rewards were 1 for all eight tasks;
no-op rewards were 0 for the same eight tasks. Both controls completed without execution errors.
These controls validate packaging, permissions, task reset and verifier execution. They do not
measure a model's decision quality. The CI job retains the task IDs and individual trial artifacts.
