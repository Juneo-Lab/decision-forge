# Generation planning, verified synthesis, and deduplication

This page describes the three stages that turn a reviewed specification into a dataset whose
distribution is deliberate rather than incidental:

1. **Plan** exact per-cell quotas from the deterministic pool, offline.
2. **Synthesize** the missing situations with a model, verifying every proposal by policy replay.
3. **Deduplicate** the merged pool globally, including held-out leak detection.

The offline core still runs without credentials. Stages 2 requires an explicit `expand` command with
a model endpoint; nothing calls a model implicitly.

## 1. Plan the recipe

```bash
uv run decision-forge plan-recipe examples/customer_support/project.yaml \
  --output runs/plan --per-cell 5
```

The planner is an agent with a typed, discoverable tool registry. Each call is appended to a ledger so
a plan can be audited and resumed instead of being re-derived in memory:

| Tool | Purpose |
| --- | --- |
| `inspect_project` | Nodes, candidates, dimensions, families, strata, declared quotas |
| `measure_pool` | Generate the deterministic pool in memory and count examples per exact cell |
| `propose_quotas` | Derive quotas from the objective, optionally scaled by a model advisor |
| `check_feasibility` | Compare requested quotas with measured supply |
| `plan_expansion` | Turn the shortfall into verified-synthesis requests plus a call estimate |
| `commit_plan` | Persist `plan.json`, `quotas.md`, and `expansion-requests.json` |

A cell is `(node_id, stratum, split, valid_choices)`. The plan then separates three kinds of gap:

- `expansion_requests`: cells a model can plausibly fill.
- `unfillable` with `held_out_split`: held-out cells. Synthesizing them would reveal the target
  answer for the split that measures generalization, so they are never requested.
- `unfillable` with an "already exist" reason: the node's declared state space is fully enumerated,
  so a model could only restate situations the pool already holds.

Derived quotas clamp held-out cells to what generation actually produced, and the clamp is reported in
`held_out_clamped`. An explicit `--mode explicit --targets` quota is never changed silently: the
shortfall is reported and the build refuses the plan.

Pass `--base-url` and `--model` to let a model re-weight the measured cells. Weights outside the
measured cell set are rejected; the advisor can shape the distribution but not invent cells.

## 2. Synthesize the missing situations

```bash
export FORGE_API_KEY=...   # explicit, per command
uv run decision-forge expand runs/demo runs/plan/expansion-requests.json \
  --output runs/expansion \
  --base-url https://api.deepseek.com/v1 --model deepseek-chat \
  --states-per-call 8 --max-calls 20 --max-output-tokens 8000
```

The model proposes **state bindings only**, never labels:

```json
{"states": [{"bindings": [{"path": "state.tier", "value": "gold"}], "rationale": "..."}]}
```

Every proposal is then replayed through the declared policy:

| Gate | Rejection reason recorded in `quarantine.json` |
| --- | --- |
| Path must be decision-relevant | `Proposal sets an undeclared state path: ...` |
| Dimension values are the value space | `Proposal value outside the declared dimension domain: ...` |
| The policy must produce the requested answer set | `Policy replay produced [...] instead of the requested [...]` |
| A state with no permitted candidate is useless | `Synthesized state leaves no permitted candidate` |
| Inputs must be new | `exact duplicate` / `near duplicate of an existing example` |

Published rows carry `label_basis: model_proposed_policy_verified` and a provenance record in
`intervention`: the request digest, the base episode, and the applied bindings.

Operational properties:

- **Held-out data is never synthesized.** `ExpansionRequest.split` only accepts `train` and
  `development`; the build refuses any synthesized row in `test`.
- **Provider failure cannot duplicate publication.** The journal reserves a call before the request,
  accepted rows are checkpointed to `accepted.jsonl` after each call, and `expansion.json` is written
  once at the end. A resumed run continues from the next call index and reloads the verified rows.
- **Budgets are explicit and resumable.** `max_calls` and `max_input_bytes` are excluded from the
  fingerprint so raising them resumes the same artifact. `max_empty_rounds` stops a model that keeps
  repeating itself; verified work is kept and the remaining gap is reported in `shortfalls`.
- **Cost is reported, or reported as unknown.** `usage.cost_usd` is `null` when the endpoint returns
  no price. The build reports `cost_basis: provider_reported | unreported_by_endpoint`, never `0`.

## 3. Deduplicate globally and detect leaks

```bash
uv run decision-forge dedup runs/merged
```

Scenario-local identity cannot see that two scenarios describe the same decision problem, because it
includes the scenario ID. Deduplication therefore works on the model-visible input:

1. A **structural key** (organization, policy version/context, state, and candidate identities/attributes)
   groups examples that pose the same problem. Different visible policies remain separate.
   Wording can only be compared inside a group.
2. **MinHash over normalized word tokens** estimates presentation similarity inside a group, so a
   cosmetic edit collapses. Word tokens are used instead of character n-grams because a one-word edit
   in a short presentation changes most character n-grams.

Keeper policy: the most held-out member survives (`test` > `development` > `train`, then lowest ID).
A cross-split duplicate is a **split leak**: the training copy is dropped and listed in `split_leaks`,
because a near-identical training row inflates held-out scores. Identical inputs that disagree about
`stratum` or the label set are reported in `metadata_conflicts`; keeping one copy would otherwise
silently empty a quota cell.

## 4. Build with the plan and the synthesis

```bash
uv run decision-forge build examples/customer_support/project.yaml \
  --output runs/final \
  --recipe-plan runs/plan/plan.json \
  --expansion runs/expansion
```

The build re-verifies every synthesized row (current scenario/family/node lineage, family split,
source hashes, policy replay, and policy fingerprint) before merging it, so a hand-edited expansion
artifact cannot inject a wrong label. The merged pool is deduplicated, then quotas are applied. `report.json` gains `synthesized`, `dedup`, and a
`generation_cost` block with real provider calls and cost basis.

## 5. Measure whether the data helps

`scripts/finetune_evaluation.py` closes the loop with a fixed budget:

```bash
uv run python scripts/finetune_evaluation.py prepare runs/final --output runs/finetune --budget-examples 2000
# use a candidate-readout trainer with the exported rows, then serve the checkpoint
uv run python scripts/finetune_evaluation.py evaluate runs/final --output runs/base.json \
  --arm base_model --base-url https://api.deepseek.com/v1 --model deepseek-chat
uv run python scripts/finetune_evaluation.py evaluate runs/final --output runs/tuned.json \
  --arm fine_tuned --base-url http://localhost:8000/v1 --model local-lora
uv run python scripts/finetune_evaluation.py compare runs/base.json runs/tuned.json --output runs/compare.json
```

`prepare` exports candidate-probability decision rows and writes a pinned handoff plan. Its example
budget limits the actual training file; selected IDs and file digests make that selection auditable.
It preserves held-out family assignments and refuses a dataset without eligible training or test rows.
Training requires a compatible candidate-readout trainer; see the [decision training contract](decision-training.md).

The endpoint commands measure generated-choice accuracy separately from candidate NLL. Both arms
must use the same held-out cases and fixed prompt. Dataset identity alone does not establish that a
training run occurred or improved decision quality.

## What this does not claim

Model-written synthesis currently proposes *decision-time state*, not new policy text. The model cannot
introduce rules, candidates, or evidence, so the label space stays closed and every published label is
traceable to declared policy. Free-text presentation rewrites are available through
`decision-forge rewrite`, which requires deterministic invariants (unchanged option set and unchanged
numeric values) plus an independent equivalence decision before publication.
