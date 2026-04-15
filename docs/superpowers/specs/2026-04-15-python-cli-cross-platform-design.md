# agent-safe-delete Python 单入口跨平台化设计

## 背景

当前仓库的唯一实现入口是 legacy shell launcher，它依赖 `bash`、`mv`、`uname`、`date` 等 POSIX 命令。虽然旧实现的默认归档目录推导考虑了 Windows 的 `LOCALAPPDATA`，但实现分支只覆盖 `MINGW`、`MSYS`、`CYGWIN` 这类类 Unix 运行时，而不是原生 Windows PowerShell。

该项目未来将给团队通过 Agent 技能使用，而不是让人手动在终端执行。对这种使用方式来说，最重要的是“技能入口在不同 shell 与操作系统下都能稳定执行”，而不是保留某个历史脚本名。

## 目标

- 提供一个单一、跨平台、可被 Agent 直接调用的 CLI 入口。
- 让 Windows PowerShell、macOS、Linux 都能通过同一条命令执行归档与恢复逻辑。
- 保持现有三类子命令和主要行为不变：`show-archive-root`、`archive`、`restore`。
- 保持 `--json` 输出结构尽量稳定，避免影响后续 Agent 消费。
- 把平台相关差异收敛到 Python 标准库中，而不是分散在多套 shell 实现里。

## 非目标

- 不保留 legacy shell launcher 作为兼容入口。
- 不新增 PowerShell 独立业务实现。
- 不引入 Python 第三方依赖或打包体系。
- 不借本次重构修改技能触发规则、确认策略或 metadata schema。

## 方案对比

### 方案 A：Python 核心 + `sh`/`ps1` 双包装层

优点：

- 对手动命令行用户更自然。
- 兼容旧的 `.sh` 调用方式。

缺点：

- 实际入口变成 3 个，文档和技能说明更难保持一致。
- 对当前“只由 Agent 调用”的目标没有明显收益。
- 即便包装层很薄，也会把“真正入口是什么”变得不够直观。

### 方案 B：Python 单入口

命令形式：

```bash
python scripts/agent-safe-delete.py show-archive-root [--json]
python scripts/agent-safe-delete.py archive <path> [--json]
python scripts/agent-safe-delete.py restore <entry-id> [--to <path>] [--json]
```

优点：

- 唯一真入口，技能、文档、测试都围绕同一个命令。
- PowerShell、bash、zsh 都能直接执行相同命令。
- 平台差异收敛到 Python 标准库，后续维护最简单。

缺点：

- 这是一次受控的 breaking change，需要同步更新 README、SKILL 和测试。
- 运行环境必须提供 `python` 或等价 Python 命令。

### 结论

采用方案 B。当前项目只有作者本人使用，尚未形成团队级历史入口依赖，因此现在做入口收敛的成本最低。未来团队通过 Agent 技能调用时，也更适合直接依赖单一 Python CLI，而不是面向人类终端体验设计包装层。

## 命令契约

新的唯一入口为：

```bash
python scripts/agent-safe-delete.py <subcommand> [args]
```

支持的子命令保持不变：

- `show-archive-root`
- `archive`
- `restore`

参数契约保持不变：

- `show-archive-root [--json]`
- `archive <path> [--json]`
- `restore <entry-id> [--to <path>] [--json]`

兼容边界：

- 兼容命令语义。
- 兼容主要 JSON 输出字段。
- 不兼容“必须通过 `.sh` 文件启动”的旧入口。

## 实现设计

### 文件结构

```text
agent-safe-delete/
  scripts/
    agent-safe-delete.py
  tests/
    smoke.sh
    test_agent_safe_delete.py
```

其中：

- `scripts/agent-safe-delete.py` 是唯一业务实现与 CLI 分发入口。
- `tests/test_agent_safe_delete.py` 负责验证核心行为。
- `tests/smoke.sh` 继续保留为黑盒冒烟测试，但调用目标改为 Python CLI。

### Python CLI 内部职责

实现保持单文件、少抽象，按以下职责组织：

- 默认归档目录推导
- 路径存在判断（包含 broken symlink）
- 绝对路径规范化
- entry id 生成
- metadata JSON 写入、读取、更新
- stale metadata 清理
- `archive` 执行
- `restore` 执行
- CLI 参数解析与命令分发

### 平台兼容策略

- 默认归档目录沿用当前规则：
  - macOS: `~/Library/Application Support/agent-safe-delete/safe-archive`
  - Linux: `${XDG_DATA_HOME:-~/.local/share}/agent-safe-delete/safe-archive`
  - Windows: `%LOCALAPPDATA%\\agent-safe-delete\\safe-archive`
- 环境变量 `ASD_SAFE_ARCHIVE_ROOT` 继续优先。
- 文件移动使用 Python 的标准库移动语义，而不是 shell `mv`。
- 时间戳、UUID、JSON 读写、symlink 判断都使用 Python 标准库完成。

### 关键行为保持

以下行为必须与当前脚本保持一致：

- 目标不存在时直接失败，不猜测路径。
- broken symlink 按 symlink 自身处理。
- 已位于归档目录中的路径禁止再次归档。
- 不能归档归档根目录本身或 metadata 目录。
- 归档完成后源路径必须消失。
- `restore` 遇到目标路径已存在时直接失败。
- 目标重名时追加时间戳而不是覆盖。
- 每次执行命令前清理 stale metadata。

### JSON 输出约束

`archive --json` 继续输出：

- `action`
- `id`
- `original_path`
- `archived_path`
- `metadata_path`
- `safe_archive_root`
- `kind`

`restore --json` 继续输出：

- `action`
- `id`
- `restored_to`
- `metadata_path`
- `kind`

`show-archive-root --json` 继续输出：

- `safe_archive_root`

metadata 文件 schema 继续维持当前版本与字段语义，不在本次改动中升级。

## 测试策略

遵循 TDD，以“先验证 Python 单入口行为”为主。

### 核心测试

新增 `tests/test_agent_safe_delete.py`，覆盖：

- `show-archive-root` 默认/显式配置解析
- 文件归档与恢复
- 目录归档与恢复
- broken symlink 归档与恢复
- stale metadata 自动清理
- JSON 输出字段稳定性

该测试使用 Python 标准库 `tempfile`、`subprocess`、`json` 等完成，不引入第三方测试依赖。

### 黑盒冒烟测试

保留 `tests/smoke.sh`，但把 CLI 从旧的 `.sh` 脚本切换为：

```bash
python scripts/agent-safe-delete.py ...
```

它继续验证端到端行为与命令行输出契约。

## 文档变更

以下文档需要同步更新：

- `README.md`
- `README.en.md`
- `SKILL.md`

更新内容包括：

- 所有调用示例切换为 Python 单入口。
- 明确说明工具依赖 Python，而不是 `bash`。
- 对 Windows 的说明从“默认归档目录存在”升级为“原生 PowerShell 可通过相同命令执行”。

## 风险与缓解

### 风险 1：入口切换影响已有脚本引用

缓解：当前仍处于个人使用阶段，且本次会同步更新所有仓库内引用，控制 breaking change 范围。

### 风险 2：Python 可执行名在不同环境下不一致

缓解：实现与文档优先使用 `python`；验证阶段确认当前仓库运行环境可执行。若后续团队环境出现差异，再单独评估是否增加轻量 wrapper 或在技能层配置显式解释器。

### 风险 3：Python 标准库移动语义与 shell `mv` 边界差异

缓解：用回归测试覆盖文件、目录、broken symlink、冲突命名和恢复路径冲突，确保对当前目标场景行为一致。

## 实施顺序

1. 新增 Python 测试，先定义目标行为。
2. 运行测试并确认失败，证明新入口尚未实现。
3. 新增 `scripts/agent-safe-delete.py`，以最小实现让测试通过。
4. 更新 `tests/smoke.sh` 改为调用 Python CLI。
5. 更新 `README.md`、`README.en.md`、`SKILL.md`。
6. 运行测试与 smoke 验证。
7. 删除旧的 legacy shell launcher。

## 验收标准

- 在当前仓库环境中，`python scripts/agent-safe-delete.py` 三个子命令均可运行。
- 所有关键行为与当前 bash 版本保持一致。
- PowerShell 不再依赖 `bash` 即可调用相同入口。
- 仓库内文档和技能说明不再引用 `.sh` 作为主入口。
- 测试与 smoke 验证通过。
