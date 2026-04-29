# Remote Safe Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add executable remote safe-delete support for test and production servers while preserving the existing local archive behavior.

**Architecture:** Keep the local CLI unchanged and add `scripts/remote-safe-delete.py` for remote planning and archive operations. The new CLI exposes pure validation and planning logic, a local fake remote backend for tests, and an SSH backend for real remote operations. Root and broad path safety is enforced before any backend is selected and again before any path is mapped or moved.

**Tech Stack:** Python standard library, `argparse`, `json`, `subprocess`, `pathlib`, `hashlib`, `shutil`, `unittest`/`pytest`.

---

### Task 1: Remote Plan Parser And Safety Gates

**Files:**
- Create: `tests/test_remote_safe_delete.py`
- Create: `scripts/remote-safe-delete.py`

- [x] **Step 1: Write failing tests for rsync delete parsing and root-path rejection**

Add tests that import the script with `importlib.util.spec_from_file_location` and assert:

```python
def test_parse_rsync_deleting_lines():
    output = "*deleting old.txt\n*deleting old-dir/\n>f+++++++++ keep.txt\n"
    assert remote_safe_delete.parse_rsync_deletions(output) == ["old.txt", "old-dir/"]

def test_rejects_root_and_broad_delete_entries_before_execution():
    for value in ["", "/", ".", "..", "../escape", "*/wide"]:
        with pytest.raises(remote_safe_delete.PathSafetyError):
            remote_safe_delete.validate_delete_entry(value)
```

These tests must not touch the real filesystem.

- [x] **Step 2: Run the new tests and verify they fail**

Run: `pytest -q tests/test_remote_safe_delete.py`

Expected: FAIL because `scripts/remote-safe-delete.py` does not exist.

- [x] **Step 3: Implement minimal parser and safety functions**

Create `scripts/remote-safe-delete.py` with:

```python
class PathSafetyError(ValueError):
    pass

def parse_rsync_deletions(output: str) -> list[str]:
    return [line.removeprefix("*deleting ").strip() for line in output.splitlines() if line.startswith("*deleting ")]

def validate_delete_entry(entry: str) -> str:
    value = entry.strip()
    if value in {"", "/", ".", ".."}:
        raise PathSafetyError(f"refusing unsafe remote path: {entry!r}")
    if any(part == ".." for part in value.split("/")):
        raise PathSafetyError(f"refusing path traversal: {entry!r}")
    if any(ch in value for ch in "*?[]"):
        raise PathSafetyError(f"refusing glob-like remote path: {entry!r}")
    return value
```

- [x] **Step 4: Run tests and verify pass**

Run: `pytest -q tests/test_remote_safe_delete.py`

Expected: PASS for parser and safety tests.

### Task 2: Plan Generation

**Files:**
- Modify: `tests/test_remote_safe_delete.py`
- Modify: `scripts/remote-safe-delete.py`

- [x] **Step 1: Write failing tests for plan generation**

Add tests that call `build_rsync_delete_plan(...)` and assert:

- plan contains `source_mode == "rsync-delete-plan"`
- `plan_sha256` is present
- nested entries like `old/` and `old/file.txt` collapse to `old/`
- `env="prod"` without `source_git_ref` raises `UsageError`

- [x] **Step 2: Run tests and verify they fail**

Run: `pytest -q tests/test_remote_safe_delete.py`

Expected: FAIL because plan generation is missing.

- [x] **Step 3: Implement plan generation**

Implement:

- `UsageError`
- `normalize_delete_entries(entries)`
- `classify_risk(path, remote_project_root)`
- `build_rsync_delete_plan(...)`
- stable SHA-256 over canonical JSON without the `plan_sha256` field

- [x] **Step 4: Run tests and verify pass**

Run: `pytest -q tests/test_remote_safe_delete.py`

Expected: PASS.

### Task 3: Explicit Path Archive With Fake Remote Root

**Files:**
- Modify: `tests/test_remote_safe_delete.py`
- Modify: `scripts/remote-safe-delete.py`

- [x] **Step 1: Write failing tests for `archive-path` core behavior**

Use `tmp_path` as a fake remote root. Create a fake remote file at `tmp_path / "srv/app/tmp.txt"`, call the local archive function with remote path `/srv/app/tmp.txt`, then assert:

- original fake remote file no longer exists
- archived payload file exists under fake `<remote-archive-root>/<env>/<batch>/payload/`
- `manifest.json` exists
- `restore.sh` exists
- manifest original path remains `/srv/app/tmp.txt`

- [x] **Step 2: Add double-insurance root test**

Add a test that calls the archive function with remote path `/` and fake remote root `tmp_path`, then asserts:

- `PathSafetyError` is raised
- a sentinel file under `tmp_path` still exists
- no archive batch directory is created

- [x] **Step 3: Run tests and verify they fail**

Run: `pytest -q tests/test_remote_safe_delete.py`

Expected: FAIL because archive execution is missing.

- [x] **Step 4: Implement local fake remote archive backend**

Implement:

- `validate_remote_absolute_path(path)`
- `map_remote_path(local_remote_root, remote_path)`
- preflight validation for all archive items before moving any item
- metadata capture with `stat`
- file checksum for regular files
- batch directory creation
- payload move with `shutil.move`
- `manifest.json`, `verify-before.txt`, `verify-after.txt`, and `restore.sh`

- [x] **Step 5: Run tests and verify pass**

Run: `pytest -q tests/test_remote_safe_delete.py`

Expected: PASS.

### Task 4: Plan-Based Archive And Environment Gates

**Files:**
- Modify: `tests/test_remote_safe_delete.py`
- Modify: `scripts/remote-safe-delete.py`

- [x] **Step 1: Write failing tests for `archive-list` gates**

Assert:

- `prod` plan execution without `confirm_plan` fails.
- wrong `confirm_plan` fails.
- high-risk `.env.production` fails without exact `confirm_high_risk` path.
- high-risk `.env.production` succeeds with exact confirmation.

- [x] **Step 2: Run tests and verify they fail**

Run: `pytest -q tests/test_remote_safe_delete.py`

Expected: FAIL because gates are missing.

- [x] **Step 3: Implement gates**

Implement:

- `ensure_environment_gates(...)`
- exact `confirm_plan` matching for production plan archives
- exact per-path high-risk confirmation
- production `source_git_ref` requirement for explicit path archive

- [x] **Step 4: Run tests and verify pass**

Run: `pytest -q tests/test_remote_safe_delete.py`

Expected: PASS.

### Task 5: CLI Commands And SSH Backend

**Files:**
- Modify: `tests/test_remote_safe_delete.py`
- Modify: `scripts/remote-safe-delete.py`

- [x] **Step 1: Write failing CLI tests**

Use subprocess to assert:

- `python scripts/remote-safe-delete.py --help` exits 0
- `plan-rsync-delete --dry-run-output <file> --output <plan>` writes a plan
- `archive-path --local-remote-root <tmp> ... --json` archives a fake remote file

- [x] **Step 2: Run tests and verify they fail**

Run: `pytest -q tests/test_remote_safe_delete.py`

Expected: FAIL because CLI parser is incomplete.

- [x] **Step 3: Implement CLI**

Add argparse subcommands:

- `plan-rsync-delete`
- `archive-list`
- `archive-path`

For SSH execution, build `ssh <target> python3 -c <bootstrap>` and send a JSON request on stdin. The SSH backend must receive already validated archive items and must validate them again before moving paths.

Implemented form: `ssh <ssh-target> python3 -c <bootstrap>` receives JSON on stdin. The implementation validates paths locally before starting SSH and validates them again inside the remote Python bootstrap.

- [x] **Step 4: Run tests and verify pass**

### Task 5.5: Explicit High-Risk Path Gates

**Files:**
- Modify: `tests/test_remote_safe_delete.py`
- Modify: `scripts/remote-safe-delete.py`

- [x] **Step 1: Write failing tests for explicit high-risk archive**

Covered `.env.production`, project root, and SSH explicit path before runner invocation.

- [x] **Step 2: Implement exact confirmation gates**

Added `ensure_high_risk_confirmations(...)` and wired it into fake local archive, SSH archive, and CLI `archive-path --confirm-high-risk`.

- [x] **Step 3: Run tests and verify pass**

Run: `pytest -q tests/test_remote_safe_delete.py`

Expected: PASS.

### Task 6: Documentation And Skill Wiring

**Files:**
- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `README.en.md`

- [x] **Step 1: Update skill documentation**

Document the installed-skill command path for `remote-safe-delete.py`, the two remote modes, test/prod gates, and the root-path safety requirement.

- [x] **Step 2: Update repository README files**

Document development-time commands and examples with placeholder values only.

- [x] **Step 3: Verify no real host or credential was introduced**

Run: `rg -n "192\.168|energyinsurlink|chituce\.energy|DEPLOY_SSH_PASSWORD|password|token|secret" SKILL.md README.md README.en.md scripts tests docs/superpowers`

Expected: no real host, domain, credential, password, token, or secret value. Generic words inside prose are acceptable only when not paired with values.

### Task 7: Full Verification

**Files:**
- Verify all changed files.

- [x] **Step 1: Run all tests**

Run: `pytest -q`

Expected: all tests pass.

- [x] **Step 2: Run CLI help checks**

Run:

```bash
python scripts/agent-safe-delete.py --help
python scripts/remote-safe-delete.py --help
./tests/smoke.sh
```

Expected: all commands exit 0.

- [x] **Step 3: Review git status**

Run: `git status --short`

Expected: implementation files are modified or added; pre-existing `.DS_Store` remains untracked and is not staged.
