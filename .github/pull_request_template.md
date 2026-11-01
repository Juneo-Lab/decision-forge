<!-- Use English. Link the issue whose acceptance criterion this satisfies, e.g. "Closes #12". -->

## Behavior

Describe the problem and resulting user-visible behavior.

## Validation

List the relevant checks and results.

## Compatibility and data

Describe schema/API changes, migrations, and any effects on provenance or split isolation.

## Checklist

- [ ] A behavioral test fails without this change and passes with it (or this is docs/CI only).
- [ ] `ruff check`, `ruff format --check`, `mypy src`, `pytest --cov-fail-under=80`, and `mkdocs build --strict` pass.
- [ ] `CHANGELOG.md` is updated for user-visible changes; docs are updated for changed commands or contracts.
- [ ] Examples remain fictional or appropriately licensed; no private material or credentials are included.
