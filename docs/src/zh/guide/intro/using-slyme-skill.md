# 使用 Slyme Skill

Slyme 提供了一个面向 Coding Agent 的 Agent Skill，用于帮助代理在其他项目中基于 Slyme 开发应用。它包含 `@node`、`@expression`、`@wrapper`、`@builder`、`Context`、`Ref`、`Auto`、PyTree 和命令行参数等推荐用法。

该 Skill 面向的是**使用** Slyme 的下游项目，不会指导代理维护或修改 Slyme 框架本身。

## Skill 位置

可分发的 Skill 位于 Slyme 仓库中：

```text
skills/
└── slyme-developer/
    ├── SKILL.md
    ├── agents/
    │   └── openai.yaml
    └── references/
        ├── core-api.md
        └── async.md
```

请将完整的 `slyme-developer` 目录视为一个独立包，并保留其相对目录结构，以便代理读取 `SKILL.md` 引用的注释示例。

## 在其他项目中安装

首先，将本地 Slyme 仓库中的 Skill 复制或链接到 Coding Agent 会扫描的目录。请将 `/path/to/slyme` 替换为本仓库的实际路径。

### Codex 和 Kimi Code

两者都支持项目级 `.agents/skills` 目录：

```bash
mkdir -p .agents/skills
ln -s /path/to/slyme/skills/slyme-developer .agents/skills/slyme-developer
```

如果希望在项目中保存一份独立副本：

```bash
mkdir -p .agents/skills
cp -R /path/to/slyme/skills/slyme-developer .agents/skills/
```

Codex 可以通过 `$slyme-developer` 显式调用；Kimi Code 可以通过 `/skill:slyme-developer` 调用。当任务与 Skill 的描述匹配时，两者也可以自动加载它。

### Claude Code

Claude Code 会扫描项目级 `.claude/skills` 目录：

```bash
mkdir -p .claude/skills
ln -s /path/to/slyme/skills/slyme-developer .claude/skills/slyme-developer
```

也可以在项目中保存一份独立副本：

```bash
mkdir -p .claude/skills
cp -R /path/to/slyme/skills/slyme-developer .claude/skills/
```

安装后可通过 `/slyme-developer` 调用，也可以让 Claude 在相关的 Slyme 开发任务中自动加载它。

::: tip 共享同一份 Skill
如果仓库后续要分发多个 Skill，建议继续将 `skills/slyme-developer` 作为唯一的规范副本，让各工具的发现目录指向它，避免维护多份重复内容。
:::

## 请求示例

安装 Skill 后，直接向代理提出正常的应用开发需求即可：

```text
使用 $slyme-developer 构建一个 Slyme pipeline：
读取记录，通过 expression 标准化，并将结果写入 Context。
```

```text
检查这个 Slyme Node tree，重点检查 Auto 求值、positional-only 和
keyword-only 参数、Context 更新以及 wrapper 组合是否正确。
```

Skill 会要求代理在修改代码前检查目标项目安装的 Slyme 版本和已有约定。
