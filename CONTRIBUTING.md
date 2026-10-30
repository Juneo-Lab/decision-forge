# Contributing

Welcome to Decision Forge. Contributions should improve the path from organizational evidence to
verifiable decision data. Use English for code, comments, documentation, issues, and pull requests.

## Local development

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run mkdocs build --strict
uv build
```

Start from an issue with a clear use case and acceptance criteria. For new architecture or public
interfaces, write a short design proposal explaining inputs, outputs, failure behavior, provenance,
and compatibility. Keep generated examples fictional or explicitly licensed for redistribution.

## Pull requests

Explain the user-visible behavior, implementation choices, and validation performed. Include tests
for policy semantics, lineage, split isolation, and failure recovery when affected. Preserve existing
public contracts or include a migration plan. Public APIs use typed inputs and explicit outputs.

## Integration boundaries

The offline core must remain usable without model credentials, hosted services, or heavyweight
generation runtimes. Optional adapters declare their dependency versions and have isolated contract
tests. Never send source documents to a model endpoint as an implicit side effect of validation.

## Data and claims

Keep raw company materials in ignored directories such as `private/` and generated data in `runs/`.
Record source permissions, dataset licenses, and provenance in adapters that ingest external material.
State whether a result comes from declared rules, simulation, human review, or real execution.
Use development data for iteration and freeze test families before augmentation.

Contributions are licensed under Apache-2.0. By submitting a contribution, you confirm that you have
the right to contribute it under this license. Maintainers review changes before release.
