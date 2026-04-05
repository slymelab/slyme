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
    responses: Ref[list[str]],
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
    prompts: Union[Ref[list[str]], Expression[list[str]], list[str]],  # [!code highlight]
    responses: Ref[list[str]],
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

::: code-group
```python [使用 scope 进行自动注入（推荐）]

```

```python [手动传入参数]

```
:::


