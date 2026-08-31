# 参与贡献

欢迎来到 Slyme！🎉 我们非常高兴你能考虑为这个项目做出贡献。无论是修复 bug、完善文档，还是提出新的功能构想，你的每一次参与都能让 Slyme 变得更好。

## 提交 Pull Request

我们非常期待收到你的 Pull Request (PR)。在准备提交代码时，请留意以下几个简单的开发规范：

* **测试**：每项行为变更都需要有针对性的测试。测试套件使用 pytest、
  pytest-asyncio、Hypothesis 和分支覆盖率，最低覆盖率门禁为 90%。
* **质量门禁**：Ruff 格式与 lint、mypy、Python 3.10–3.14 兼容性矩阵、
  构建产物检查和文档构建必须全部通过。
* **本地 hooks**：运行 `uv run pre-commit install --install-hooks`
  安装提交时检查和推送前测试。

创建锁定的开发环境，并运行与 CI 相同的门禁：

```bash
uv sync --locked --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

发布、文档和安全流程请参阅仓库根目录的
[CONTRIBUTING.md](https://github.com/slymelab/slyme/blob/main/CONTRIBUTING.md)。

## 关于功能扩展与接口设计

作为整个生态系统的核心基石，Slyme 的稳定性直接影响着所有依赖它的开发者。

正因如此，对于新功能的引入或核心 API 的调整，我们都会采取一种严谨但绝对开放的态度。但这**绝不是**为了将大家的好想法拒之门外！相反，我们非常渴望听到你的创新想法。

我们希望在大家投入大量精力编写代码之前，能够与你以及整个社区一起充分交流。通过前期的集思广益，我们可以在确保**后向兼容性**和**系统稳定性**的前提下，共同打磨出更优秀的开发体验。

::: tip 一个小建议
如果你有一个关于新功能或接口设计的绝妙主意，强烈建议在动手敲代码之前，先开启一个 Issue 与我们聊聊你的构思。我们非常期待与你一起探讨最佳的实现方案！
:::

再次感谢你对 Slyme 的关注与支持，我们期待着你的贡献！
