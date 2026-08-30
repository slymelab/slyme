# Contributing

Welcome to Slyme! 🎉 We are thrilled that you are considering contributing to this project. Whether it's fixing bugs, improving documentation, or proposing new feature ideas, every participation makes Slyme better.

## Submitting Pull Requests

We greatly look forward to receiving your Pull Requests (PRs). When preparing to submit code, please keep the following simple development guidelines in mind:

* **Tests**: Add focused tests for every behavior change. The suite uses pytest,
  pytest-asyncio, Hypothesis, and branch coverage with a 90% minimum.
* **Quality gates**: Ruff formatting and linting, mypy, the Python 3.9–3.14
  compatibility matrix, package checks, and documentation builds must pass.
* **Local hooks**: Install the repository's commit and pre-push hooks with
  `uv run pre-commit install --install-hooks`.

Create the locked development environment and run the same gates as CI:

```bash
uv sync --locked --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

See the root [CONTRIBUTING.md](https://github.com/slymelab/slyme/blob/main/CONTRIBUTING.md)
for release, documentation, and security guidance.

## Regarding Feature Extensions and API Design

As the core foundation of the entire ecosystem, Slyme's stability directly affects all developers who depend on it.

Because of this, we adopt a rigorous but absolutely open attitude toward introducing new features or adjusting core APIs. But this **is by no means** to keep everyone's good ideas out! On the contrary, we are very eager to hear your innovative ideas.

We hope that before everyone invests significant effort in writing code, they can fully communicate with you and the entire community. Through collective brainstorming in the early stages, we can jointly refine a better development experience while ensuring **backward compatibility** and **system stability**.

::: tip A Small Suggestion
If you have a brilliant idea about new features or API design, it is highly recommended to open an Issue to discuss your thoughts with us before writing any code. We look forward to exploring the best implementation plan with you!
:::

Thank you again for your attention and support of Slyme. We look forward to your contributions!
