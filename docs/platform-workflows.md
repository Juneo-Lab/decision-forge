# Distribution, execution, and feedback

## Exact distribution targets

A recipe can request accepted-example counts for each node, stratum, split, and complete answer set:

```yaml
recipe:
  seed: 42
  candidate_pool_per_scenario: 128
  require_full_distribution: true
  distribution:
    - node_id: routing
      stratum: routine
      split: train
      valid_choices: [human]
      count: 12
    - node_id: routing
      stratum: routine
      split: train
      valid_choices: [agent]
      count: 12
```

An explicit target list includes only its named cells. The deterministic candidate pool is sampled
without replacement; quota sampling never changes labels. `distribution.json` records requested,
generated, rejected, filtered, accepted and shortfall counts. Jensen–Shannon divergence measures
normalized distribution deviation; absolute shortfalls remain visible even when normalized
proportions match. Impossible quotas stop publication by default. `require_full_distribution: false`
allows an explicitly partial dataset. Increase the candidate pool or supply additional scenarios
to cover missing cells. A zero target produces no accepted examples for that cell.

The four strata are `routine`, `boundary`, `change`, and `feedback`. A stratum is an authored design
annotation, not automatic evidence that an example has that property. Executable interventions add
an independently checked relationship. `transform` reads a completed dataset, an episode ID and:

```json
{"state_updates": {"amount": 1001}, "relation": "disjoint_choices", "stratum": "boundary"}
```

`same_choices` checks answer-set equality; `disjoint_choices` requires two nonempty, disjoint answer
sets. Both replay the policy engine. Every child retains its parent's family and split. Candidate
order rewrites use `candidate_order_seed`; they preserve IDs, attributes and verified choices.
Free-text paraphrases require a semantic verifier and are not part of this deterministic transform.

## Durable orchestration

```bash
uv sync --extra orchestration
uv run decision-forge orchestrate examples/customer_support/project.yaml \
  --output runs/job --database runs/jobs.sqlite --thread-id support-v1
```

The pinned LangGraph graph persists review, build, and export stages in SQLite. Checkpoints store
artifact paths and fingerprints. A compiler draft pauses until all current reviews are accepted;
rerunning the same command resumes the graph. Changed inputs require a new thread ID. Repeated
publication verifies existing file digests. Checkpoint deserialization permits primitive types only.
Use the core `build`/`export` commands when the orchestration extra is absent.

## Data Designer adapter

```bash
uv sync --extra designer
uv run decision-forge designer-process runs/demo --output runs/designer
```

The actual Data Designer 0.9.7 engine reads ordered seed records and generates a training-input
expression column. It preserves episode IDs, source inputs, labels, families and splits. Interrupted
engine work resumes under the same adapter fingerprint; completed records undergo invariant checks.
No model calls occur in this adapter. Its required provider registry points to a disabled localhost
endpoint; there are no LLM columns. Generation cost is therefore zero **for this processing stage**.
Model-written scenario synthesis and semantic paraphrase validation remain separate integration work.

## Multi-step business tasks

The three fictional task fixtures cover normal fulfillment, approval escalation, and revision:

```bash
export FORGE_API_KEY="your-provider-key"
uv run decision-forge workflow-run examples/customer_support/project.yaml \
  examples/customer_support/workflow-routine.json --output runs/workflow \
  --base-url https://your-model-host.example/v1 --model your-model --reasoning-effort high
```

Mock tools are `crm.assign`, `documents.share`, `approvals.act`, and `quality.submit`. Each tool call
records its decision-time state, action, resulting state, and next step. Denied calls leave state
unchanged. Approval requests can end in an explicit escalation; they do not grant real approval.
All effects occur inside the declared local simulator. No CRM, document-sharing or approval service
is contacted. Reset restores the initial snapshot exactly.

The solver receives current state, available candidates, policy descriptions, and the current tool.
It cannot see task-success criteria or verifier labels. An independent trace grader recomputes each
policy decision and transition from the original task and project. It reports task utility, policy
compliance, best-choice rate, escalation, completion, and trace validity separately. Provider cost,
unknown-cost attempts and latency are reported separately by the runner. Solver failures are retained.

The portable Harbor exporter remains a **single-step** adapter. It uses deterministic round-robin
sampling across kind/stratum cells and records the exact episode IDs and dataset digest. CI pins
Harbor 0.25.0 and executes eight tasks in Docker with both the oracle (reward 1) and no-op
(reward 0) controls. It verifies every trial and retains the task manifest and execution artifacts.
Multi-step container packaging and two distinct harness/model executions remain acceptance work.

## Runtime observations and reviewed feedback

`events-ingest events.json --database events.sqlite` appends a JSON array of `RuntimeEvent` records.
Each includes schema version, event/workflow/decision IDs, sequence, episode ID, event kind and payload.
A workflow starts at sequence zero and advances contiguously. Repeated identical event IDs are
idempotent; conflicting content or broken decision associations reject the complete transaction.

`decision`, `action`, `tool_result` and `outcome` are observations. A `review` additionally requires
`reviewer`, `reason`, and a candidate `choice`. Reviewer identity is attribution; a deployment must
authenticate users and protect write access to the event database.

```bash
uv run decision-forge feedback-plan runs/demo events.sqlite --output runs/feedback.json
uv run decision-forge lineage runs/demo --output runs/lineage.json
uv run decision-forge update revised-project.yaml runs/demo --output runs/updated
```

Only reviewed development-set errors produce feedback requests. Test examples and training examples
are excluded from adaptive feedback. A review that disagrees with the declared policy requires policy
review before generation; observational success never becomes a verified label automatically.

Dataset updates freeze a cumulative family registry, including removed families. Restoring a family
cannot move it from held-out data into training. Scenario caches bind the resolved split and generation
parameters. Updates reuse compatible scenarios, preserve prior dataset identity, and report changed
volume and reuse. Current source invalidation is document-wide; fine-grained source-span reuse and
model-weight lineage remain open work.
