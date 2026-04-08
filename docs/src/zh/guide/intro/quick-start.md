# 快速上手

在这篇指引中，我们将通过一个简单的 LLM 模拟调用示例来串联一遍 Slyme 的核心概念。我们将接受一个文章的列表（`list[dict]`），包含文章的标题和内容，然后模拟调用 LLM 来生成文章的总结。在这个过程中，我们将对 LLM 的调用时间进行记录并打印。

## Step 1：使用 @node 实现 LLM 模拟调用节点

我们使用 @node 装饰器来装饰 `llm_api` 函数，其中 `llm_api` 的功能是接收一个 prompt 列表（batch 输入），根据 prompt 来调用 LLM 模型，最后将 LLM 的响应（batch 输出）写入到 `responses` 中。

::: code-group
```python [使用 Auto 自动注入（推荐）]
from slyme.context import Context, Ref
from slyme.node import node, Auto

@node
def llm_api(
    ctx: Context,
    /,
    *,
    prompts: Auto[list[str]],  # 这里我们通过 Auto 自动注入 prompts 的参数值  // [!code highlight]
    responses: Ref[list[str]],  # Ref 对象，类似于字典的 key，用于读取/写入特定的路径
):
    # NOTE: 这里我们模拟 LLM 对每一个 prompt 做出了响应
    responses_ = [f"Response to the prompt: {prompt}" for prompt in prompts]
    return ctx.set(responses, responses_)  # 写入并返回新的 Context 对象
```

```python [手动通过 Context 获取值]
from typing import Union
from slyme.context import Context, Ref
from slyme.node import node, Expression

@node
def llm_api(
    ctx: Context,
    /,
    *,
    prompts: Union[Ref[list[str]], Expression[list[str]], list[str]],  # 如果不使用 Auto，我们需要根据 prompts 的类型来分别处理，或者把 prompts 的类型限制为 Expression 以简化代码 // [!code highlight]
    responses: Ref[list[str]],  # Ref 对象，类似于字典的 key，用于读取/写入特定的路径
) -> Context:
    # NOTE: 这里我们手动通过 Context 来获取 prompts 的值，根据 prompts 的类型进行不同的处理
    if isinstance(prompts, Ref):  # [!code highlight]
        prompts_ = ctx.get(prompts)  # [!code highlight]
    elif isinstance(prompts, Expression):  # [!code highlight]
        prompts_ = prompts(ctx)  # [!code highlight]
    else:  # [!code highlight]
        prompts_ = prompts  # [!code highlight]
    # NOTE: 这里我们模拟 LLM 对每一个 prompt 做出了响应
    responses_ = [f"Response to the prompt: {prompt}" for prompt in prompts_]
    return ctx.set(responses, responses_)  # 写入并返回新的 Context 对象
```
:::

在实现 @node 函数的过程中，有几个注意点：

- Node 是构建期/执行期分离的，@node 对函数的签名有特定的要求，执行期参数只有一个，即 `ctx`，需要定义为 Python 函数的**仅位置参数**（即 `/` 之前）；构建期参数可根据具体的需求任意定义，但是这些参数需要定义为 Python 函数的**仅关键字参数**（即 `*` 之后）。具体说明可见 [Node 章节](/zh/guide/essentials/node)。
- 在这里，我们推荐使用强大的 `Auto` 类型注解，它可以实现自动依赖注入，将构建期用户绑定的 Ref / @expression 类型**自动运行求值**。相比于手动调用 `ctx.get` / `expression(ctx)`，`Auto` 简化了代码，并且在一些求值场景下（如异步求值）能够通过批量调用来加速执行，具有较高的性能下限。
- @node 函数的返回值需要是一个 Context 对象。

## Step 2：使用 @expression 实现调用 prompt 格式化

由于 `llm_api` 接收的是一个 prompt 列表，而我们已有的数据结构是一个 `list[dict]`，列表的每一个元素是一个文章的标题和内容信息，因此我们在这里可以通过 @expression 来进行数据的处理和转换。

```python
from slyme.context import Context, Ref
from slyme.node import expression, Auto

@expression
def format_article_prompts(
    ctx: Context,
    /,
    *,
    articles: Auto[list[dict]]  # 自动注入文章数据
) -> list[str]:
    return [
        f"Please summarize the content of the article titled: {article['title']}. The content is: {article['content']}"
        for article in articles
    ]
```

需要注意的是：

- 在这里，我们使用 `Auto` 自动注入了文章的 `list[dict]` 数据，并且对每个文章的标题和内容进行了格式化处理，生成了一个 prompt 列表。在最终构建的时候，我们将 `format_article_prompts` 绑定到 `llm_api` 的 `prompts` 参数（即 `llm_api(..., prompts=format_article_prompts(...))`，`llm_api` 的 `Auto` 注解会自动调用 `format_article_prompts` 进行求值，并且将返回的结果作为 `prompts` 参数的输入值）。值得注意的是，由于 Slyme 的高度可组合性，我们的 `llm_api` 设计得极致解耦，它接受的是通用的 prompts，因此我们可以不仅调用它来总结文章，还可以实现其他各种功能，只需要在构建的时候将 `format_article_prompts` 变成其他 @expression 或者 Ref 即可。
- @expression 的参数定义要求与 @node 相同（Context 仅位置参数 + 任意仅关键字参数）。
- @expression 的返回值不要求是一个 Context 对象，它可以是任意 Python 类型。

## Step 3：使用 @wrapper 实现性能监控

接下来，我们将基于 @wrapper 来实现一个简单的运行时间监控功能。

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
    ctx = call_next(ctx)  # 在这里我们实现了对被包裹节点的调用 // [!code highlight]
    end_time = time()
    print(f"[{prefix}] Node:\n{wrapped}\nFinished successfully in {end_time - start_time:.4f} seconds.")
    return ctx
```

@wrapper 的作用类似于中间件，它将 @node 的执行过程包裹起来，并在执行前后添加一些额外的功能。请注意：

- @wrapper 的 **执行期参数** 有 3 个，分别是 `ctx`（与 @node / @expression 相同，即 Context 对象），`wrapped`（被包裹的 @node 实例），`call_next`（回调函数，用于继续执行被包裹的下一个流程，如果不调用它，那么被包裹的流程将被跳过）。@wrapper 的 **构建期参数** 与 @node / @expression 相同，即任意仅关键字参数。
- @wrapper 的返回值需要是一个 Context 对象。

::: tip
至此，我们的核心逻辑已经实现完成，包括一个用于模拟 LLM 调用的 `llm_api`（@node）、一个用于将文章数据转换为输入 prompt 的 `format_article_prompts`（@expression）和一个用于监控节点执行时间的 `timing`（@wrapper），那么接下来，我们将这些组件组装起来并执行。
:::

## Step 4：使用 @builder 将组件实例化并组装起来

我们推荐使用 @builder 来将原子化的组件构建成具体的执行流程。它会自动进行结构校验，保证组装正确性。

::: code-group
```python [使用 scope 进行自动注入（推荐）]
from slyme.builder import builder
from slyme.context import Ref

@builder
def build_pipeline():
    scope = {  # [!code highlight]
        "articles": Ref("input.articles"),  # [!code highlight]
        "responses": Ref("output.responses"),  # [!code highlight]
    }  # [!code highlight]

    return llm_api(
        scope,  # [!code highlight]
        prompts=format_article_prompts(scope),  # [!code highlight]
    ).add_wrappers(timing(prefix="LLM API Call"))
```

```python [手动传入参数]
from slyme.builder import builder
from slyme.context import Ref

@builder
def build_pipeline():
    return llm_api(
        responses=Ref("output.responses"),  # [!code highlight]
        prompts=format_article_prompts(
            articles=Ref("input.articles"),  # [!code highlight]
        ),
    ).add_wrappers(timing(prefix="LLM API Call"))
```
:::

有几个需要注意的点：
- 组装 Node 过程我们称之为**构建期**，在这个过程中，我们需要填入**构建期参数**（即**仅关键字参数**，在 `*` 之后），这些参数完全根据具体的逻辑来定义。在这里我们使用了 Node 自带的 scope 注入来绑定参数，它的好处是相比于我们手动地传入各种 Ref 参数，scope 注入只需要用户维护一个 scope 字典，即可根据函数的参数名来自动地传入对应的同名参数，这在 Node 结构复杂、重复字段比较多的场景下非常有用，避免了用户反复写 `value=Ref("foo.bar")` 这样的参数赋值语句。scope 字典的 key 是 `str` 类型，它对应的是用户函数的参数名，我们称之为“别名”。比如说，上面的代码中，`"articles": Ref("input.articles")` 的 `"articles"` 是别名，与 `format_article_prompts` 的 `articles` 参数对应，而 `Ref("input.articles")` 则代表着填入的值，其中 `"input.articles"` 对应的是我们在 Context 对象中存储的真实路径。
- 上述组装过程中，我们将 `timing` 加入到 `llm_api` 的 wrappers 列表，那么 `timing` 会在 `llm_api` 执行的时候被调用。我们给 `llm_api` 的 `prompts` 参数赋值了 `format_article_prompts`，在 `Auto` 注解的加持下，`llm_api` 函数完全不需要知道 `format_article_prompts` 的存在，传入函数的参数是已经被 `format_article_prompts` 处理好之后返回的 prompt 列表（`list[str]`）。同理，`format_article_prompts` 的 `articles` 参数使用了 `Auto` 注解，而构建期我们给 `articles` 参数赋值了 `Ref("input.articles")`，那么执行期会自动根据路径从 Context 中取值，然后赋值给 `articles` 参数。

## Step 5：运行

最终，我们调用上述代码并执行：

```python
from slyme.context import Context, Ref

ctx = Context().update({
    # NOTE: 我们给 Context 注入了初始所需的文章数据
    Ref("input.articles"): [
        {"title": "Article 1", "content": "Content of Article 1"},
        {"title": "Article 2", "content": "Content of Article 2"},
    ],
})
pipeline = build_pipeline()
pipeline_exec = pipeline.prepare()  # 通过调用 prepare 将构建期转换为执行期  // [!code highlight]
ctx = pipeline_exec(ctx)  # 执行
print(ctx.get(Ref("output.responses")))  # 打印执行结果
```

::: details 最终的完整代码
```python
from time import time
from collections.abc import Callable
from slyme.builder import builder
from slyme.context import Context, Ref
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
    scope = {
        "articles": Ref("input.articles"),
        "responses": Ref("output.responses"),
    }

    return llm_api(
        scope,
        prompts=format_article_prompts(scope),
    ).add_wrappers(timing(prefix="LLM API Call"))


if __name__ == "__main__":
    ctx = Context().update({
    Ref("input.articles"): [
            {"title": "Article 1", "content": "Content of Article 1"},
            {"title": "Article 2", "content": "Content of Article 2"},
        ],
    })
    pipeline = build_pipeline()
    pipeline_exec = pipeline.prepare()
    ctx = pipeline_exec(ctx)
    print(ctx.get(Ref("output.responses")))
```
:::

输出结果：

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

## 下一步

现在你已经对 Slyme 的核心模块有了基本的理解，接下来：

- 为了对这些组件的行为有更深入的掌握，推荐你阅读接下来的基础章节，了解 [Context](/zh/guide/essentials/context)、[Node](/zh/guide/essentials/node)、[Builder](/zh/guide/essentials/builder) 和[生命周期](/zh/guide/essentials/lifecycle)。它们将提供组件的更详细的说明，帮助你高效地开发 Slyme 执行流程。
- 如果你想要更深入地了解 Slyme 的工作原理，推荐你阅读 [深入 Slyme](/zh/guide/slyme-in-depth/functional-programming-basics) 章节。
