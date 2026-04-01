# agent-safe-delete

[English](README.en.md)

> 通过把删除改写为可恢复归档，保护用户免受 AI Agent 破坏性文件操作的影响。

`agent-safe-delete` 是一个面向 AI Agent 的安全删除技能：当 Agent 需要“删除”文件或目录时，不执行不可恢复的永久删除，而是把目标移动到可恢复的归档区，并把恢复所需 metadata 写入隐藏目录中的 JSON 文件。

## 核心思路

- 默认把删除语义改写为“可恢复归档”。
- 不只拦截用户显式提出的删除，也拦截 Agent 在执行过程中自行推断出的删除、替换、清理动作。
- 归档目录通过单一环境变量 `ASD_SAFE_ARCHIVE_ROOT` 控制。
- 未显式配置时，使用平台默认目录。
- 归档对象默认保持原名，直接进入归档根目录；同名时自动追加时间戳。
- metadata 集中放在归档根目录下的隐藏目录 `.agent-safe-delete/` 中。
- 支持 `restore` 把文件或目录移回原路径，或恢复到指定目标路径。
- 每次执行命令前会自动清理已失效的 metadata，避免长期膨胀。
- 明确的普通文件或目录删除请求会直接归档；只有高风险删除才需要再次确认。

## 默认归档目录

- macOS: `~/Library/Application Support/agent-safe-delete/safe-archive`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/agent-safe-delete/safe-archive`
- Windows: `%LOCALAPPDATA%\\agent-safe-delete\\safe-archive`

可以通过环境变量覆盖：

```bash
export ASD_SAFE_ARCHIVE_ROOT="$HOME/Library/Application Support/agent-safe-delete/safe-archive"
```

## 仓库结构

```text
agent-safe-delete/
  .gitignore
  LICENSE
  README.md
  README.en.md
  SKILL.md
  scripts/
    agent-safe-delete.sh
  tests/
    smoke.sh
```

## 使用方式

### 1. 作为技能使用

把整个目录放到 Agent 可发现的 skills 目录中，并让技能调用 `scripts/agent-safe-delete.sh`。

这个技能的定位不是“只有用户说 delete 才用”，而是“只要 Agent 准备删除、替换、清理文件系统对象，就先由它接管删除语义”。

### 2. 直接作为命令行工具使用

```bash
./scripts/agent-safe-delete.sh show-archive-root
./scripts/agent-safe-delete.sh archive ./example.txt
./scripts/agent-safe-delete.sh archive ./build --json
./scripts/agent-safe-delete.sh restore ASD-20260401-101530-8f3k2m
./scripts/agent-safe-delete.sh restore ASD-20260401-101530-8f3k2m --to ./restored.txt
```

## 为什么会自动触发

这个项目的目标不是“提供一个用户手动调用的归档命令”，而是“给 AI Agent 一个默认安全删除语义”。

因此它不仅在这些情况下触发：

- 用户明确说“删除”
- 用户明确说“归档”

也会在这些情况下触发：

- Agent 在执行任务时推断出需要删除旧文件后重建
- Agent 需要清理废弃目录、旧模块、错误生成物或临时输出
- Agent 需要通过删除旧对象来完成替换动作

也就是说，只要 Agent 准备对文件系统对象执行删除、替换、清理，本技能就应该优先接管删除语义，把永久删除改写为可恢复归档。

## 归档结构

归档对象会直接进入归档根目录，metadata 放在隐藏目录中：

```text
<safe-archive-root>/
  LiteBanana/
  README.md
  README-20260401-101530.md
  .agent-safe-delete/
    ASD-20260401-101530-8f3k2m.json
```

metadata JSON 示例：

```json
{
  "schema_version": 2,
  "id": "ASD-20260401-101530-8f3k2m",
  "archived_at": "2026-04-01T10:15:30Z",
  "original_path": "/path/to/project/example.txt",
  "archived_path": "/path/to/safe-archive/example.txt",
  "archived_name": "example.txt",
  "kind": "file",
  "safe_archive_root": "/path/to/safe-archive",
  "restore_status": "archived"
}
```

## 行为约束

- 目标路径不存在时直接失败。
- 已位于归档目录内的路径禁止再次归档。
- 不能归档归档根目录本身，也不能归档隐藏 metadata 目录。
- 归档使用 `mv`，不是复制。
- `restore` 默认恢复到原路径；如果原路径已存在，则直接失败。
- 目录和文件都会记录结构化元数据，便于 Agent 和脚本消费。
- 如果归档对象被手动删除，后续命令会自动清理对应 metadata JSON。
- 删除目标不明确时先澄清；命中 `.env`、凭据、系统路径、仓库根目录或大范围批量删除时才再次确认。

## 本地验证

```bash
./tests/smoke.sh
```

这个测试会在临时目录里验证：

- 默认配置解析
- 文件归档与恢复
- 目录归档与恢复
- metadata JSON 生成
- 失效 metadata 自动清理

## 后续可扩展方向

- `list` / `inspect` 子命令
- `restore --force`
- 更丰富的 JSON 输出
