# Repository guidance

- Use English for all source code, documentation, examples, tests, and GitHub content.
- Keep the core pipeline runnable offline. External model calls require an explicit command and endpoint.
- Preserve source provenance, family-level data splits, and separation of model inputs from labels.
- Add behavioral tests for policy semantics, export contracts, integrity, and recovery when changed.
- Keep optional backends behind adapter boundaries and document their actual integration status.
- Run Ruff, mypy, pytest, MkDocs strict builds, and package builds before publishing a release.
- Use only fictional or appropriately licensed public examples. Keep private materials out of Git.
- Keep ROADMAP.md and .github/roadmap.json aligned when changing milestone scope.
