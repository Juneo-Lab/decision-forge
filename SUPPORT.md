# Support

## Questions and usage help

Open an issue using the Question template. Include the command you ran, the
Decision Forge version (`python -c "import decision_forge; print(decision_forge.__version__)"), the Python version, and the relevant part of
`report.json` or `calls.json`. Never paste credentials or customer documents.

## Bugs

Use the bug report template. A reproducible case is worth more than a description: a minimal
specification, a small fictional source document, and the exact command let a maintainer reproduce the
failure offline.

## Security

Do not report vulnerabilities in public issues. Follow [`SECURITY.md`](SECURITY.md).

## What is out of scope

- Debugging private organizational documents or third-party model endpoints.
- Operating a hosted service. Decision Forge is a library and CLI.
- Training or serving models. The repository ships data contracts and an evaluation harness, not a trainer.

## Response expectations

This is a maintainer-led project with no service-level agreement. Issues with a reproduction are
triaged first; feature requests are evaluated against the [roadmap](ROADMAP.md) rather than queued.
