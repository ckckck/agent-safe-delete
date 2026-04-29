# Remote Safe Delete Strict Recoverable Mode Design

## Goal

远端安全归档工具层统一为一套严格可恢复模式。`test` 和 `prod` 只作为环境身份、归档目录分组和审计字段，不再决定计划确认、高风险确认、取证、归档和恢复逻辑的强弱。

这次设计同时修正本地 fake remote 测试边界：测试可以模拟远端绝对路径，但绝不能因为测试用例写错而移动本机真实 `/`、用户 home、仓库根目录、真实归档根或系统关键目录下的内容。

## Background

当前远端归档实现已经有 `scripts/remote-safe-delete.py`，支持：

- `plan-rsync-delete`：从 `rsync --dry-run --delete --itemize-changes` 输出生成远端删除计划。
- `archive-list`：读取计划并归档计划中的远端对象。
- `archive-path`：直接归档一个显式远端绝对路径。

现有实现里仍保留了“测试环境较宽松、正式环境更严格”的工具层分支。例如 `prod` 才强制 `source_git_ref`，`prod` 的 `archive-list` 才强制 `--confirm-plan <plan_sha256>`。这会造成测试环境通过并不代表正式环境同一行为也通过，增加两套模式测试成本。

## Decisions

### Unified Strict Gate

所有远端归档执行都必须先生成计划。计划必须包含 `plan_sha256`，执行时必须显式提供：

```bash
--confirm-plan <plan_sha256>
```

这个要求对 `test` 和 `prod` 完全一致。`--env` 仍保留，但只用于：

- 归档目录分组，例如 `<remote-archive-root>/test/...` 或 `<remote-archive-root>/prod/...`。
- `manifest.json` 的环境审计字段。
- 人类阅读和后续项目治理流程识别环境身份。

`--env` 不再决定安全归档工具内部门禁强弱。

### Optional Source Git Ref

`--source-git-ref` 保留为可选审计字段，不再由安全归档工具根据 `prod` 强制要求。

具体项目如果要求正式环境必须使用稳定 commit/tag、回退预案、发布窗口或人工确认，应由项目环境治理技能负责。例如赤兔策项目可以在正式环境部署流程里要求稳定版本和回退方案，但这些要求不进入通用 `agent-safe-delete` 工具层。

### High-Risk Confirmation

高风险路径在所有环境下都必须逐项精确确认：

```bash
--confirm-high-risk <remote-absolute-path>
```

不接受 `all`、通配符或批量确认。高风险对象包括：

- `.env`、运行环境文件、密钥、证书、凭据文件。
- 数据库文件、Docker volume、上传目录、媒体目录。
- 仓库根目录、项目根目录或其他大范围路径。

### Explicit Path Becomes Plan-Based

显式远端目标归档也应先生成计划，再执行计划。直接执行式 `archive-path` 会产生另一条绕过 `plan_sha256` 的路径，不符合统一严格模式。

设计上新增 `plan-path`：

```bash
remote-safe-delete.py plan-path \
  --remote-path <remote-absolute-path> \
  --env <test|prod> \
  --remote-project-root <remote-project-root> \
  --remote-archive-root <remote-archive-root> \
  --purpose <purpose> \
  --output <plan.json>
```

然后统一使用：

```bash
remote-safe-delete.py archive-list \
  --plan <plan.json> \
  --confirm-plan <plan_sha256>
```

`archive-path` 可以保留为兼容入口，但必须 fail closed，并提示改用 `plan-path` + `archive-list`。

### Forensics And Recovery

所有远端归档批次都必须写入：

- `manifest.json`
- `verify-before.txt`
- `verify-after.txt`
- `restore.sh`
- `payload/`

`manifest.json` 记录环境、用途、远端项目根、远端归档根、计划 hash、可选 source git ref、风险等级、原路径、归档路径、类型、大小、权限、属主、mtime、checksum 和恢复命令。敏感文件只记录元数据，不输出内容。

归档后必须验证：

- 原路径已消失。
- 归档对象存在。

验证结果应进入 manifest item，便于审计。

## Local Fake Remote Safety

本地模拟模式的目标是验证远端路径校验和映射逻辑，而不是移动本机真实路径。所有远端绝对路径必须映射到临时 fake remote root 下。

### Remote Path Rejection

无论是 rsync 删除条目还是显式远端路径，都必须拒绝：

- 空路径。
- `/`。
- `.`。
- `..`。
- 包含 `..` segment 的路径。
- glob 风格路径，例如包含 `*`、`?`、`[`、`]`。
- 归档根自身。
- 归档根内部路径。

### Fake Root Guard

`--local-remote-root` 必须是专门创建的一次性临时目录。工具必须拒绝把 fake root 设置为：

- 真实 `/`。
- 用户 home。
- 当前仓库根目录。
- 配置的本地或远端归档根目录。
- 系统关键目录，例如 `/tmp`、`/var`、`/home`、`/root` 本身。

映射后的 `resolved_path` 必须仍位于 fake root 内。如果路径解析后逃逸 fake root，立即失败，且不得移动任何对象。

### Test Boundary

危险路径测试只验证字符串校验、fake root 映射和 sentinel 未被移动。即使测试失败，也只能影响 `tmp_path` 或 `TemporaryDirectory` 创建的临时目录。

测试不得对真实 `/`、`$HOME`、仓库根、真实归档根或项目根执行移动、删除或清理。

## Shared-First Workflow

这次升级优先修改共享技能安装态，因为这是 Agent 实际加载的运行面：

```text
$HOME/.config/shared-skills/agent-safe-delete/
```

实施顺序：

1. 修改 `$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py`。
2. 修改 `$HOME/.config/shared-skills/agent-safe-delete/SKILL.md`，共享技能文档中的命令必须使用 `$HOME/.config/shared-skills/...` 绝对入口。
3. 使用 `publishable-skill.py promote agent-safe-delete --apply` 将共享安装态同步到独立仓库。
4. 在独立仓库补充测试、README 和历史报告。
5. 在独立仓库运行公开测试和检查。

不得把这次流程反过来做成 Repo-First `export`。也不得用 `rsync --delete` 或宽泛目录镜像同步共享技能和独立仓库。

## Implementation Scope

In scope:

- 统一 `archive-list` 对 `confirm_plan` 的要求。
- 移除安全归档工具层的 `prod` 专属 `source_git_ref` 强制逻辑。
- 新增显式路径计划生成入口 `plan-path`。
- 让直接 `archive-path` fail closed。
- 加强 `--local-remote-root` 真实路径护栏。
- 归档后验证原路径消失和归档对象存在。
- 修正 SSH plan 执行路径的高风险确认转发一致性。
- 更新共享技能文档、独立仓库 README 和测试。

Out of scope:

- 数据库记录删除。
- Git 分支删除或历史重写。
- 具体项目的生产发布门禁。
- 自动执行真实远端清理或真实生产演练。
- 重新设计本地 `agent-safe-delete.py` 的普通本地归档行为。

## Verification

实现完成后至少验证：

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_remote_safe_delete.py
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider
python scripts/agent-safe-delete.py --help
python scripts/remote-safe-delete.py --help
python "$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py" --help
git diff --check
```

如果 `tests/smoke.sh` 仍包含直接 `rm -rf "$tmpdir"` trap，本次计划不把它作为必跑验证；除非先单独改造该 smoke 脚本的清理方式，或获得明确许可运行既有测试脚本清理逻辑。

## Open Questions

无。上一线程已经确认核心方向：统一严格可恢复模式、本地 fake remote root 额外护栏、Shared-First 升级顺序。
