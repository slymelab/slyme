# Quick Start

In this guide, we will walk through Slyme's core concepts using a simple LLM simulation example. We will accept a list of articles (`list[dict]`) containing article titles and content, then simulate calling an LLM to generate summaries. During this process, we will record and print the LLM call time.

## Installation

```bash
pip install slyme
```

The project dependency only includes `Python >= 3.9` and `typing_extensions`.

## Step 1: Implement LLM Simulation Node Using @node

We use the @node decorator to decorate the `llm_api` function, where `llm_api` accepts a list of prompts (batch input), calls the LLM model based on the prompts, and writes the LLM response (batch output) to `responses`.

::: code-group
```python [Using Auto for Automatic Injection (Recommended)]
from slyme.context import Context, Ref
from slyme.node import node, Auto

@node
def llm_api(
    ctx: Context,
    /,
    *,
    prompts: Auto[list[str]],  # Here we use Auto to automatically inject the prompts parameter value  // [!code highlight]
    responses: Ref[list[str]],  # Ref object, similar to a dictionary key, used to read/write a specific path
):
    # NOTE: Here we simulate LLM responding to each prompt
    responses_ = [f"Response to the prompt: {prompt}" for prompt in prompts]
    return ctx.set(responses, responses_)  # Write and return the new Context object
```
:::

A few notes when implementing @node functions:

- Node follows build-time/execution-time separation. @node has specific requirements for function signatures. The execution-time parameter is only one, `ctx`, which needs to be defined as a **positional-only parameter** (before `/`) in the Python function. Build-time parameters can be arbitrarily defined based on specific needs, but they need to be defined as **keyword-only parameters** (after `*`) in the Python function. See the [Node](/guide/essentials/node) chapter for details.
- Here, we recommend using the powerful `Auto` type annotation, which enables automatic dependency injection, automatically **evaluating** Ref / @expression types bound by users at build-time. Compared to manually calling `ctx.get` / `expression(ctx)`, `Auto` simplifies code and can speed up execution through batch calls in some evaluation scenarios (like async evaluation), providing a higher performance floor.
- The return value of an @node function must be a Context object.

## Step 2: Implement Prompt Formatting Using @expression

Since `llm_api` accepts a list of prompts, and our existing data structure is a `list[dict]` where each element contains article title and content information, we can use @expression for data processing and transformation here.

```python
from slyme.context import Context, Ref
from slyme.node import expression, Auto

@expression
def format_article_prompts(
    ctx: Context,
    /,
    *,
    articles: Auto[list[dict]]  # Automatically inject article data
) -> list[str]:
    return [
        f"Please summarize the content of the article titled: {article['title']}. The content is: {article['content']}"
        for article in articles
    ]
```

Note that:

- Here, we use `Auto` to automatically inject the article `list[dict]` data, and format each article's title and content to generate a prompt list. At final build time, we bind `format_article_prompts` to `llm_api`'s `prompts` parameter (i.e., `llm_api(..., prompts=format_article_prompts(...))`, and `llm_api`'s `Auto` annotation will automatically call `format_article_prompts` for evaluation, using the returned result as the `prompts` parameter input). Due to Slyme's high composability, our `llm_api` is designed to be completely decoupled — it accepts generic prompts. Therefore, we can not only call it to summarize articles, but also implement various other functions, simply by changing `format_article_prompts` to another @expression or Ref at build time.
- The parameter definition requirements for @expression are the same as @node (Context as positional-only parameter + arbitrary keyword-only parameters).
- The return value of @expression does not need to be a Context object; it can be any Python type.

## Step 3: Implement Performance Monitoring Using @wrapper

Next, we will implement a simple execution time monitoring feature based on @wrapper.

```python
from time import time
from collections.abc import Callable
from slyme.context import Context
from slyme.node import wrapper, Node

@wrapper
def timing(
    ctx: Context,
    wrapped: Node,
    call_next: Callable[[Context], Context],
    /,
    *,
    prefix: str
) -> Context:
    start_time = time()
    ctx = call_next(ctx)  # Here we implement calling the wrapped node // [!code highlight]
    end_time = time()
    print(f"[{prefix}] Node:\n{wrapped}\nFinished successfully in {end_time - start_time:.4f} seconds.")
    return ctx
```

@wrapper functions like middleware, wrapping the @node execution process and adding extra functionality before and after execution. Note that:

- @wrapper's **execution-time parameters** include 3: `ctx` (same as @node/@expression, the Context object), `wrapped` (the wrapped @node instance), and `call_next` (callback function for continuing to execute the wrapped next flow; if not called, the wrapped flow will be skipped). @wrapper's **build-time parameters** are the same as @node/@expression, i.e., arbitrary keyword-only parameters.
- The return value of @wrapper must be a Context object.

::: tip
So far, our core logic is complete, including `llm_api` (@node) for simulating LLM calls, `format_article_prompts` (@expression) for converting article data to input prompts, and `timing` (@wrapper) for monitoring node execution time. Next, we will assemble these components and execute them.
:::

## Step 4: Use @builder to Instantiate and Assemble Components

We recommend using @builder to build atomic components into specific execution flows. It automatically performs structural validation to ensure correct assembly.

```python
from slyme.builder import builder
from slyme.context import R

@builder
def build_pipeline():
    return llm_api(
        responses=R.output.responses,  # [!code highlight]
        prompts=format_article_prompts(
            articles=R.input.articles,  # [!code highlight]
        ),
    ).add_wrappers(timing(prefix="LLM API Call"))
```

A few notes:

- The process of assembling Nodes is called **build-time**. During this, we need to fill in **build-time parameters** (i.e., **keyword-only parameters**, after `*`), which are entirely defined based on specific logic. We use [`R` (RefFactory)](/guide/essentials/context#reffactory) to create `Ref` objects with concise dot-notation — `R.input.articles` is equivalent to `Ref("input.articles")` but more readable and type-safe. Simply pass `R`-based refs directly as keyword arguments to Node constructors.
- In the assembly process above, we added `timing` to `llm_api`'s wrappers list, so `timing` will be called when `llm_api` executes. We assigned `format_article_prompts` to `llm_api`'s `prompts` parameter. With the `Auto` annotation, `llm_api` function doesn't need to know about `format_article_prompts` at all — the parameter passed to the function is already the processed prompt list (`list[str]`) returned by `format_article_prompts`. Similarly, `format_article_prompts`'s `articles` parameter uses the `Auto` annotation, and at build-time we assign `R.input.articles` to the `articles` parameter. At execution-time, it will automatically retrieve the value from Context based on the path and assign it to the `articles` parameter.

## Step 5: Run

Finally, we call the above code and execute:

```python
from slyme.context import Context, R

ctx = Context().update({
    # NOTE: We inject the initial article data into Context
    R.input.articles: [
        {"title": "Article 1", "content": "Content of Article 1"},
        {"title": "Article 2", "content": "Content of Article 2"},
    ],
})
pipeline = build_pipeline()
pipeline_exec = pipeline.prepare()  # Convert build-time to execution-time by calling prepare  // [!code highlight]
ctx = pipeline_exec(ctx)  # Execute
print(ctx.get(R.output.responses))  # Print execution result
```

::: details Complete Code
```python
from time import time
from collections.abc import Callable
from slyme.builder import builder
from slyme.context import Context, R
from slyme.node import node, expression, wrapper, Auto, Node


@node
def llm_api(
    ctx: Context,
    /,
    *,
    prompts: Auto[list[str]],
    responses: Ref[list[str]],
):
    responses_ = [f"Response to the prompt: {prompt}" for prompt in prompts]
    return ctx.set(responses, responses_)


@expression
def format_article_prompts(
    ctx: Context,
    /,
    *,
    articles: Auto[list[dict]]
) -> list[str]:
    return [
        f"Please summarize the content of the article titled: {article['title']}. The content is: {article['content']}"
        for article in articles
    ]


@wrapper
def timing(
    ctx: Context,
    wrapped: Node,
    call_next: Callable[[Context], Context],
    /,
    *,
    prefix: str
) -> Context:
    start_time = time()
    ctx = call_next(ctx)
    end_time = time()
    print(f"[{prefix}] Node:\n{wrapped}\nFinished successfully in {end_time - start_time:.4f} seconds.")
    return ctx


@builder
def build_pipeline():
    return llm_api(
        responses=R.output.responses,
        prompts=format_article_prompts(
            articles=R.input.articles,
        ),
    ).add_wrappers(timing(prefix="LLM API Call"))


if __name__ == "__main__":
    ctx = Context().update({
        R.input.articles: [
            {"title": "Article 1", "content": "Content of Article 1"},
            {"title": "Article 2", "content": "Content of Article 2"},
        ],
    })
    pipeline = build_pipeline()
    pipeline_exec = pipeline.prepare()
    ctx = pipeline_exec(ctx)
    print(ctx.get(R.output.responses))
```
:::

Output:

```text
[LLM API Call] Node:
llm_api<NodeExec>
│ => @wrappers
├── .wrappers tuple
│   └── [0] timing<WrapperExec>
│ => $expressions
└── ['prompts'] format_article_prompts<ExpressionExec>
Finished successfully in 0.0000 seconds.
['Response to the prompt: Please summarize the content of the article titled: Article 1. The content is: Content of Article 1', 'Response to the prompt: Please summarize the content of the article titled: Article 2. The content is: Content of Article 2']
```

## Next Steps

Now that you have a basic understanding of Slyme's core modules, next:

- For a deeper grasp of these components, we recommend reading the subsequent Essentials chapters covering [Context](/guide/essentials/context), [Node](/guide/essentials/node), [Builder](/guide/essentials/builder), and [Lifecycle](/guide/essentials/lifecycle). They provide more detailed explanations to help you efficiently develop Slyme execution flows.
- If you want to understand Slyme's working principles more deeply, we recommend reading the [Slyme In Depth](/guide/slyme-in-depth/functional-programming-basics) chapter.
