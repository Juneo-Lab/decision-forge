# Decision Forge

Decision Forge compiles organizational workflows into evidence-linked decision training data.
The four decision surfaces are work allocation, information sharing, action selection, and outcome
evaluation.

## Quick start

From a source checkout with Python 3.11+ and uv installed:

```bash
uv sync --group dev
uv run decision-forge validate examples/customer_support/project.yaml
uv run decision-forge build examples/customer_support/project.yaml --output runs/demo
uv run decision-forge export runs/demo --output runs/demo-typed --format typed
```

The fictional example builds 336 episodes. All variants of a customer case share one split. Hash-based
splitting preserves family membership; small datasets may differ from requested split proportions.
The report records realized counts. No model credentials are required for this workflow.

## Command reference

| Command | Purpose |
| --- | --- |
| `validate SPEC` | Validate schema, references, source paths, and evidence quotations |
| `build SPEC --output DIR` | Generate episodes and publish a verified run manifest |
| `export RUN --output DIR --format typed` | Export typed, SFT, or canonical training views |
| `compile FILE... --output DIR --base-url URL --model MODEL` | Compile documents using an explicit model endpoint |
| `impact SPEC PREVIOUS_RUN` | Report changed, added, removed, and reusable scenario work |
| `replay RUN PREDICTIONS` | Score recorded candidate choices against declared policies |
| `harbor-export RUN --output DIR` | Produce portable single-step decision tasks |
| `schema --output FILE` | Export the project JSON Schema |

Read the [architecture](architecture.md), [specification](specification.md), and
[data protocol](data-protocol.md) before adapting private company workflows.
