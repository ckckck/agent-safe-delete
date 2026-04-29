# 远端安全归档实施报告

## 摘要

本轮在 `agent-safe-delete` 技能仓库中完成了远端服务器安全归档能力，实现位置为 `scripts/remote-safe-delete.py`。本机安全归档仍由 `scripts/agent-safe-delete.py` 负责；远端文件系统归档由新脚本独立负责。

最终方案保持通用技能解耦：安全归档技能不引用任何项目专属服务器管理技能，也不内置真实服务器、SSH 用户、项目目录、域名或凭据。远端服务器、项目目录、环境名和归档根都由调用时显式提供或通过通用环境变量提供。

## 初始设计方案

上一轮设计的核心目标是：把本地“删除即归档”的安全语义扩展到远端服务器，覆盖 `rsync --delete`、远端 `rm`、远端覆盖式同步，以及显式删除服务器上不在本地代码库中的文件或目录。

初始方案确定了以下架构：

1. 保持 `scripts/agent-safe-delete.py` 作为本地归档工具，不把 `ssh:` 路径塞进本地归档逻辑。
2. 新增 `scripts/remote-safe-delete.py` 作为远端归档工具。
3. 提供三类远端命令：`plan-rsync-delete`、`archive-list`、`archive-path`。
4. 对 `rsync --delete` 先 dry-run，解析 `*deleting` 清单，生成可审计的 JSON plan。
5. 对 plan 中的远端目标逐项归档，归档后再允许真正执行同步或清理。
6. 支持显式归档单个远端绝对路径，用于服务器上临时脚本、旧静态页、旧配置备份、旧 release 等不一定存在于本地代码库中的对象。
7. 远端执行不要求预先把脚本部署到服务器；本机通过 `ssh <ssh-target> python3 -c <bootstrap>` 临时传入远端执行逻辑。
8. 路径安全必须本地预检和远端执行端双重校验。
9. 测试只使用字符串校验和临时 fake remote root，不对真实 `/`、`/tmp`、`/var`、`/home`、`/root` 等系统目录执行移动测试。

初始设计还要求区分 `test` 与 `prod`：测试环境在清单明确、风险门禁通过后可快速执行；生产环境必须有稳定源码引用、plan hash 确认和更严格的高风险路径确认。

## 方案变更

### 远端归档根从项目事实源改为通用环境变量

规划阶段曾考虑把不同环境的归档根写入项目级环境事实源，例如测试环境和生产环境分别使用不同目录。后来确认这会让通用安全归档技能和具体项目的服务器管理技能产生耦合。

最终改为通用机制：

1. 命令行参数 `--remote-archive-root` 仍然支持，并且优先级最高。
2. 未传 `--remote-archive-root` 时，读取通用环境变量 `ASD_REMOTE_ARCHIVE_ROOT`。
3. 两者都不存在时，命令直接失败，不猜测项目或服务器专属目录。
4. 推荐通用值为 `~/.agent-safe-delete`。
5. SSH 模式会把 `~/.agent-safe-delete` 原样传给远端，由目标服务器按当前 SSH 用户解析到各自 home 下。

这个变更使安全归档技能不需要知道“测试服务器是哪台”“正式服务器是哪台”“项目目录在哪里”。项目专属环境管理技能可以继续负责 SSH 目标和项目目录，但归档根由通用参数或通用环境变量控制。

### 安装态技能文档保持绝对入口

共享技能安装态中，`SKILL.md` 里的脚本入口不能写成相对当前工作区的 `scripts/...`。因此仓库态文档保留适合开发测试的相对命令，安装态文档转换为：

```bash
python "$HOME/.config/shared-skills/agent-safe-delete/scripts/agent-safe-delete.py" ...
python "$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py" ...
```

这样可以避免 Agent 在其他项目工作区中把 `scripts/...` 错误解析为当前项目路径。

### 批次结构以 manifest 为准

初始设计草案中的远端批次结构曾包含 `plan.json`。最终实现中，plan 由 `plan-rsync-delete --output <plan.json>` 单独产出和传入，归档批次内写入 `manifest.json`、`verify-before.txt`、`verify-after.txt`、`restore.sh` 和 `payload/`。对于 plan-based 归档，`manifest.json` 记录 `plan_sha256`，用于把批次和执行计划关联起来。

## 最终实现结果

### 新增远端归档 CLI

新增文件：`scripts/remote-safe-delete.py`。

支持命令：

1. `plan-rsync-delete`：消费已有 dry-run 输出，或直接执行 `rsync --dry-run --delete --itemize-changes`，生成删除计划。
2. `archive-list`：读取计划文件，把计划中将被删除的远端对象归档到一个批次。
3. `archive-path`：显式归档单个远端绝对路径。

远端真实执行采用 SSH bootstrap：本机脚本通过 SSH 调用远端 `python3 -c <bootstrap>`，并通过 stdin 传入 JSON request。服务器上不需要预先放置 `remote-safe-delete.py`。

### 远端归档根控制

最终控制规则：

```text
--remote-archive-root > ASD_REMOTE_ARCHIVE_ROOT > 失败
```

推荐用法：

```bash
export ASD_REMOTE_ARCHIVE_ROOT="~/.agent-safe-delete"
```

如果需要对单次命令临时覆盖，可显式传入：

```bash
--remote-archive-root <remote-archive-root>
```

SSH 模式下，`~/.agent-safe-delete` 不在本机展开，而是由远端当前 SSH 用户展开。这样不同服务器和不同登录用户会自然使用各自 home 下的归档目录。

### 路径安全能力

已实现的拒绝规则包括：

1. 空路径。
2. `/`、`.`、`..`。
3. 包含 `..` path segment 的路径。
4. glob 风格路径，例如包含 `*`、`?`、`[`、`]`。
5. `rsync --delete` dry-run 清单中的绝对路径。
6. 远端归档根本身。
7. 远端归档根内部路径。
8. 不安全的 home-relative 归档根，例如 `~`、`~/../escape`、`~/*`。

显式远端绝对路径仍允许，但必须通过独立校验。所有会移动文件的路径在本机预检一次，SSH bootstrap 内再校验一次。

### 风险分级与确认门禁

远端归档按路径风险分为低、中、高三类。

高风险对象包括：

1. `.env`、`.env.production`、`.env.prod`、`.env.test`。
2. 密钥、证书、数据库文件。
3. 上传目录、媒体目录、Docker volume 等运行期数据。
4. 项目根目录等大范围路径。

高风险路径必须通过精确路径确认：

```bash
--confirm-high-risk <remote-absolute-path>
```

不支持 `all` 或批量确认代替逐项确认。

### 测试与生产环境门禁

`test` 环境：

1. 清单明确且路径安全检查通过后可归档低风险对象。
2. 高风险对象仍必须逐项精确确认。

`prod` 环境：

1. `plan-rsync-delete` 必须提供 `--source-git-ref <commit-or-tag>`。
2. `archive-list` 必须提供 `--confirm-plan <plan_sha256>`。
3. 高风险对象必须同时满足 plan hash 确认和逐路径确认。
4. 显式路径生产归档也必须提供 `source_git_ref`。

### 文档与共享技能同步

已更新仓库文档：

1. `SKILL.md`
2. `README.md`
3. `README.en.md`

文档中明确：

1. 本地归档由 `ASD_SAFE_ARCHIVE_ROOT` 控制。
2. 远端归档由 `--remote-archive-root` 或 `ASD_REMOTE_ARCHIVE_ROOT` 控制。
3. 安全归档技能不依赖项目专属服务器管理技能。
4. 远端归档示例只使用占位符，不写真实服务器、SSH 用户、项目路径、域名或凭据。

已将实现同步到共享技能安装源：`$HOME/.config/shared-skills/agent-safe-delete/`。各 Agent 的 `agent-safe-delete` 技能入口仍保持 symlink 指向共享技能目录，没有改成副本。

## 当前文件变更范围

核心实现与测试：

1. 新增 `scripts/remote-safe-delete.py`。
2. 新增 `tests/test_remote_safe_delete.py`。
3. 修改 `SKILL.md`。
4. 修改 `README.md`。
5. 修改 `README.en.md`。
6. 新增设计文档 `docs/superpowers/specs/2026-04-28-remote-safe-delete-design.md`。
7. 新增实施计划 `docs/superpowers/plans/2026-04-28-remote-safe-delete.md`。
8. 新增本报告 `docs/2026-04-29-remote-safe-delete-implementation-report.md`。

未纳入提交的无关文件：

1. `.DS_Store`
2. `docs/superpowers/.DS_Store`

## 验证结果

已完成以下验证：

1. `pytest -q`：`32 passed`。
2. `python scripts/agent-safe-delete.py --help`：通过。
3. `python scripts/remote-safe-delete.py --help`：通过。
4. `./tests/smoke.sh`：输出 `smoke test passed`。
5. 共享技能安装态 `agent-safe-delete.py --help`：通过。
6. 共享技能安装态 `remote-safe-delete.py --help`：通过。
7. 仓库态 `ASD_REMOTE_ARCHIVE_ROOT='~/.agent-safe-delete'` 可生成远端删除计划。
8. 安装态 `ASD_REMOTE_ARCHIVE_ROOT='~/.agent-safe-delete'` 可生成远端删除计划。
9. 显式 `--remote-archive-root /explicit-archive` 可以覆盖环境变量。
10. 仓库态与共享技能态的 `scripts/remote-safe-delete.py` 一致。
11. 仓库态与共享技能态的 `scripts/agent-safe-delete.py` 一致。
12. 共享技能安装态 `SKILL.md` 与仓库态内容一致，只保留脚本入口路径转换差异。
13. `git diff --check` 通过。
14. 敏感信息扫描未发现真实服务器地址、真实域名或凭据；命中项为计划文档中的扫描命令文本等假阳性。

## 后续注意事项

1. 当前分支为 `feature/remote-safe-delete`，尚未提交和合并。
2. 提交时不要包含 `.DS_Store` 或 `docs/superpowers/.DS_Store`。
3. 如果后续在具体项目中使用远端归档，项目环境技能只负责确认 SSH 目标、SSH 用户、项目目录和运行环境；不要把归档根重新写死到项目技能里。
4. 推荐在远端操作前显式设置：`ASD_REMOTE_ARCHIVE_ROOT='~/.agent-safe-delete'`。
5. 生产环境归档必须继续遵守 `--source-git-ref`、`--confirm-plan` 和高风险路径逐项确认门禁。

## 后续设计调整：统一严格可恢复模式

后续确认远端安全归档工具层不再按环境保留两套松紧不同的门禁。`test` 和 `prod` 只保留为环境身份、归档目录分组和审计字段。所有远端归档执行统一要求先生成带 `plan_sha256` 的计划，并在执行时显式提供 `--confirm-plan <plan_sha256>`；高风险路径仍必须逐项精确确认。正式环境额外的稳定版本、回退预案、发布窗口和人工确认属于项目环境治理流程，不再作为通用安全归档工具内部逻辑。

显式远端路径归档也已收敛为 plan-based 流程：先使用 `plan-path` 生成计划，再通过 `archive-list --confirm-plan <plan_sha256>` 执行。原 `archive-path` 仅保留为兼容入口，直接执行会失败并提示改用 `plan-path` + `archive-list`。

因此本报告前文关于 `prod` 强制 `--source-git-ref`、测试环境可更快执行、以及 `archive-path` 直接归档的描述均为历史行为记录；当前工具层以本节说明和最新 README/SKILL.md 为准。
