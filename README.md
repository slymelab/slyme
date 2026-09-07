<div align="center">
  <img src="https://raw.githubusercontent.com/slymelab/slyme/main/assets/images/logo.jpg" alt="Slyme Logo" width="200" style="max-width: 50%;" />

  <p><em>SLYME Lets You Mold Everything.</em></p>

  <p>
    <a href="https://pypi.org/project/slyme/"><img src="https://img.shields.io/pypi/v/slyme.svg?label=PyPI" alt="PyPI version"></a>
    <a href="https://github.com/slymelab/slyme/actions/workflows/ci.yml"><img src="https://github.com/slymelab/slyme/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
    <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python version">
    <a href="https://slymelab.github.io/slyme/"><img src="https://img.shields.io/badge/docs-latest-blue.svg" alt="Documentation"></a>
    <a href="https://github.com/slymelab/slyme/blob/main/LICENSE"><img src="https://img.shields.io/github/license/slymelab/slyme" alt="License"></a>
  </p>

  <p>
    <b>English</b> |
    <a href="https://github.com/slymelab/slyme/blob/main/i18n/README_zh.md">简体中文</a>
  </p>
</div>

## About Slyme

Slyme (pronounced /slaɪm/) is a highly composable functional execution framework. It enables developers to seamlessly build arbitrarily complex execution flows based on simple, reusable functions, without needing to master cumbersome APIs or syntax.

Whether you are building complex LLM pipelines, executing DAGs, or creating generic data-processing flows, Slyme provides a structural, functional, and deeply Pythonic foundation.

## Installation

Slyme requires **Python 3.10+**. You can install it directly via pip:

```bash
pip install slyme
```

*(Note: Slyme is extremely lightweight and its only main dependency is `typing_extensions`.)*

## Quick Start

Here is a quick example using Slyme's core primitives (`@node`, `@wrapper`, and `@builder`):

```python
from time import time
from collections.abc import Callable
from slyme.builder import builder
from slyme.context import Context, Ref, Schema
from slyme.node import node, wrapper, Auto, Node


R = Schema(
    {
        "input": {
            "articles": Schema.leaf(),
        },
        "output": {"responses": Schema.leaf()},
    }
)


# 1. Define an execution node
@node
def llm_api(ctx: Context, /, *, prompts: Auto[list[str]], responses: Ref[list[str]]):
    responses_ = [f"Response to the prompt: {prompt}" for prompt in prompts]
    ctx.set(responses, responses_)


# 2. Define a value-producing node
@node
def format_prompts(ctx: Context, /, *, articles: Auto[list[dict]]) -> list[str]:
    return [
        f"Summarize: {article['title']}. Content: {article['content']}"
        for article in articles
    ]


# 3. Define a wrapper for middleware (e.g., performance timing)
@wrapper
def timing(
    ctx: Context,
    wrapped: Node,
    call_next: Callable[[Context], object],
    /,
    *,
    prefix: str,
):
    start_time = time()
    result = call_next(ctx)
    end_time = time()
    print(f"[{prefix}] Finished successfully in {end_time - start_time:.4f} seconds.")
    return result


# 4. Assemble the pipeline at Build-Time
@builder
def build_pipeline():
    return llm_api(
        responses=R.resolve("output.responses"),
        prompts=format_prompts(
            articles=R.resolve("input.articles"),
        ),
    ).add_wrappers(timing(prefix="LLM API Call"))


# 5. Execute at Run-Time
if __name__ == "__main__":
    ctx = Context(
        {
            R.resolve("input.articles"): [
                {"title": "Article 1", "content": "Content 1"},
                {"title": "Article 2", "content": "Content 2"},
            ]
        },
        schema=R,
    )
    build_pipeline()(ctx)
    print(ctx.get(R.resolve("output.responses")))
```

## Context layers and Compose

A Context root holds a live `Schema` declaration tree, which may be built before the Context or extended reversibly through `Context.declare()`. Schema is the sole source of leaf/container structure; Context values use flat per-entry bindings with live C3 lookup. `Context.fork()` creates an empty child layer, while `Context.isolate()` can additionally block selected inherited leaves. `Compose` associates ordered values with Context identities and returns an exact disposer for every addition:

```python
from slyme.context import Compose, Context

root = Context()
agent = root.fork()
hooks = Compose[str, tuple[str, ...]].collect()

remove_root = hooks.add(root, "root")
remove_agent = hooks.add(agent, "agent")
assert hooks.resolve(agent) == ("agent", "root")

remove_agent()
remove_root()
```

## Core Advantages

**Native Python Development Experience:** Eliminates heavy object-oriented boilerplate code. You only need to master basic Python functions and native data structures (dictionaries, lists, tuples) to get started quickly.

**Unlimited Composability:** Build arbitrarily complex execution flows with complete decoupling. Thanks to PyTree augmentation, Node containment relationships can be represented directly through native Python structures.

**Explicit State Layers:** Every Context path, structural role, and replacement policy is declared by a shared Schema. `Context.fork()` creates a child with live C3 lookup and local writes, while `flatten()` exposes the visible Ref-to-value mapping. `Compose` provides ordered, reversible values across the same Context hierarchy.

**Seamless Collaboration:** Highly decoupled Nodes communicate through explicit Context paths and Compose objects. This allows teams to independently develop features and write unit tests, reducing "glue code" and deep system coupling.

## Documentation

To dive deeper into Slyme's architecture, including Context management, dependency injection, and the Node lifecycle, please refer to our official documentation site.

**👉 [Read the Official Slyme Documentation](https://slymelab.github.io/slyme/)**

## License

This project is licensed under the Apache-2.0 License.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for the locked development environment,
tests, quality gates, and pull request workflow. Please report suspected
vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
