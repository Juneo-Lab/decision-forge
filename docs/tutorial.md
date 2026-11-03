# Tutorial: from a policy to training data in five minutes

This tutorial turns one organization's written agent policy into verified decision training data,
changes the policy, and watches the labels follow. It runs offline: no API keys and no model downloads.

[Open the notebook version in Colab](https://colab.research.google.com/github/Juneo-Lab/decision-forge/blob/main/examples/agent_delegation/tutorial.ipynb)
to run the same steps in a browser.

## 1. Install

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Juneo-Lab/decision-forge
cd decision-forge
uv sync
```

## 2. Start from a written policy

Copy the fictional Halcyon Health organization from the
[agent delegation example](https://github.com/Juneo-Lab/decision-forge/tree/main/examples/agent_delegation):

```bash
cp -r examples/agent_delegation/halcyon-health my-org
```

`my-org/sources/policy.md` is the policy as the organization wrote it. One sentence governs refunds:

> Agents may issue refunds of up to 100 dollars to verified patients without approval.

`my-org/project.yaml` is the specification. It declares what an agent may attempt (four decision nodes,
each with candidate outcomes such as `proceed`, `hand-back`, and `block`), the situations to generate
(dimensions such as `amount` and `customer_verified`), and rules. Every rule quotes the sentence it
encodes:

```yaml
- id: refund-proceed
  description: Verified patients may receive refunds up to 100 dollars without approval.
  when:
    all_of:
    - {path: candidate.id, op: eq, value: proceed}
    - {path: state.customer_verified, op: eq, value: true}
    - {path: state.amount, op: lte, value: 100}
  effect: allow
  score: 3
  evidence:
  - source_id: policy
    quote: Agents may issue refunds of up to 100 dollars to verified patients without approval.
```

## 3. Build verified decisions

```bash
uv run decision-forge validate my-org/project.yaml
uv run decision-forge build my-org/project.yaml --output runs/my-org
```

The build generates 17 situations, labels every one by replaying the declared rules, and writes
`runs/my-org/episodes.jsonl` with a report, a hash-bound snapshot, and an integrity-checked manifest.
Here is the refund of 400 dollars to a verified patient, as stored:

```json
{
  "question": "An agent wants to issue a refund through the payments tool. What should happen?",
  "state": {"amount": 400, "customer_verified": true},
  "valid_choices": ["hand-back"],
  "split": "train"
}
```

The label is `hand-back` because only `refund-hand-back` ("Every other refund for a verified patient
goes to a person in patient accounts.") allows a candidate in that situation. Each episode records the
rules that matched every candidate, so the label can be audited.

## 4. Change the policy, watch the labels follow

Raise the threshold to 500 dollars in the policy, but leave the specification alone:

```bash
sed -i.bak 's/refunds of up to 100 dollars/refunds of up to 500 dollars/' my-org/sources/policy.md
uv run decision-forge build my-org/project.yaml --output runs/my-org-v2
```

The build refuses:

```text
Error: Evidence quote rejected for rule refund-proceed: Quote is not present in the source text
```

A rule may not cite text the organization no longer says. Update the rule to match, both its quote and
its threshold, and build again:

```bash
sed -i.bak 's/refunds of up to 100 dollars/refunds of up to 500 dollars/; s/value: 100$/value: 500/' my-org/project.yaml
uv run decision-forge build my-org/project.yaml --output runs/my-org-v2
```

The same 400-dollar refund is now labeled `proceed`. It stays in the same split: the first build wrote
`my-org/project.splits.lock.json`, and every later build keeps each family where it was published, so
a revised dataset never moves held-out examples into training.

## 5. Export training data

```bash
uv run decision-forge export runs/my-org-v2 --output runs/my-org-train --format decision
```

`runs/my-org-train/` holds `train.jsonl`, `development.jsonl`, and `test.jsonl`. Each row carries the
question, the decision state, the policy context, explicit options with aligned target probabilities,
the family used for grouping, and provenance: the dataset digest, the rules, and the policy
fingerprint. [Decision training](decision-training.md) describes the format and the other three.

## 6. Compare two organizations

```bash
uv run python scripts/compare_organizations.py \
  examples/agent_delegation/lumen-labs/project.yaml \
  examples/agent_delegation/halcyon-health/project.yaml \
  --workspace runs/compare
```

The same 17 situations, decided by a speed-first startup and by a clinic network: 8 come out
differently, and every decision cites the sentence that governs it.

## Next steps

- Write your own policy and specification, or let `compile-corpus` draft one from your documents
  through a model endpoint you choose. Compiled specifications pass a content-bound business review
  before they can build: see the [organization compiler](organization-compiler.md).
- Control how many examples each node, stratum, split, and answer receives with
  [generation planning](generation.md).
- Read [Why Decision Forge](vision.md) for the problem this data is meant to solve.
