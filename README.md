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

`Auto` evaluates registered leaves and reconstructs PyTree containers on each
invocation, including containers holding only ordinary values. Ordinary leaves
and evaluator results retain their identities; non-Auto parameters pass through
unchanged. `eval_tree(ctx, tree)` exposes the same evaluation behavior directly.

## Installation

Slyme requires **Python 3.10+**. You can install it directly via pip:

```bash
pip install slyme
```

*(Note: Slyme is extremely lightweight and its only main dependency is `typing_extensions`.)*

## Quick Start

Here is a quick example using `@node`, `@wrapper`, and an ordinary function to assemble the graph:

```python
from time import time
from collections.abc import Callable
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
def build_pipeline() -> Node[None]:
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

Typed APIs rely on their annotations for argument and callback types, including synchronous versus asynchronous callbacks and `Literal` options. Use a static type checker to validate these calls. Slyme checks dynamic Schema declarations, structural conflicts, and lifecycle constraints at runtime. Assemble reusable Node graphs with ordinary Python functions.

Use `await task.acall(ctx)` and `await ctx.adispose()` when execution or cleanup may be asynchronous. These always-awaitable adapters preserve immediate synchronous work and errors, and do not schedule tasks. Purely synchronous applications can call `task(ctx)` and `ctx.dispose()` directly.

## Context lifetimes, Scope visibility, and Compose

`set` and `delete` change individual local paths. `update` and `drop` validate
the complete batch before applying changes; preflight failures leave bindings
unchanged, while failures during application do not trigger rollback.

A Context root holds a live `Schema` reference and owns an application data store and lifetime tree. Each Context has at most one parent and is bound to one immutable `Scope`. `Context.fork()` creates an owned child that shares the current Scope by default; pass `scope=ctx.scope.fork()` when the child needs its own local visibility identity. `Scope.fork()` is single-parent, while explicit `Scope(parents=(...))` construction provides C3 multiple inheritance. Reads follow the bound Scope's C3 order, and writes target the Compose-local identity bound to that Scope. `Compose.bind()` can make selected Scopes share one identity without changing visibility for any other Compose. `effect()` owns immediate or asynchronous setup and cleanup; `add()` and `declare()` own synchronous registration cleanup. `dispose()` returns `None` when finished synchronously or an awaitable for remaining cleanup. Use `await resolve(ctx.dispose())` with `resolve` from `slyme.utils.awaitable` when either is possible. A Context tree and its mutable Schema and Compose objects belong to one thread, and to one event loop during asynchronous execution; this requirement is not enforced through thread-identity checks. Worker threads or processes should receive ordinary input values and return results for mutation on the owner thread. Registrations return exact disposers for optional early removal:

```python
from slyme.context import Compose, Context, Schema

R = Schema({"hooks": Schema.leaf(replaceable=False)})
root = Context(schema=R)
hooks = Compose[str, tuple[str, ...]].collect()
root.add(R.resolve("hooks"), hooks)
agent = root.fork(scope=root.scope.fork(name="agent"))

root.effect(lambda: hooks.add(root.scope, "root"))
agent.effect(lambda: agent.get(R.resolve("hooks")).add(agent.scope, "agent"))
assert hooks.resolve(agent.scope) == ("agent", "root")

agent.dispose()
assert hooks.resolve(root.scope) == ("root",)
root.dispose()
```

Scope viewers, shared Context-binding identities, and Schema declarations track their owners directly in sets. Internal registration and cleanup methods remove empty ownership records and their data; Compose removes entries by unique token and drops empty buckets. Only operations exposed for explicit undo return disposers. A later registration can reuse the Scope or identity, but cleared data does not return.

A weak Scope-to-binding index limits viewer registration and release to the
affected Context leaves, without scanning unrelated application fields or
retaining withdrawn Schema values.

## Core Advantages

**Native Python Development Experience:** Eliminates heavy object-oriented boilerplate code. You only need to master basic Python functions and native data structures (dictionaries, lists, tuples) to get started quickly.

**Unlimited Composability:** Build arbitrarily complex execution flows with complete decoupling. Thanks to PyTree augmentation, Node containment relationships can be represented directly through native Python structures.

**Explicit Lifetime and Visibility:** Context provides single-parent lifetime ownership, while Scope provides independent C3 visibility. Every Context path, structural role, and replacement policy is declared by a shared Schema; `flatten()` exposes the visible Ref-to-value mapping, and `Compose` provides ordered, reversible values across Scope hierarchies.

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
