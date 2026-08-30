# Contributing to Slyme

Thank you for helping improve Slyme. Changes should be small enough to review,
covered by tests, and compatible with every supported Python version.

## Development setup

Install [uv](https://docs.astral.sh/uv/), clone the repository, and create the
locked development environment:

~~~bash
uv sync --locked --all-groups
uv run pre-commit install --install-hooks
~~~

The second command installs both commit-time checks and the pre-push test hook.
The repository supports Python 3.9 through 3.14; CI exercises the complete
matrix.

## Quality gates

Run the same gates as CI before opening a pull request:

~~~bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run pre-commit run --all-files
~~~

Pytest enforces branch coverage of at least 90%. Add focused regression tests
for bugs and cover synchronous and asynchronous behavior when both are part of
the affected API. Hypothesis is available for invariant and round-trip tests.

To build and inspect the release artifacts locally:

~~~bash
uv run python -m build
uv run twine check --strict dist/*
~~~

Documentation requires Node.js 24:

~~~bash
npm ci --prefix docs
npm run --prefix docs docs:build
~~~

## Pull requests

- Discuss significant public API changes in an issue first.
- Update documentation and CHANGELOG.md for user-visible changes.
- Keep unrelated refactors out of a focused fix.
- Do not edit uv.lock manually; regenerate it with uv lock.
- Never include secrets, credentials, private data, or generated environments.

All GitHub Actions are pinned to immutable commit SHAs. Dependabot maintains
Python, documentation, and Actions dependencies. Releases use PyPI trusted
publishing and build provenance attestations; maintainers should not add a PyPI
API token to repository secrets.

Please report vulnerabilities through the process in [SECURITY.md](SECURITY.md),
not through a public issue.
