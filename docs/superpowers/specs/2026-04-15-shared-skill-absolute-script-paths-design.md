# 共享技能脚本绝对路径规则设计

## 背景

共享技能目录位于 `~/.config/shared-skills/`，并通过 symlink 暴露给多个 agent 工具使用。当前 `managing-shared-skills` 技能已经要求共享技能的真实源只能在该目录下维护，但还没有明确规定：当 `SKILL.md` 中给出“直接执行本技能自带脚本”的命令示例时，脚本路径应该如何书写。

这会导致一个常见问题：技能文档里如果写成 `python scripts/foo.py` 或 `./scripts/foo.sh`，Agent 往往会在“当前工作区”执行这条命令，而不是先切换到技能目录。结果就是第一次调用时先去当前仓库找 `scripts/foo.py`，找不到就报错，之后才可能退回到技能目录绝对路径进行补救。

## 目标

- 为共享技能建立统一的脚本路径书写规则。
- 避免 Agent 把共享技能中的相对脚本路径误解释为当前工作区路径。
- 让共享技能命令在任意工作目录下第一次执行就命中正确脚本。

## 非目标

- 不修改工具专属技能（非 shared-skills）的路径规则。
- 不要求所有 shell 命令都变成绝对路径；只覆盖“直接调用共享技能自带脚本”的场景。
- 不改变 `sync-shared-skills.sh` 的同步机制。

## 方案

在 `managing-shared-skills` 技能中补充一条强制规则：

- 当共享技能的 `SKILL.md` 里直接调用本技能目录下的脚本时，必须使用共享技能源目录的绝对路径。
- 禁止在共享技能文档中使用 `python scripts/foo.py`、`./scripts/foo.sh` 这类依赖当前工作区的相对路径写法。

推荐写法：

```bash
python "$HOME/.config/shared-skills/<skill-name>/scripts/<script>.py" ...
bash "$HOME/.config/shared-skills/<skill-name>/scripts/<script>.sh" ...
```

## 设计理由

### 为什么不用相对路径

- Agent 执行技能命令时，工作目录通常是当前项目仓库，而不是共享技能目录。
- 相对路径会优先解析到当前仓库，造成“第一次调用报错，第二次补救成功”的糟糕体验。
- 共享技能的核心价值是跨仓库复用，因此命令入口必须和具体工作区解耦。

### 为什么选共享技能绝对路径

- `~/.config/shared-skills/` 是共享技能唯一源，路径稳定且可预期。
- 各 agent 工具目录只是 symlink 暴露层，不应该作为命令基准路径。
- 使用 `$HOME/.config/shared-skills/...` 可以兼顾可读性和稳定性，不依赖某个工具专属目录。

## 影响范围

这条规则主要影响：

- `managing-shared-skills` 技能文档本身
- 未来所有新增或改造的共享技能 `SKILL.md`
- 已存在但仍使用相对脚本路径的共享技能

## 验收标准

- `managing-shared-skills` 技能明确写出共享技能脚本必须使用绝对路径。
- 规则中包含禁止相对路径示例和推荐绝对路径示例。
- 读者看完后能明确区分“共享技能脚本路径”和“当前工作区路径”。
