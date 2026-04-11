<div align="center">
  <img src="https://raw.githubusercontent.com/slymelab/slyme/main/assets/images/logo.jpg" alt="Slyme Logo" width="200" style="max-width: 50%;" />

  <p><em>SLYME Lets You Mold Everything.</em></p>

  <p>
    <a href="https://pypi.org/project/slyme/"><img src="https://img.shields.io/pypi/v/slyme.svg?label=PyPI" alt="PyPI version"></a>
    <img src="https://img.shields.io/badge/python-3.9%2B-blue" alt="Python version">
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

Slyme requires **Python 3.9+**. You can install it directly via pip:

```bash
pip install slyme
````

*(Note: Slyme is extremely lightweight and its only main dependency is `typing_extensions`.)*

## Quick Start

Here is a quick example demonstrating how to build a simple execution pipeline using Slyme's core primitives (`@node`, `@expression`, `@wrapper`, and `@builder`):

```python
from time import time
from collections.abc import Callable
from slyme.builder import builder
from slyme.context import Context, Ref
from slyme.node import node, expression, wrapper, Auto, Node


# 1. Define an execution node
@node
def llm_api(ctx: Context, /, *, prompts: Auto[list[str]], responses: Ref[list[str]]):
    responses_ = [f"Response to the prompt: {prompt}" for prompt in prompts]
    return ctx.set(responses, responses_)


# 2. Define an expression for data transformation
@expression
def format_prompts(ctx: Context, /, *, articles: Auto[list[dict]]) -> list[str]:
    return [
        f"Summarize: {article['title']}. Content: {article['content']}" 
        for article in articles
    ]


# 3. Define a wrapper for middleware (e.g., performance timing)
@wrapper
def timing(ctx: Context, wrapped: Node, call_next: Callable[[Context], Context], /, *, prefix: str) -> Context:
    start_time = time()
    ctx = call_next(ctx)
    end_time = time()
    print(f"[{prefix}] Finished successfully in {end_time - start_time:.4f} seconds.")
    return ctx


# 4. Assemble the pipeline at Build-Time
@builder
def build_pipeline():
    scope = {
        "articles": Ref("input.articles"),
        "responses": Ref("output.responses"),
    }
    return llm_api(
        scope,
        prompts=format_prompts(scope),
    ).add_wrappers(timing(prefix="LLM API Call"))


# 5. Execute at Run-Time
if __name__ == "__main__":
    # Inject initial data into Context
    ctx = Context().update({
        Ref("input.articles"): [
            {"title": "Article 1", "content": "Content 1"},
            {"title": "Article 2", "content": "Content 2"},
        ],
    })
    
    # Prepare and run
    pipeline_exec = build_pipeline().prepare()
    ctx = pipeline_exec(ctx)
    
    print(ctx.get(Ref("output.responses")))
```

## Core Advantages

**Native Python Development Experience:** Eliminates heavy object-oriented boilerplate code. You only need to master basic Python functions and native data structures (dictionaries, lists, tuples) to get started quickly.

**Unlimited Composability:** Build arbitrarily complex execution flows with complete decoupling. Thanks to PyTree augmentation, Node containment relationships can be represented directly through native Python structures.

**Functional & Concurrency Safety:** The state exchanged between execution units (`Context`) is structurally immutable, utilizing a Copy-On-Write mechanism to make state management under concurrent execution simple and safe.

**Seamless Collaboration:** Highly decoupled Nodes communicate exclusively through Context. This allows teams to independently develop features and write unit tests, reducing "glue code" and deep system coupling.

## Documentation

To dive deeper into Slyme's architecture, including Context management, Dependency Injection, and Lifecycle hooks, please refer to our official documentation site.

**👉 [Read the Official Slyme Documentation](https://slymelab.github.io/slyme/)**

## License

This project is licensed under the Apache-2.0 License.
