# Decision training contract

Decision Forge produces supervision for four organizational judgments: work allocation, information
sharing, action selection, and outcome acceptance. Each training example asks the model to choose
among explicit candidates using the available state and organizational policy.

## Export candidate probabilities

```bash
uv run decision-forge build examples/customer_support/project.yaml --output runs/decisions
uv run decision-forge export runs/decisions --output runs/decision-training --format decision
uv run python scripts/finetune_evaluation.py prepare runs/decisions \
  --output runs/training-plan --budget-examples 2000 --base-model your-base-model
```

The `decision` format exports JSONL with `objective: candidate_readout`:

| Field | Meaning |
| --- | --- |
| `id` | Stable episode identity |
| `system`, `input` | Model-visible task, state, and policy context |
| `options` | Ordered `{key, description}` candidates; descriptions include candidate IDs and decision-relevant attributes |
| `target_probs` | Probability target aligned with the original option order |
| `root_id`, `normalized_group` | Organization-scoped family identity shared by related variants |
| `scope`, `provenance` | Supervision basis, source hashes, rule references, and dataset digest |
| `weight` | Unit row weight |

A unique policy-verified best choice yields a one-hot target. Multiple best choices remain set-valued
in the canonical export and are skipped in decision export, with per-split counts in `export.json`.
Rule priority and rule scores determine policy decisions; they have no probability interpretation.

`DecisionRow.model_validate(row)` also accepts existing candidate-readout rows with explicit soft
probabilities. It preserves those probabilities, history, and additional metadata. Importers remain
responsible for verifying the original supervision and maintaining family splits. Validation checks
the schema and target alignment; it does not certify an external label's correctness.

## Native candidate-label encoding

```python
from decision_forge.training import DecisionRow, encode_decision

DecisionRow.model_validate(row)
encoded = encode_decision(row, tokenizer, seed=1015, max_tokens=8192)
```

The tokenizer supplies distinct single-token labels from `A`–`Z`, then `AA`–`ZZ`. A deterministic
per-row permutation moves candidate descriptions and their target probabilities together. The encoder
uses the tokenizer's native chat template with `enable_thinking=False` and an assistant generation
prefix. It appends no answer token. `label_to_key` maps the selected label back to the original option.
The tokenizer must support this template setting and be kept unchanged during encoding.

At the last prompt position, gather logits for `candidate_token_ids` and minimize:

```text
loss = -sum(target_probs * log_softmax(candidate_logits))
```

The returned `labels` are all `-100`; the candidate-readout trainer computes the objective explicitly.
A trainer that only uses ordinary answer-token SFT loss cannot consume this encoding as a training
objective. Overlong prompts and insufficient distinct single-token labels produce errors. The encoder
never silently truncates policy context or candidates.

Only `system`, `input`, and candidate descriptions enter the prompt. Targets, family identifiers,
source references, and verification records stay outside model inputs. Exported policy context contains
the rules needed to decide, while per-example verdicts and the verified answer remain supervision.

## Budgets and evaluation

`prepare` writes a decision export, an auditable `plan.json`, and handoff instructions. The budget
limits actual training file rows after eligibility checks, using deterministic episode-ID order.
Development and test splits retain their original eligible rows. The plan records selected IDs,
export file digests, actual counts, and the candidate-readout objective. It records `status: prepared`
and `training_launched: false`; a compatible trainer must execute the run.

Evaluate the trained checkpoint with candidate NLL, candidate accuracy, per-surface and per-stratum
accuracy, and held-out family performance. Preserve reference soft targets when calculating NLL.
The existing endpoint `evaluate`/`compare` commands separately measure generated-choice accuracy;
they do not measure candidate NLL or execute training. Use the same cases and prompt for both arms.

The production data path stays focused on organizational decisions. Workflow execution supplies
states and feedback for those decisions. World-observation targets are excluded from this contract.
