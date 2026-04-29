# Remote Safe Delete Strict Recoverable Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将远端安全归档统一为一套严格可恢复模式，让 `test` 和 `prod` 只作为环境身份、归档目录分组和审计字段，不再决定工具层门禁强弱。

**Architecture:** 采用 Shared-First 工作流：先升级实际被 Agent 加载的 `$HOME/.config/shared-skills/agent-safe-delete/` 安装态脚本和技能文档，再通过 `publishable-skill.py promote agent-safe-delete --apply` 同步到独立仓库。保留 `remote-safe-delete.py` 的三层结构：纯计划生成、归档计划执行、local fake remote/SSH 后端。新增显式路径计划生成入口，把所有远端归档执行收敛到 plan-based `archive-list`；统一 `confirm_plan`、`plan_sha256`、高风险路径精确确认和 fake root 防护。

**Tech Stack:** Python standard library, `argparse`, `json`, `pathlib`, `hashlib`, `shutil`, `subprocess`, `pytest`, shell-based CLI verification.

**Spec:** `docs/superpowers/specs/2026-04-29-remote-safe-delete-strict-recoverable-mode-design.md`

---

## File Structure

- Modify first: `$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py` — 统一计划门禁、添加显式路径计划生成、加强 fake remote root 防护、执行后验证、修复 SSH plan 高风险确认转发。
- Modify first: `$HOME/.config/shared-skills/agent-safe-delete/SKILL.md` — 把共享技能文档改成统一严格可恢复模式；说明 `--env` 不再决定门禁强弱；移除工具层 `prod` 专属强制 `source_git_ref` 文案。共享安装态命令必须保留 `$HOME/.config/shared-skills/...` 绝对入口。
- Promote after shared changes: `scripts/remote-safe-delete.py` and `SKILL.md` in `<repo-root>/` — 只能通过 `publishable-skill.py promote agent-safe-delete --apply` 从共享安装态同步到独立仓库，不手工反向覆盖共享态。
- Modify in repository after promote: `tests/test_remote_safe_delete.py` — 增加统一严格模式、显式路径计划、fake root 拒绝、危险路径和 SSH 高风险确认回归测试；调整旧的 `prod` 专属测试。
- Modify in repository after promote: `README.md` — 更新中文 README 的远端归档示例和行为保证。
- Modify in repository after promote: `README.en.md` — 同步英文 README 的远端归档示例和行为保证。
- Optional Modify in repository after promote: `docs/2026-04-29-remote-safe-delete-implementation-report.md` — 追加一段“统一严格模式后续变更”，只在实现者希望保留历史报告连续性时修改。
- External command only: `$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py` — 本次使用 Shared-First 的 `promote`，不要使用 Repo-First 的 `export`，也不要使用 `rsync --delete`。

## Shared-First Execution Rule

All implementation snippets in this plan that mention `scripts/remote-safe-delete.py` apply first to `$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py`. All skill-documentation snippets that mention `SKILL.md` apply first to `$HOME/.config/shared-skills/agent-safe-delete/SKILL.md` and must keep installed-skill absolute command paths.

After each shared script or shared skill-doc task is implemented and locally checked, run:

```bash
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete --apply
```

Expected: preview then apply copy only manifest-selected install files from shared install state to `<repo-root>/`, with path transforms from installed absolute commands to repository-local examples. Repository tests and README/docs are maintained in the independent repository after promote.

---

### Task 1: Unified Plan Confirmation Gates

**Files:**
- Modify first: `$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py`
- Promote target: `<repo-root>/scripts/remote-safe-delete.py`
- Modify in repository: `<repo-root>/tests/test_remote_safe_delete.py`

- [ ] **Step 1: Write failing tests for strict plan confirmation in every env**

In `tests/test_remote_safe_delete.py`, replace the old `prod`-only confirm-plan tests with env-neutral tests. Add these tests near the current `test_archive_plan_prod_requires_confirm_plan` block:

```python
def test_archive_plan_requires_confirm_plan_for_test_env(tmp_path):
    fake_root = tmp_path / "remote-root"
    stale = fake_root / "srv" / "app" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n", encoding="utf-8")
    plan = remote_safe_delete.build_rsync_delete_plan(
        dry_run_output="*deleting stale.txt\n",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="test-sync",
    )

    with pytest.raises(remote_safe_delete.UsageError, match="archive-list requires confirm_plan"):
        remote_safe_delete.archive_plan_local(local_remote_root=fake_root, plan=plan)

    assert stale.read_text(encoding="utf-8") == "stale\n"


def test_archive_plan_rejects_wrong_confirm_plan_for_test_env(tmp_path):
    fake_root = tmp_path / "remote-root"
    stale = fake_root / "srv" / "app" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n", encoding="utf-8")
    plan = remote_safe_delete.build_rsync_delete_plan(
        dry_run_output="*deleting stale.txt\n",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="test-sync",
    )

    with pytest.raises(remote_safe_delete.UsageError, match="confirm_plan does not match plan_sha256"):
        remote_safe_delete.archive_plan_local(local_remote_root=fake_root, plan=plan, confirm_plan="wrong")

    assert stale.read_text(encoding="utf-8") == "stale\n"


def test_archive_plan_accepts_confirm_plan_for_test_env(tmp_path):
    fake_root = tmp_path / "remote-root"
    stale = fake_root / "srv" / "app" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n", encoding="utf-8")
    plan = remote_safe_delete.build_rsync_delete_plan(
        dry_run_output="*deleting stale.txt\n",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="test-sync",
    )

    result = remote_safe_delete.archive_plan_local(
        local_remote_root=fake_root,
        plan=plan,
        confirm_plan=plan["plan_sha256"],
    )

    assert result["plan_sha256"] == plan["plan_sha256"]
    assert not stale.exists()


def test_prod_plan_no_longer_requires_source_git_ref(tmp_path):
    fake_root = tmp_path / "remote-root"
    stale = fake_root / "srv" / "app" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n", encoding="utf-8")
    plan = remote_safe_delete.build_rsync_delete_plan(
        dry_run_output="*deleting stale.txt\n",
        env="prod",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="prod-sync",
    )

    assert plan["source_git_ref"] is None

    result = remote_safe_delete.archive_plan_local(
        local_remote_root=fake_root,
        plan=plan,
        confirm_plan=plan["plan_sha256"],
    )

    assert result["env"] == "prod"
    assert result["source_git_ref"] is None
    assert not stale.exists()


def test_archive_plan_requires_plan_sha256_for_every_env(tmp_path):
    fake_root = tmp_path / "remote-root"
    stale = fake_root / "srv" / "app" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n", encoding="utf-8")
    plan = remote_safe_delete.build_rsync_delete_plan(
        dry_run_output="*deleting stale.txt\n",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="test-sync",
    )
    plan.pop("plan_sha256")

    with pytest.raises(remote_safe_delete.UsageError, match="plan requires plan_sha256"):
        remote_safe_delete.archive_plan_local(local_remote_root=fake_root, plan=plan, confirm_plan="anything")

    assert stale.read_text(encoding="utf-8") == "stale\n"
```

Remove or rewrite these old tests because their assumptions are intentionally obsolete:

```python
def test_prod_plan_requires_source_git_ref():
    ...

def test_archive_plan_prod_requires_confirm_plan(tmp_path):
    ...

def test_archive_plan_prod_rejects_wrong_confirm_plan(tmp_path):
    ...
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_remote_safe_delete.py
```

Expected: FAIL. Failures should show that `test` archive-list currently does not require `confirm_plan`, and `prod` plan generation still requires `source_git_ref`.

- [ ] **Step 3: Implement env-neutral plan gate logic in the shared skill script**

In `$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py`, update `build_rsync_delete_plan` to keep `source_git_ref` as optional audit metadata for every environment:

```python
def build_rsync_delete_plan(
    *,
    dry_run_output: str,
    env: str,
    remote_project_root: str,
    remote_archive_root: str,
    purpose: str,
    source_git_ref: str | None = None,
) -> dict[str, object]:
    if env not in {"test", "prod"}:
        raise UsageError("env must be test or prod")

    entries = normalize_delete_entries(parse_rsync_deletions(dry_run_output))
    items = [
        {"path": entry, "risk": classify_risk(entry, remote_project_root)}
        for entry in entries
    ]
    plan: dict[str, object] = {
        "schema_version": 1,
        "source_mode": "rsync-delete-plan",
        "created_at": current_utc_timestamp(),
        "env": env,
        "purpose": purpose,
        "remote_project_root": remote_project_root,
        "remote_archive_root": remote_archive_root,
        "source_git_ref": source_git_ref,
        "risk_level": highest_risk(items),
        "items": items,
    }
    plan["plan_sha256"] = canonical_plan_hash(plan)
    return plan
```

Replace `ensure_environment_gates` with unified confirmation logic. Keep the function name for minimal churn, but make the behavior env-neutral:

```python
def ensure_environment_gates(
    *,
    plan: dict[str, object],
    confirm_plan: str | None = None,
    confirm_high_risk: list[str] | None = None,
) -> None:
    env = str(plan.get("env", ""))
    if env not in {"test", "prod"}:
        raise UsageError("plan env must be test or prod")

    expected_hash = str(plan.get("plan_sha256", ""))
    if not expected_hash:
        raise UsageError("plan requires plan_sha256")
    if not confirm_plan:
        raise UsageError("archive-list requires confirm_plan")
    if confirm_plan != expected_hash:
        raise UsageError("confirm_plan does not match plan_sha256")

    confirmed = set(confirm_high_risk or [])
    missing = [path for path in high_risk_paths(plan) if path not in confirmed]
    if missing:
        raise UsageError(f"high-risk paths require exact confirmation: {', '.join(missing)}")
```

Remove `prod`-specific `source_git_ref` checks from these functions:

```python
def archive_explicit_path_local(...):
    ...

def archive_paths_ssh(...):
    ...
```

If those wrappers are removed or deprecated in later tasks, make sure no `env == "prod" and not source_git_ref` guard remains in the execution path.

- [ ] **Step 4: Promote shared script to the independent repository**

Run:

```bash
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete --apply
```

Expected: `scripts/remote-safe-delete.py` in `<repo-root>/` receives the shared implementation through manifest-selected copy. Do not use `export`.

- [ ] **Step 5: Run focused tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_remote_safe_delete.py
```

Expected: PASS for the unified gate tests added in this task. Other explicit-path tests may still fail after later steps intentionally change direct execution semantics.

- [ ] **Step 6: Commit Task 1 in both repositories**

Run:

```bash
git -C "$HOME/.config/shared-skills" add agent-safe-delete/scripts/remote-safe-delete.py
git -C "$HOME/.config/shared-skills" commit -m "feat: 统一远端归档确认门禁"

git add scripts/remote-safe-delete.py tests/test_remote_safe_delete.py
git commit -m "test: 覆盖远端归档统一确认门禁"
```

Expected: shared-skills commit contains the shared runtime script change; independent repo commit contains the promoted script and repository regression tests.

---

### Task 2: Explicit Path Plans Instead Of Direct Archive

**Files:**
- Modify first: `$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py`
- Promote target: `<repo-root>/scripts/remote-safe-delete.py`
- Modify in repository: `<repo-root>/tests/test_remote_safe_delete.py`

- [ ] **Step 1: Write failing tests for explicit-path plan generation**

Add these tests after `test_build_rsync_delete_plan_contains_stable_hash_and_normalized_items`:

```python
def test_build_explicit_path_plan_contains_hash_and_absolute_item():
    plan = remote_safe_delete.build_explicit_path_plan(
        remote_path="/srv/app/tmp.txt",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="manual-cleanup",
    )

    assert plan["source_mode"] == "explicit-path-plan"
    assert plan["plan_sha256"]
    assert plan["items"] == [{"path": "/srv/app/tmp.txt", "risk": "low"}]


def test_explicit_path_plan_rejects_archive_root_target():
    with pytest.raises(remote_safe_delete.PathSafetyError, match="inside archive root"):
        remote_safe_delete.build_explicit_path_plan(
            remote_path="/archive/old.txt",
            env="test",
            remote_project_root="/srv/app",
            remote_archive_root="/archive",
            purpose="manual-cleanup",
        )
```

- [ ] **Step 2: Rewrite explicit local archive success test to use plan plus confirm-plan**

Replace `test_archive_explicit_path_moves_fake_remote_file_and_writes_manifest` with:

```python
def test_archive_explicit_path_plan_moves_fake_remote_file_and_writes_manifest(tmp_path):
    fake_root = tmp_path / "remote-root"
    fake_file = fake_root / "srv" / "app" / "tmp.txt"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("temporary\n", encoding="utf-8")
    plan = remote_safe_delete.build_explicit_path_plan(
        remote_path="/srv/app/tmp.txt",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="manual-cleanup",
    )

    result = remote_safe_delete.archive_plan_local(
        local_remote_root=fake_root,
        plan=plan,
        confirm_plan=plan["plan_sha256"],
    )

    manifest_path = fake_root / result["manifest_path"].lstrip("/")
    restore_path = fake_root / result["restore_script"].lstrip("/")
    payload_path = fake_root / result["items"][0]["archived_path"].lstrip("/")

    assert result["source_mode"] == "explicit-path-plan"
    assert not fake_file.exists()
    assert payload_path.read_text(encoding="utf-8") == "temporary\n"
    assert manifest_path.is_file()
    assert restore_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["items"][0]["original_path"] == "/srv/app/tmp.txt"
    assert manifest["plan_sha256"] == plan["plan_sha256"]
```

Replace direct high-risk explicit-path execution tests with plan-based equivalents:

```python
def test_archive_explicit_high_risk_path_plan_requires_exact_confirmation(tmp_path):
    fake_root = tmp_path / "remote-root"
    env_file = fake_root / "srv" / "app" / ".env.production"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("REDACTED=value\n", encoding="utf-8")
    plan = remote_safe_delete.build_explicit_path_plan(
        remote_path="/srv/app/.env.production",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="manual-cleanup",
    )

    with pytest.raises(remote_safe_delete.UsageError, match="high-risk paths require exact confirmation"):
        remote_safe_delete.archive_plan_local(
            local_remote_root=fake_root,
            plan=plan,
            confirm_plan=plan["plan_sha256"],
        )

    assert env_file.read_text(encoding="utf-8") == "REDACTED=value\n"

    result = remote_safe_delete.archive_plan_local(
        local_remote_root=fake_root,
        plan=plan,
        confirm_plan=plan["plan_sha256"],
        confirm_high_risk=["/srv/app/.env.production"],
    )

    assert result["risk_level"] == "high"
    assert not env_file.exists()
```

- [ ] **Step 3: Add CLI test for `plan-path` and strict `archive-path` refusal**

Add these tests near the existing CLI tests:

```python
def test_cli_plan_path_writes_explicit_path_plan(tmp_path):
    plan_path = tmp_path / "plan.json"

    result = run_remote_cli(
        "plan-path",
        "--remote-path",
        "/srv/app/tmp.txt",
        "--env",
        "test",
        "--remote-project-root",
        "/srv/app",
        "--remote-archive-root",
        "/archive",
        "--purpose",
        "manual-cleanup",
        "--output",
        str(plan_path),
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["source_mode"] == "explicit-path-plan"
    assert plan["items"] == [{"path": "/srv/app/tmp.txt", "risk": "low"}]
    assert plan["plan_sha256"]


def test_cli_archive_path_refuses_direct_execution():
    result = run_remote_cli(
        "archive-path",
        "--ssh-target",
        "<ssh-target>",
        "--remote-path",
        "/srv/app/tmp.txt",
        "--env",
        "test",
        "--remote-project-root",
        "/srv/app",
        "--remote-archive-root",
        "/archive",
        "--purpose",
        "manual-cleanup",
    )

    assert result.returncode == 1
    assert "use plan-path then archive-list" in result.stderr
```

Update `test_cli_archive_path_archives_fake_remote_file` into a plan-based archive-list CLI test:

```python
def test_cli_archive_list_archives_explicit_path_plan_with_confirm_plan(tmp_path):
    fake_root = tmp_path / "remote-root"
    fake_file = fake_root / "srv" / "app" / "tmp.txt"
    plan_path = tmp_path / "plan.json"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("temporary\n", encoding="utf-8")
    plan = remote_safe_delete.build_explicit_path_plan(
        remote_path="/srv/app/tmp.txt",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="manual-cleanup",
    )
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    result = run_remote_cli(
        "archive-list",
        "--local-remote-root",
        str(fake_root),
        "--plan",
        str(plan_path),
        "--confirm-plan",
        plan["plan_sha256"],
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert not fake_file.exists()
    assert payload["source_mode"] == "explicit-path-plan"
    assert payload["items"][0]["original_path"] == "/srv/app/tmp.txt"
```

- [ ] **Step 4: Run focused tests and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_remote_safe_delete.py
```

Expected: FAIL because `build_explicit_path_plan` and `plan-path` do not exist, and `archive-path` still executes directly.

- [ ] **Step 5: Implement explicit path plan generation in the shared skill script**

Add this function in `$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py` near `build_rsync_delete_plan`:

```python
def ensure_path_outside_archive_root(remote_path: str, remote_archive_root: str) -> None:
    if remote_path == remote_archive_root or remote_path.startswith(f"{remote_archive_root}/"):
        raise PathSafetyError(f"refusing to archive path inside archive root: {remote_path!r}")


def build_explicit_path_plan(
    *,
    remote_path: str,
    env: str,
    remote_project_root: str,
    remote_archive_root: str,
    purpose: str,
    source_git_ref: str | None = None,
) -> dict[str, object]:
    if env not in {"test", "prod"}:
        raise UsageError("env must be test or prod")
    safe_project_root = validate_remote_absolute_path(remote_project_root)
    safe_archive_root = validate_archive_root(remote_archive_root)
    safe_remote_path = validate_remote_absolute_path(remote_path)
    ensure_path_outside_archive_root(safe_remote_path, safe_archive_root)
    items = [{"path": safe_remote_path, "risk": classify_risk(safe_remote_path, safe_project_root)}]
    plan: dict[str, object] = {
        "schema_version": 1,
        "source_mode": "explicit-path-plan",
        "created_at": current_utc_timestamp(),
        "env": env,
        "purpose": purpose,
        "remote_project_root": safe_project_root,
        "remote_archive_root": safe_archive_root,
        "source_git_ref": source_git_ref,
        "risk_level": highest_risk(items),
        "items": items,
    }
    plan["plan_sha256"] = canonical_plan_hash(plan)
    return plan
```

Replace duplicated archive-root checks in local and SSH archive code with `ensure_path_outside_archive_root(...)` only after both paths are validated.

- [ ] **Step 6: Preserve source_mode from the plan during execution**

Update `archive_plan_local` so it does not hardcode `rsync-delete-plan`:

```python
def archive_plan_local(
    *,
    local_remote_root: Path | str,
    plan: dict[str, object],
    confirm_plan: str | None = None,
    confirm_high_risk: list[str] | None = None,
) -> dict[str, object]:
    ensure_environment_gates(plan=plan, confirm_plan=confirm_plan, confirm_high_risk=confirm_high_risk)
    remote_paths = [plan_item_remote_path(plan, item) for item in plan.get("items", []) if isinstance(item, dict)]
    return archive_items_local(
        local_remote_root=local_remote_root,
        remote_paths=remote_paths,
        env=str(plan["env"]),
        remote_project_root=str(plan["remote_project_root"]),
        remote_archive_root=str(plan["remote_archive_root"]),
        purpose=str(plan["purpose"]),
        source_mode=str(plan.get("source_mode", "archive-plan")),
        source_git_ref=str(plan["source_git_ref"]) if plan.get("source_git_ref") else None,
        plan_sha256=str(plan.get("plan_sha256", "")),
        confirm_high_risk=confirm_high_risk,
    )
```

Update the SSH branch in `command_archive_list` the same way:

```python
result = archive_paths_ssh(
    ssh_target=args.ssh_target,
    remote_paths=[plan_item_remote_path(plan, item) for item in plan.get("items", []) if isinstance(item, dict)],
    env=str(plan["env"]),
    remote_project_root=str(plan["remote_project_root"]),
    remote_archive_root=str(plan["remote_archive_root"]),
    purpose=str(plan["purpose"]),
    source_mode=str(plan.get("source_mode", "archive-plan")),
    source_git_ref=str(plan["source_git_ref"]) if plan.get("source_git_ref") else None,
    plan_sha256=str(plan.get("plan_sha256", "")),
    confirm_high_risk=args.confirm_high_risk or [],
)
```

- [ ] **Step 7: Add the `plan-path` CLI and make `archive-path` fail closed**

Add command function:

```python
def command_plan_path(args: argparse.Namespace) -> int:
    remote_archive_root = remote_archive_root_from_args(args)
    plan = build_explicit_path_plan(
        remote_path=args.remote_path,
        env=args.env,
        remote_project_root=args.remote_project_root,
        remote_archive_root=remote_archive_root,
        purpose=args.purpose,
        source_git_ref=args.source_git_ref,
    )
    if args.output:
        Path(args.output).write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print_json(plan)
    elif not args.output:
        print("planned 1 remote archive item")
        print(f"plan_sha256: {plan['plan_sha256']}")
    return 0
```

Replace `command_archive_path` body with a fail-closed message:

```python
def command_archive_path(args: argparse.Namespace) -> int:
    raise UsageError("archive-path direct execution is disabled; use plan-path then archive-list --confirm-plan <plan_sha256>")
```

Register `plan-path` before `archive-path` in `build_parser`:

```python
plan_path_parser = subparsers.add_parser("plan-path")
plan_path_parser.add_argument("--remote-path", required=True)
plan_path_parser.add_argument("--output")
plan_path_parser.add_argument("--json", action="store_true")
add_environment_args(plan_path_parser)
plan_path_parser.set_defaults(func=command_plan_path)
```

Keep the `archive-path` subcommand for a clear compatibility error instead of silently removing it from help.

- [ ] **Step 8: Promote shared script to the independent repository**

Run:

```bash
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete --apply
```

Expected: the repository copy of `scripts/remote-safe-delete.py` now contains `plan-path` and fail-closed `archive-path`. Do not use `export`.

- [ ] **Step 9: Run focused tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_remote_safe_delete.py
```

Expected: PASS for explicit-path plan and strict archive-list tests.

- [ ] **Step 10: Commit Task 2 in both repositories**

Run:

```bash
git -C "$HOME/.config/shared-skills" add agent-safe-delete/scripts/remote-safe-delete.py
git -C "$HOME/.config/shared-skills" commit -m "feat: 将显式远端归档收敛为计划执行"

git add scripts/remote-safe-delete.py tests/test_remote_safe_delete.py
git commit -m "feat: 将显式远端归档收敛为计划执行"
```

Expected: shared-skills commit contains the shared runtime script change; independent repo commit contains the promoted script and repository regression tests.

---

### Task 3: Fake Remote Root Safety Guard

**Files:**
- Modify first: `$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py`
- Promote target: `<repo-root>/scripts/remote-safe-delete.py`
- Modify in repository: `<repo-root>/tests/test_remote_safe_delete.py`

- [ ] **Step 1: Write failing tests for local fake root rejection**

Add these tests near `test_archive_explicit_path_rejects_root_without_touching_fake_remote_root`:

```python
def test_validate_local_remote_root_rejects_real_root_home_repo_and_archive_root(tmp_path, monkeypatch):
    real_archive_root = tmp_path / "real-safe-archive"
    real_archive_root.mkdir()
    monkeypatch.setenv("ASD_SAFE_ARCHIVE_ROOT", str(real_archive_root))

    for value in [Path("/"), Path.home(), ROOT_DIR, real_archive_root]:
        with pytest.raises(remote_safe_delete.PathSafetyError):
            remote_safe_delete.validate_local_remote_root(value)


def test_validate_local_remote_root_allows_tmp_child_directory(tmp_path):
    fake_root = tmp_path / "remote-root"
    fake_root.mkdir()

    assert remote_safe_delete.validate_local_remote_root(fake_root) == fake_root.resolve()


def test_archive_plan_rejects_dangerous_fake_root_before_touching_files(tmp_path):
    fake_file = tmp_path / "srv" / "app" / "stale.txt"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("stale\n", encoding="utf-8")
    plan = remote_safe_delete.build_rsync_delete_plan(
        dry_run_output="*deleting srv/app/stale.txt\n",
        env="test",
        remote_project_root="/",
        remote_archive_root="/archive",
        purpose="unsafe-root",
    )

    with pytest.raises(remote_safe_delete.PathSafetyError):
        remote_safe_delete.archive_plan_local(
            local_remote_root=Path("/"),
            plan=plan,
            confirm_plan=plan["plan_sha256"],
        )

    assert fake_file.read_text(encoding="utf-8") == "stale\n"
```

The third test validates the guard before any move. It does not create or move real root paths; it only creates a sentinel under `tmp_path` and passes `/` as a rejected fake root.

- [ ] **Step 2: Add dangerous string and glob regression tests**

Expand `test_rejects_root_and_broad_delete_entries_before_execution` to include all rejected rsync delete entry forms:

```python
@pytest.mark.parametrize("value", ["", "/", ".", "..", "../escape", "old/../escape", "*/wide", "wide?", "[abc]"])
def test_rejects_root_and_broad_delete_entries_before_execution(value):
    with pytest.raises(remote_safe_delete.PathSafetyError):
        remote_safe_delete.validate_delete_entry(value)
```

Add explicit absolute path rejection tests that only touch the fake root sentinel:

```python
@pytest.mark.parametrize("remote_path", ["", ".", "..", "/", "/srv/../escape", "/srv/*", "/srv/name?", "/srv/[abc]"])
def test_unsafe_explicit_remote_paths_do_not_touch_fake_remote_root(tmp_path, remote_path):
    fake_root = tmp_path / "remote-root"
    sentinel = fake_root / "sentinel.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("do not move\n", encoding="utf-8")

    with pytest.raises(remote_safe_delete.PathSafetyError):
        remote_safe_delete.build_explicit_path_plan(
            remote_path=remote_path,
            env="test",
            remote_project_root="/srv/app",
            remote_archive_root="/archive",
            purpose="unsafe-path",
        )

    assert sentinel.read_text(encoding="utf-8") == "do not move\n"
    assert not (fake_root / "archive").exists()
```

- [ ] **Step 3: Run focused tests and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_remote_safe_delete.py
```

Expected: FAIL because `validate_local_remote_root` does not exist and fake root mapping currently resolves `/` without a dedicated high-risk root guard.

- [ ] **Step 4: Implement fake root validation in the shared skill script**

Add this helper near `map_remote_path`:

```python
def configured_local_archive_roots() -> list[Path]:
    roots: list[Path] = []
    for name in ("ASD_SAFE_ARCHIVE_ROOT", REMOTE_ARCHIVE_ROOT_ENV):
        value = os.environ.get(name)
        if value and not value.startswith("/") and not value.startswith("~"):
            continue
        if value:
            roots.append(Path(value).expanduser().resolve())
    return roots


def is_same_or_nested(path: Path, candidate: Path) -> bool:
    return path == candidate or candidate in path.parents


def validate_local_remote_root(local_remote_root: Path | str) -> Path:
    root = Path(local_remote_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise PathSafetyError(f"local remote root must be an existing directory: {local_remote_root!r}")

    repo_root = Path(__file__).resolve().parent.parent
    if root == Path("/").resolve():
        raise PathSafetyError("refusing dangerous local remote root: /")

    dangerous_roots = [Path.home().resolve(), repo_root.resolve()]
    dangerous_roots.extend(configured_local_archive_roots())
    for dangerous in dangerous_roots:
        if is_same_or_nested(root, dangerous):
            raise PathSafetyError(f"refusing dangerous local remote root: {root}")

    for system_root in (Path("/tmp"), Path("/var"), Path("/home"), Path("/root")):
        resolved = system_root.resolve()
        if root == resolved:
            raise PathSafetyError(f"refusing system directory as local remote root: {root}")
    return root
```

Update `map_remote_path` to call this helper:

```python
def map_remote_path(local_remote_root: Path | str, remote_path: str) -> Path:
    safe_remote_path = validate_remote_absolute_path(remote_path)
    relative_path = safe_remote_path.lstrip("/")
    root = validate_local_remote_root(local_remote_root)
    mapped = (root / relative_path).resolve()
    if root != mapped and root not in mapped.parents:
        raise PathSafetyError(f"mapped path escapes fake remote root: {remote_path!r}")
    return mapped
```

If any test constructs `fake_root` but does not create the directory before calling plan execution, add `fake_root.mkdir()` or create parent files first so the root exists intentionally.

- [ ] **Step 5: Promote shared script to the independent repository**

Run:

```bash
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete --apply
```

Expected: the repository copy of `scripts/remote-safe-delete.py` now contains `validate_local_remote_root`. Do not use `export`.

- [ ] **Step 6: Run focused tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_remote_safe_delete.py
```

Expected: PASS for fake root and dangerous path tests. No test should move anything under real `/`, `$HOME`, repository root, or configured archive roots.

- [ ] **Step 7: Commit Task 3 in both repositories**

Run:

```bash
git -C "$HOME/.config/shared-skills" add agent-safe-delete/scripts/remote-safe-delete.py
git -C "$HOME/.config/shared-skills" commit -m "fix: 加强远端归档本地模拟根目录防护"

git add scripts/remote-safe-delete.py tests/test_remote_safe_delete.py
git commit -m "fix: 加强远端归档本地模拟根目录防护"
```

Expected: shared-skills commit contains the shared runtime script change; independent repo commit contains the promoted script and repository regression tests.

---

### Task 4: Post-Archive Verification And SSH Confirmation Parity

**Files:**
- Modify first: `$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py`
- Promote target: `<repo-root>/scripts/remote-safe-delete.py`
- Modify in repository: `<repo-root>/tests/test_remote_safe_delete.py`

- [ ] **Step 1: Write failing test for SSH archive-list forwarding high-risk confirmations**

Add this test near the existing SSH tests:

```python
def test_archive_list_ssh_forwards_high_risk_confirmation_to_runner():
    calls = []
    plan = remote_safe_delete.build_explicit_path_plan(
        remote_path="/srv/app/.env.production",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="manual-cleanup",
    )

    def runner(command, *, input_text):
        calls.append((command, input_text))
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}\n', stderr="")

    result = remote_safe_delete.archive_paths_ssh(
        ssh_target="<ssh-target>",
        remote_paths=["/srv/app/.env.production"],
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="manual-cleanup",
        source_mode="explicit-path-plan",
        plan_sha256=plan["plan_sha256"],
        confirm_high_risk=["/srv/app/.env.production"],
        runner=runner,
    )

    assert result == {"ok": True}
    assert len(calls) == 1
```

If the helper already passes, add a CLI-shaped unit test around `command_archive_list` with monkeypatched `archive_paths_ssh`:

```python
def test_command_archive_list_ssh_passes_confirm_high_risk(tmp_path, monkeypatch):
    plan = remote_safe_delete.build_explicit_path_plan(
        remote_path="/srv/app/.env.production",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="manual-cleanup",
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    captured = {}

    def fake_archive_paths_ssh(**kwargs):
        captured.update(kwargs)
        return {"items": [], "manifest_path": "/archive/manifest.json", "restore_script": "/archive/restore.sh"}

    monkeypatch.setattr(remote_safe_delete, "archive_paths_ssh", fake_archive_paths_ssh)
    args = remote_safe_delete.build_parser().parse_args([
        "archive-list",
        "--ssh-target",
        "<ssh-target>",
        "--plan",
        str(plan_path),
        "--confirm-plan",
        plan["plan_sha256"],
        "--confirm-high-risk",
        "/srv/app/.env.production",
    ])

    assert remote_safe_delete.command_archive_list(args) == 0
    assert captured["confirm_high_risk"] == ["/srv/app/.env.production"]
```

- [ ] **Step 2: Write failing tests for post-archive verification metadata**

Add this assertion to `test_archive_explicit_path_plan_moves_fake_remote_file_and_writes_manifest`:

```python
    assert result["items"][0]["verified_source_missing"] is True
    assert result["items"][0]["verified_archive_exists"] is True
```

Add equivalent assertions to `test_archive_plan_accepts_confirm_plan_for_test_env`:

```python
    assert result["items"][0]["verified_source_missing"] is True
    assert result["items"][0]["verified_archive_exists"] is True
```

- [ ] **Step 3: Run focused tests and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_remote_safe_delete.py
```

Expected: FAIL because verification flags are not written yet, or because `command_archive_list` does not forward `confirm_high_risk` to the SSH backend.

- [ ] **Step 4: Implement post-archive verification in the shared skill script**

In `archive_items_local`, after each `shutil.move`, verify both sides before appending metadata:

```python
        shutil.move(str(local_path), str(local_destination))
        source_missing = not (local_path.exists() or local_path.is_symlink())
        archive_exists = local_destination.exists() or local_destination.is_symlink()
        if not source_missing or not archive_exists:
            raise UsageError(f"archive verification failed for {safe_remote_path}")
        metadata["archived_path"] = remote_destination
        metadata["restore_command"] = f"mkdir -p {sh_quote(os.path.dirname(safe_remote_path))} && mv {sh_quote(remote_destination)} {sh_quote(safe_remote_path)}"
        metadata["verified_source_missing"] = source_missing
        metadata["verified_archive_exists"] = archive_exists
        archived_items.append(metadata)
```

In `REMOTE_PAYLOAD_BOOTSTRAP`, make the same change inside its `archive_paths(request)` loop:

```python
        shutil.move(str(local_path), destination)
        destination_path = Path(destination)
        source_missing = not (local_path.exists() or local_path.is_symlink())
        archive_exists = destination_path.exists() or destination_path.is_symlink()
        if not source_missing or not archive_exists:
            fail(f"archive verification failed for {remote_path}")
        metadata["archived_path"] = destination
        metadata["restore_command"] = f"mkdir -p {sh_quote(os.path.dirname(remote_path))} && mv {sh_quote(destination)} {sh_quote(remote_path)}"
        metadata["verified_source_missing"] = source_missing
        metadata["verified_archive_exists"] = archive_exists
        archived_items.append(metadata)
```

- [ ] **Step 5: Forward high-risk confirmations to SSH archive-list**

In `command_archive_list`, SSH branch must pass the already-confirmed high-risk path list into `archive_paths_ssh`:

```python
            confirm_high_risk=args.confirm_high_risk or [],
```

This prevents local plan gate and SSH backend gate from drifting apart.

- [ ] **Step 6: Promote shared script to the independent repository**

Run:

```bash
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete --apply
```

Expected: the repository copy of `scripts/remote-safe-delete.py` now contains post-archive verification and SSH confirmation forwarding. Do not use `export`.

- [ ] **Step 7: Run focused tests and verify pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_remote_safe_delete.py
```

Expected: PASS for post-archive verification and SSH confirmation parity.

- [ ] **Step 8: Commit Task 4 in both repositories**

Run:

```bash
git -C "$HOME/.config/shared-skills" add agent-safe-delete/scripts/remote-safe-delete.py
git -C "$HOME/.config/shared-skills" commit -m "fix: 校验远端归档执行结果"

git add scripts/remote-safe-delete.py tests/test_remote_safe_delete.py
git commit -m "fix: 校验远端归档执行结果"
```

Expected: shared-skills commit contains the shared runtime script change; independent repo commit contains the promoted script and repository regression tests.

---

### Task 5: Documentation Update For Unified Strict Mode

**Files:**
- Modify first: `$HOME/.config/shared-skills/agent-safe-delete/SKILL.md`
- Promote target: `<repo-root>/SKILL.md`
- Modify in repository: `<repo-root>/README.md`
- Modify in repository: `<repo-root>/README.en.md`
- Optional Modify in repository: `<repo-root>/docs/2026-04-29-remote-safe-delete-implementation-report.md`

- [ ] **Step 1: Update shared `SKILL.md` command examples**

In `$HOME/.config/shared-skills/agent-safe-delete/SKILL.md`, update the remote command examples so execution always uses `archive-list --confirm-plan <plan_sha256>`. Because this file is the installed skill document, every command must use the shared-skill absolute entry `python "$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py"`, not `python scripts/remote-safe-delete.py`.

Replace the current `archive-list` example with:

```bash
python "$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py" archive-list \
  --ssh-target <ssh-target> \
  --plan <plan.json> \
  --confirm-plan <plan_sha256>
```

Replace the current direct `archive-path` execution example with explicit path planning plus plan execution:

```bash
python "$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py" plan-path \
  --remote-path <remote-absolute-path> \
  --env test \
  --remote-project-root <remote-project-root> \
  --remote-archive-root <remote-archive-root> \
  --purpose <purpose> \
  --output <plan.json>

python "$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py" archive-list \
  --ssh-target <ssh-target> \
  --plan <plan.json> \
  --confirm-plan <plan_sha256>
```

Update the high-risk example to include both confirmations:

```bash
python "$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py" archive-list \
  --ssh-target <ssh-target> \
  --plan <plan.json> \
  --confirm-plan <plan_sha256> \
  --confirm-high-risk <remote-absolute-path>
```

- [ ] **Step 2: Replace environment-specific gate wording in shared `SKILL.md`**

Replace this obsolete paragraph:

```text
远端生产环境额外要求：源码发布或目录清理必须有明确版本、回退路径和验证步骤；不要从 dirty worktree 触发生产清理。`prod` 计划必须带 `--source-git-ref <commit-or-tag>`，执行 `archive-list` 时必须带 `--confirm-plan <plan_sha256>`。测试环境也必须先归档再删除，但可以在清单明确且风险门禁通过时更快执行。
```

With:

```text
远端安全归档工具层统一使用严格可恢复模式：`test` 和 `prod` 只作为环境身份、归档目录分组和审计字段，不决定门禁强弱。所有远端归档执行都必须先生成带 `plan_sha256` 的计划，并在执行 `archive-list` 时显式提供 `--confirm-plan <plan_sha256>`。高风险路径仍必须逐项提供精确的 `--confirm-high-risk <remote-absolute-path>`。`--source-git-ref` 仅作为可选审计字段保留；具体项目若要求正式环境稳定 commit/tag、回退预案、发布窗口或人工确认，应由项目环境治理技能负责，不应变成安全归档工具内部的另一套执行逻辑。
```

Update the supported subcommands list from:

```text
- `plan-rsync-delete`
- `archive-list`
- `archive-path`
```

To:

```text
- `plan-rsync-delete`
- `plan-path`
- `archive-list`
- `archive-path`（兼容入口；直接执行会失败并提示改用 `plan-path` + `archive-list`）
```

- [ ] **Step 3: Promote shared `SKILL.md` to the independent repository**

Run:

```bash
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete --apply
```

Expected: `<repo-root>/SKILL.md` receives the shared skill documentation with repository-path transforms applied. Do not use `export`.

- [ ] **Step 4: Update repository `README.md` remote archive section**

In `README.md`, replace the direct explicit archive example with the same `plan-path` plus `archive-list --confirm-plan` flow used in `SKILL.md`.

Replace the production-specific paragraph:

```text
生产环境要求更严格：`plan-rsync-delete` 必须提供 `--source-git-ref <commit-or-tag>`，`archive-list` 必须提供 `--confirm-plan <plan_sha256>`。高风险路径还必须逐项提供 `--confirm-high-risk <remote-absolute-path>`。
```

With:

```text
远端安全归档工具层不再区分“测试环境宽松 / 正式环境严格”两套行为。`test` 和 `prod` 只用于环境身份、归档目录分组和审计字段；所有远端归档执行都必须先生成带 `plan_sha256` 的计划，并在 `archive-list` 中显式提供 `--confirm-plan <plan_sha256>`。高风险路径还必须逐项提供 `--confirm-high-risk <remote-absolute-path>`。`--source-git-ref` 仅作为可选审计字段保留；正式环境的稳定 commit/tag、回退预案、发布窗口和人工确认由项目环境治理流程负责。
```

In the behavior constraints list, add:

```text
- 本地模拟远端时，`--local-remote-root` 不能指向真实 `/`、用户 home、仓库根目录、配置的归档根目录或系统关键目录；测试必须使用临时 fake remote root。
```

- [ ] **Step 5: Update repository `README.en.md` remote archive section**

Use this English replacement for the production-specific paragraph:

```text
The remote safe-archive tool no longer has separate “loose test / strict production” behavior. `test` and `prod` are environment identities used for archive grouping and audit fields only; every remote archive execution must first generate a plan with `plan_sha256`, and `archive-list` must receive `--confirm-plan <plan_sha256>`. High-risk paths still require exact per-path `--confirm-high-risk <remote-absolute-path>` confirmations. `--source-git-ref` remains an optional audit field; project-level production requirements such as stable commits or tags, rollback plans, release windows, and human approvals belong in project environment governance, not in this generic safe-archive tool.
```

Replace direct explicit archive examples with `plan-path` plus `archive-list --confirm-plan`, and add this behavior guarantee:

```text
- In local fake-remote mode, `--local-remote-root` cannot point at the real `/`, the user home directory, the repository root, configured archive roots, or critical system directories; tests must use a temporary fake remote root.
```

- [ ] **Step 6: Optionally append a short note to the historical implementation report**

If preserving implementation history is desired, append this section to `docs/2026-04-29-remote-safe-delete-implementation-report.md`:

```markdown
## 后续设计调整：统一严格可恢复模式

后续确认远端安全归档工具层不再保留“测试环境宽松 / 正式环境严格”的两套门禁。`test` 和 `prod` 只保留为环境身份、归档目录分组和审计字段。所有远端归档执行统一要求先生成带 `plan_sha256` 的计划，并在执行时显式提供 `--confirm-plan <plan_sha256>`；高风险路径仍必须逐项精确确认。正式环境额外的稳定版本、回退预案、发布窗口和人工确认属于项目环境治理流程，不再作为通用安全归档工具内部逻辑。
```

- [ ] **Step 7: Run documentation grep checks**

Run:

```bash
rg -n "测试环境.*宽松|正式环境.*严格|prod.*requires source_git_ref|production is stricter|生产环境要求更严格|prod.*confirm_plan" SKILL.md README.md README.en.md docs
```

Expected: no matches, except historical report text if Task 5 Step 5 intentionally keeps old behavior in a history section. If historical matches remain, add adjacent text clearly marking them as obsolete historical behavior.

- [ ] **Step 8: Commit Task 5 in both repositories**

Run:

```bash
git -C "$HOME/.config/shared-skills" add agent-safe-delete/SKILL.md
git -C "$HOME/.config/shared-skills" commit -m "docs: 说明远端归档统一严格模式"

git add SKILL.md README.md README.en.md docs/2026-04-29-remote-safe-delete-implementation-report.md
git commit -m "docs: 说明远端归档统一严格模式"
```

Expected: shared-skills commit contains the installed skill documentation; independent repo commit contains the promoted `SKILL.md` plus repository README/docs updates. If the optional report file was not modified, omit it from `git add`.

---

### Task 6: Verification And Independent Repository Publication

**Files:**
- Verify shared install state: `$HOME/.config/shared-skills/agent-safe-delete/SKILL.md`
- Verify shared install state: `$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py`
- Verify independent repository state: `<repo-root>/`

- [ ] **Step 1: Run focused remote tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_remote_safe_delete.py
```

Expected: all tests in `tests/test_remote_safe_delete.py` pass.

- [ ] **Step 2: Run full pytest suite without cache artifacts**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider
```

Expected: all tests pass. This command avoids writing `.pyc` and `.pytest_cache` artifacts.

- [ ] **Step 3: Verify CLI help**

Run:

```bash
python scripts/agent-safe-delete.py --help
python scripts/remote-safe-delete.py --help
python "$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py" --help
```

Expected: all commands exit 0. Both repository and shared `remote-safe-delete.py --help` outputs list `plan-rsync-delete`, `plan-path`, `archive-list`, and the compatibility `archive-path` command.

- [ ] **Step 4: Avoid unsafe smoke cleanup unless separately redesigned**

Do not run `./tests/smoke.sh` in this plan as-is if it still contains a direct `rm -rf "$tmpdir"` trap. The user explicitly required that generated artifacts, caches, and temporary files must not be removed with direct `rm` during this work. If smoke verification is required before release, first create a separate small plan to make the smoke script cleanup compatible with `agent-safe-delete`, or run it only after explicit user approval for that existing test-script cleanup behavior.

- [ ] **Step 5: Check formatting-sensitive whitespace**

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 6: Verify promote status after Shared-First sync**

Run:

```bash
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" status agent-safe-delete
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete
```

Expected: status reports no unexpected drift after each prior task's promote, or only intentional changes awaiting final promote. `promote` preview must be empty or contain only manifest-selected install files; do not use Repo-First `export`.

- [ ] **Step 7: Apply final promote if preview still has intended changes**

Run:

```bash
python "$HOME/.config/shared-skills/maintaining-publishable-skills/scripts/publishable-skill.py" promote agent-safe-delete --apply
```

Expected: independent repository receives any remaining shared install changes through manifest-selected copy and path transforms. Existing symlink-managed agent skill directories do not need separate content edits.

- [ ] **Step 8: Verify shared install CLI help**

Run:

```bash
python "$HOME/.config/shared-skills/agent-safe-delete/scripts/remote-safe-delete.py" --help
```

Expected: exits 0 and lists `plan-path` plus strict `archive-list` usage.

- [ ] **Step 9: Inspect final status**

Run:

```bash
git -C "$HOME/.config/shared-skills" status --short --branch
git status --short --branch
```

Expected: shared-skills and independent repository statuses show only intentional committed changes, or no uncommitted changes if each task was committed. Do not run `git clean` to remove artifacts.

- [ ] **Step 10: Final commit for verification notes if needed**

If Task 6 introduced any repository-local verification notes, commit them with:

```bash
git add <changed-repository-files>
git commit -m "chore: 验证远端归档严格模式"
```

If Task 6 introduced any shared-skill verification notes, commit them separately in `$HOME/.config/shared-skills` with:

```bash
git -C "$HOME/.config/shared-skills" add <changed-shared-files>
git -C "$HOME/.config/shared-skills" commit -m "chore: 验证远端归档严格模式"
```

Expected: no unrelated generated artifacts are committed in either repository.

---

## Self-Review Notes

- Spec coverage: plan covers unified `confirm_plan` for every env, optional `source_git_ref`, explicit path planning, high-risk exact confirmations, fake root isolation, post-archive verification, shared-first implementation, and independent repository publication through `promote`.
- Placeholder scan: no `TBD`, `TODO`, or “similar to” implementation steps remain.
- Type consistency: plan consistently uses `plan_sha256`, `confirm_plan`, `confirm_high_risk`, `build_explicit_path_plan`, `plan-path`, and `archive-list`.
- Scope boundary: project-specific production gates stay in project environment governance; this plan only changes the generic safe-archive tool and its documentation.
