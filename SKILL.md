---
name: agent-safe-delete
description: Use when a task involves archiving, removing, replacing, or cleaning up files or folders, including temporary files, intermediate outputs, conversion source files, or one-off generated artifacts, whether explicitly requested by the user or inferred during agent execution, and deletions should become reversible archive moves instead of permanent removal.
---

# Agent Safe Delete

## 概述

把文件、目录或 symlink 的“删除”改写为“移动到安全归档区”，避免 AI Agent 执行不可恢复的永久删除。

本技能既支持用户主动要求“归档”，也支持用户要求“删除文件/文件夹”时，用归档代替真正的删除。

当 Agent 在执行任务过程中，自行判断需要删除、替换、清理某个文件或目录时，也必须触发本技能，把删除语义改写为归档。

## 触发规则

- 用户明确要求“归档”文件或文件夹时触发。
- 用户要求删除文件或文件夹时也触发。
- Agent 在执行过程中，判断需要删除、替换、清理某个文件或目录时也触发。
- 中间文件、临时文件、转换源文件、缓存文件、一次性生成文件，只要接下来要从文件系统移除，也属于本技能触发范围。
- “这个文件”“这个文件夹”这类指代如果不够明确，先问一个最短澄清问题。
- 如果用户请求的是批量归档或批量删除，而路径列表并不明确，先澄清后再执行。
- 只覆盖文件、文件夹和 symlink 删除，不覆盖数据库记录、系统配置、Git 历史或其他非文件系统删除。

典型隐式触发场景包括：

- 删除旧文件后重建
- 清理废弃目录或重构遗留模块
- 用新文件替换旧文件时需要先删除旧对象
- 清理错误生成的输出目录或临时文件夹
- 生成最终交付文件后清理中间 `.html`、`.md`、`.txt`、图片或脚本文件
- 转换或导出成功后删除源格式文件、缓存文件或一次性产物

不要因为文件是 Agent 刚创建的、容易重建的、临时的，或只是为了完成当前任务而生成的，就把它当作可以直接删除的例外。

## 不适用场景

- 数据库记录删除
- Git 历史重写
- 系统配置清理
- 其他不属于文件系统移动的删除语义

## 配置

- 通过环境变量 `ASD_SAFE_ARCHIVE_ROOT` 指定归档根目录。
- 如果未设置，则脚本自动选择平台默认目录。
- 运行时应把这个环境变量指向你信任的归档根目录。
- 归档对象会直接进入归档根目录，metadata 则写入隐藏目录 `.agent-safe-delete/`。

查看当前生效的归档目录：

```bash
python scripts/agent-safe-delete.py show-archive-root
```

## 用法

以下命令示例假定你是在仓库根目录，或已经位于该技能自身的安装目录中执行它们。

如果某个 Agent 平台会在其他工作目录中执行技能命令，则不要假设工作区里存在 `scripts/agent-safe-delete.py`。这类已安装技能应由宿主平台或安装层解析技能的实际安装目录，或提供一个稳定的包装命令，再调用本仓库附带的 Python CLI。

查看当前生效的归档目录：

```bash
python scripts/agent-safe-delete.py show-archive-root
```

归档文件或目录：

```bash
python scripts/agent-safe-delete.py archive <path>
```

以 JSON 返回结果：

```bash
python scripts/agent-safe-delete.py archive <path> --json
```

恢复到原路径：

```bash
python scripts/agent-safe-delete.py restore <entry-id>
```

恢复到指定路径：

```bash
python scripts/agent-safe-delete.py restore <entry-id> --to <path>
```

## 执行约定

- 当用户说“删除文件/文件夹”时，不执行 `rm`，而是执行 `archive`。
- 当用户主动说“归档”时，直接执行 `archive`。
- 当 Agent 在执行过程中准备删除、替换或清理文件系统对象时，也必须优先执行 `archive`，而不是直接删除。
- 即使删除目标只是为生成最终交付物而产生的中间文件、临时文件或转换源文件，也不得直接使用 `rm`，仍然必须走 `archive`。
- `archive` 是底层动作，`safe delete` 是技能语义。
- `restore` 用于恢复已归档条目，不属于危险删除确认流程。
- 每次执行命令前会自动清理失效 metadata，避免隐藏目录长期膨胀。
- 当用户明确指定单个普通文件或普通目录时，直接执行归档，不再二次确认。
- 当删除目标不明确时，先做最短澄清，而不是做形式化确认。
- 只有命中高风险对象时，才再次确认，例如：`.env`、密钥/证书、凭据文件、系统路径、主目录关键目录、仓库根目录、或大范围批量删除。

## 保护规则

- 目标不存在则直接报错，不猜测；broken symlink 按 symlink 本身处理。
- 目标如果已经位于归档目录中，则直接失败，避免嵌套归档。
- 不能归档归档根目录本身，也不能归档隐藏 metadata 目录。
- 归档完成后源路径必须消失，因为这里是移动而不是复制。
- `restore` 如果发现目标位置已存在文件或目录，则直接失败。
- 批量归档或批量删除但路径不明确时，必须先澄清。
- 删除请求默认直接走归档；只有高风险对象才需要再次确认。
- 新实现使用隐藏目录中的 metadata JSON 记录来源路径、归档时间和恢复状态，不再使用 `归档前路径.md`。

## 输出约定

- 默认输出简洁的人类可读结果。
- 传入 `--json` 时，输出结构化 JSON，便于其他 Agent 或脚本继续处理。

## 实现入口

本仓库附带的 CLI 入口文件是 `scripts/agent-safe-delete.py`。

在仓库内开发或调试时，可以直接使用下面的相对路径命令。在已安装技能场景中，应先定位技能自身目录，再调用这个入口，而不是把 `scripts/agent-safe-delete.py` 解释为当前工作区相对路径。

执行脚本：

```bash
python scripts/agent-safe-delete.py <subcommand> [args]
```

支持的子命令：

- `show-archive-root`
- `archive`
- `restore`
