# agent-safe-delete

[English](README.en.md)

`agent-safe-delete` 是一个面向 AI Agent 的安全删除技能：当 Agent 需要“删除”文件、目录或 symlink 时，不执行不可恢复的永久删除，而是把目标移动到可恢复的归档区，并把恢复所需 metadata 写入隐藏目录中的 JSON 文件。

## 核心思路

- 默认把删除语义改写为“可恢复归档”。
- 不只拦截用户显式提出的删除，也拦截 Agent 在执行过程中自行推断出的删除、替换、清理动作。
- 归档目录通过单一环境变量 `ASD_SAFE_ARCHIVE_ROOT` 控制。
- 未显式配置时，使用平台默认目录。
- 归档对象默认保持原名，直接进入归档根目录；同名时自动追加时间戳。
- metadata 集中放在归档根目录下的隐藏目录 `.agent-safe-delete/` 中。
- 支持 `restore` 把文件、目录或 symlink 移回原路径，或恢复到指定目标路径。
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
    agent-safe-delete.py
  tests/
    test_agent_safe_delete.py
    smoke.sh
```

## 使用方式

### 1. 作为技能使用

把整个目录放到 Agent 可发现的 skills 目录中，让技能把 `scripts/agent-safe-delete.py` 作为其附带 CLI 入口来调用。

这里要区分两种场景：

- 在仓库内开发、测试或直接试跑时，可以从仓库根目录执行 `python scripts/agent-safe-delete.py ...`。
- 在“已安装 skill”场景中，不应假设 Agent 的当前工作目录就是技能目录，也不应假设当前工作区里存在 `scripts/agent-safe-delete.py`。此时应由宿主平台或安装层解析技能的实际安装目录，或提供稳定的包装命令，再调用这个 Python 入口。

这个技能的定位不是“只有用户说 delete 才用”，而是“只要 Agent 准备删除、替换、清理文件系统对象，就先由它接管删除语义”。

### 2. 直接作为命令行工具使用

以下命令示例假定当前目录就是仓库根目录：

```bash
python scripts/agent-safe-delete.py show-archive-root
python scripts/agent-safe-delete.py archive ./example.txt
python scripts/agent-safe-delete.py archive ./build --json
python scripts/agent-safe-delete.py restore ASD-20260401-101530-8f3k2m
python scripts/agent-safe-delete.py restore ASD-20260401-101530-8f3k2m --to ./restored.txt
```

## 运行依赖

- 需要可用的 `python` 命令。
- 不再依赖 `bash` 作为唯一运行入口。
- 在 Windows PowerShell、macOS、Linux 下都使用同一条命令调用。

## 为什么会自动触发

这个项目的目标不是“提供一个用户手动调用的归档命令”，而是“给 AI Agent 一个默认安全删除语义”。

因此它不仅在这些情况下触发：

- 用户明确说“删除”
- 用户明确说“归档”

也会在这些情况下触发：

- Agent 在执行任务时推断出需要删除旧文件后重建
- Agent 需要清理废弃目录、旧模块、错误生成物或临时输出
- Agent 需要通过删除旧对象来完成替换动作

这里的“清理”不仅包括明显的废弃文件，也包括中间文件、临时文件、转换源文件、缓存文件和一次性生成产物。只要 Agent 接下来准备把这些文件从文件系统移除，就属于本技能的触发范围。

例如：生成最终 `docx` 之后清理中间 `html`，导出成功后删除临时图片，或转换成功后删除源格式文件，这些都不应该被视为“可以直接删掉的例外”。

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

- 目标路径不存在时直接失败；broken symlink 会按 symlink 自身处理，而不是当作缺失路径。
- 已位于归档目录内的路径禁止再次归档。
- 不能归档归档根目录本身，也不能归档隐藏 metadata 目录。
- 归档使用 `mv`，不是复制。
- `restore` 默认恢复到原路径；如果原路径已存在，则直接失败。
- 文件、目录和 symlink 都会记录结构化元数据，便于 Agent 和脚本消费。
- 如果归档对象被手动删除，后续命令会自动清理对应 metadata JSON。
- 即使删除目标只是为了完成最终交付物而产生的中间文件、临时文件、缓存文件或转换源文件，也不得直接使用 `rm`，仍然必须走归档。
- 删除目标不明确时先澄清；命中 `.env`、凭据、系统路径、仓库根目录或大范围批量删除时才再次确认。

## 本地验证

```bash
./tests/smoke.sh
```

这个测试会在临时目录里验证：

- 默认配置解析
- 文件归档与恢复
- 目录归档与恢复
- broken symlink 归档与恢复
- metadata JSON 生成
- 失效 metadata 自动清理

## 许可证

本项目使用 MIT License，具体内容见 `LICENSE`。

## 致谢

- [Linux Do 社区](https://linux.do/)
