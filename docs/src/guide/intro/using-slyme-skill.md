# Using the Slyme Skill

Slyme provides an [Agent Skill](https://agentskills.io/) for coding agents that develop downstream applications with the framework. It teaches agents the recommended patterns for `@node`, `@wrapper`, `@builder`, `Context`, `Ref`, `Auto`, PyTrees, and CLI arguments.

The Skill is intended for projects that **use** Slyme. It does not instruct an agent to maintain or modify the Slyme framework itself.

## Skill location

The distributable Skill is maintained in the Slyme repository:

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

Treat the complete `slyme-developer` directory as one package. Keep its relative directory structure intact so the agent can load the annotated example referenced by `SKILL.md`.

## Install in another project

First, copy or link the Skill from a local Slyme checkout into a directory scanned by your coding agent. Replace `/path/to/slyme` with the location of this repository.

### Codex and Kimi Code

Both tools support the project-level `.agents/skills` directory:

```bash
mkdir -p .agents/skills
ln -s /path/to/slyme/skills/slyme-developer .agents/skills/slyme-developer
```

To vendor a standalone copy instead:

```bash
mkdir -p .agents/skills
cp -R /path/to/slyme/skills/slyme-developer .agents/skills/
```

Codex can invoke it explicitly as `$slyme-developer`. Kimi Code can invoke it as `/skill:slyme-developer`. Both may also select it automatically when the request matches its description.

### Claude Code

Claude Code scans `.claude/skills` at project level:

```bash
mkdir -p .claude/skills
ln -s /path/to/slyme/skills/slyme-developer .claude/skills/slyme-developer
```

Or vendor a standalone copy:

```bash
mkdir -p .claude/skills
cp -R /path/to/slyme/skills/slyme-developer .claude/skills/
```

Invoke the installed Skill as `/slyme-developer`, or let Claude load it automatically for relevant Slyme development tasks.

::: tip Sharing one installation
Keep `skills/slyme-developer` as the canonical copy when your repository distributes several Skills. Point each tool-specific discovery directory at that copy instead of maintaining duplicated Skill contents.
:::

## Example requests

After installing the Skill, give the agent a normal application-development request:

```text
Use $slyme-developer to build a Slyme pipeline that loads records,
normalizes them with an node, and writes the result to Context.
```

```text
Review this Slyme node tree. Check Auto evaluation, positional-only and
keyword-only parameters, Context updates, and wrapper composition.
```

The Skill directs the agent to inspect the target project's installed Slyme version and existing conventions before making changes.
