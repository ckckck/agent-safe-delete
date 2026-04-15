# Python CLI Cross-Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bash-only safe delete CLI with a single Python entrypoint that works consistently from PowerShell, macOS, and Linux while preserving current behavior.

**Architecture:** Introduce one standard-library Python CLI at `scripts/agent-safe-delete.py`, drive its behavior with black-box tests first, then migrate repository references and remove the legacy shell implementation. Keep command semantics and JSON output stable so the skill interface changes only at the launcher level.

**Tech Stack:** Python 3.11 standard library, shell smoke test, git

---

## File Structure

- Create: `scripts/agent-safe-delete.py` — 唯一 CLI 实现，负责参数解析、归档、恢复、metadata 和平台默认目录。
- Create: `tests/test_agent_safe_delete.py` — Python 黑盒回归测试，直接通过 subprocess 调用 CLI。
- Modify: `tests/smoke.sh` — 把黑盒冒烟入口从 `.sh` 改成 `python scripts/agent-safe-delete.py`。
- Modify: `README.md` — 更新主文档中的命令示例、依赖说明和 Windows 说明。
- Modify: `README.en.md` — 同步英文文档。
- Modify: `SKILL.md` — 把技能实现入口改成 Python 命令约定。
- Delete: `scripts/agent-safe-delete.sh` — 删除旧的 bash 真实实现。

### Task 1: Add failing Python regression tests

**Files:**
- Create: `tests/test_agent_safe_delete.py`

- [ ] **Step 1: Write the failing test file**

```python
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
CLI = ROOT_DIR / "scripts" / "agent-safe-delete.py"


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        env=merged_env,
        cwd=ROOT_DIR,
    )


class AgentSafeDeleteCLITest(unittest.TestCase):
    def test_show_archive_root_uses_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_root = str(Path(tmpdir) / "archive-root")
            result = run_cli("show-archive-root", env={"ASD_SAFE_ARCHIVE_ROOT": archive_root})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), archive_root)

    def test_archive_and_restore_file_with_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            archive_root = Path(tmpdir) / "archive-root"
            workspace.mkdir()
            source = workspace / "example.txt"
            source.write_text("hello\n", encoding="utf-8")

            archive = run_cli("archive", str(source), "--json", env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)})
            self.assertEqual(archive.returncode, 0, archive.stderr)
            payload = json.loads(archive.stdout)

            archived_path = Path(payload["archived_path"])
            metadata_path = Path(payload["metadata_path"])
            self.assertFalse(source.exists())
            self.assertTrue(archived_path.is_file())
            self.assertTrue(metadata_path.is_file())

            restore = run_cli("restore", payload["id"], env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)})
            self.assertEqual(restore.returncode, 0, restore.stderr)
            self.assertTrue(source.is_file())
            self.assertEqual(source.read_text(encoding="utf-8"), "hello\n")

    def test_archive_and_restore_broken_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            archive_root = Path(tmpdir) / "archive-root"
            workspace.mkdir()
            link_path = workspace / "broken-link"
            link_path.symlink_to(workspace / "missing-target")

            archive = run_cli("archive", str(link_path), "--json", env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)})
            self.assertEqual(archive.returncode, 0, archive.stderr)
            payload = json.loads(archive.stdout)
            self.assertEqual(payload["kind"], "symlink")
            self.assertFalse(link_path.exists())
            self.assertTrue(Path(payload["archived_path"]).is_symlink())

            restore = run_cli("restore", payload["id"], env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)})
            self.assertEqual(restore.returncode, 0, restore.stderr)
            self.assertTrue(link_path.is_symlink())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_agent_safe_delete -v`
Expected: FAIL with errors indicating `scripts/agent-safe-delete.py` does not exist yet.

- [ ] **Step 3: Commit**

```bash
git add tests/test_agent_safe_delete.py
git commit -m "test: define python cli regression coverage"
```

### Task 2: Implement the Python CLI

**Files:**
- Create: `scripts/agent-safe-delete.py`
- Test: `tests/test_agent_safe_delete.py`

- [ ] **Step 1: Write the next failing test coverage for directory archive and stale metadata pruning**

```python
    def test_archive_and_restore_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            archive_root = Path(tmpdir) / "archive-root"
            source_dir = workspace / "example-dir"
            nested_file = source_dir / "nested" / "value.txt"
            nested_file.parent.mkdir(parents=True)
            nested_file.write_text("world\n", encoding="utf-8")

            archive = run_cli("archive", str(source_dir), "--json", env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)})
            self.assertEqual(archive.returncode, 0, archive.stderr)
            payload = json.loads(archive.stdout)

            self.assertFalse(source_dir.exists())
            self.assertTrue(Path(payload["archived_path"]).is_dir())

            restore = run_cli("restore", payload["id"], env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)})
            self.assertEqual(restore.returncode, 0, restore.stderr)
            self.assertEqual(nested_file.read_text(encoding="utf-8"), "world\n")

    def test_show_archive_root_prunes_stale_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_root = Path(tmpdir) / "archive-root"
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            source_dir = workspace / "orphan-dir"
            source_dir.mkdir()

            archive = run_cli("archive", str(source_dir), "--json", env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)})
            self.assertEqual(archive.returncode, 0, archive.stderr)
            payload = json.loads(archive.stdout)
            archived_path = Path(payload["archived_path"])
            metadata_path = Path(payload["metadata_path"])

            self.assertTrue(metadata_path.is_file())
            archived_path.rmdir()

            show_root = run_cli("show-archive-root", env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)})
            self.assertEqual(show_root.returncode, 0, show_root.stderr)
            self.assertFalse(metadata_path.exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_agent_safe_delete -v`
Expected: FAIL because the new behaviors are not implemented yet.

- [ ] **Step 3: Write the minimal Python CLI implementation**

```python
#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def error(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def path_exists_or_link(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def default_archive_root() -> Path:
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "agent-safe-delete" / "safe-archive"
        return Path.home() / "AppData" / "Local" / "agent-safe-delete" / "safe-archive"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "agent-safe-delete" / "safe-archive"

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / "agent-safe-delete" / "safe-archive"
    return Path.home() / ".local" / "share" / "agent-safe-delete" / "safe-archive"


def resolved_archive_root() -> Path:
    configured = os.environ.get("ASD_SAFE_ARCHIVE_ROOT")
    base = Path(configured).expanduser() if configured else default_archive_root()
    return base.expanduser().resolve(strict=False)


def metadata_dir(archive_root: Path) -> Path:
    return archive_root / ".agent-safe-delete"


def ensure_archive_layout(archive_root: Path) -> None:
    archive_root.mkdir(parents=True, exist_ok=True)
    metadata_dir(archive_root).mkdir(parents=True, exist_ok=True)


def current_utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_entry_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"ASD-{stamp}-{uuid.uuid4().hex[:8]}"


def append_timestamp_path(target_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if target_path.is_dir():
        candidate = target_path.with_name(f"{target_path.name}-{timestamp}")
    else:
        suffix = "".join(target_path.suffixes)
        stem = target_path.name[:-len(suffix)] if suffix else target_path.name
        candidate = target_path.with_name(f"{stem}-{timestamp}{suffix}")

    counter = 1
    while path_exists_or_link(candidate):
        counter += 1
        if target_path.is_dir():
            candidate = target_path.with_name(f"{target_path.name}-{timestamp}-{counter}")
        else:
            suffix = "".join(target_path.suffixes)
            stem = target_path.name[:-len(suffix)] if suffix else target_path.name
            candidate = target_path.with_name(f"{stem}-{timestamp}-{counter}{suffix}")
    return candidate


def write_metadata(metadata_path: Path, data: dict[str, str | int]) -> None:
    metadata_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_metadata(metadata_path: Path) -> dict[str, str | int]:
    with metadata_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def prune_stale_metadata(archive_root: Path) -> None:
    ensure_archive_layout(archive_root)
    for metadata_path in metadata_dir(archive_root).glob("*.json"):
        try:
            data = read_metadata(metadata_path)
        except Exception:
            continue
        if data.get("restore_status") != "archived":
            continue
        archived_path = Path(str(data.get("archived_path", "")))
        if not archived_path:
            continue
        if not path_exists_or_link(archived_path):
            metadata_path.unlink()


def classify_kind(source_path: Path) -> str:
    if source_path.is_symlink():
        return "symlink"
    if source_path.is_dir():
        return "directory"
    return "file"


def show_archive_root(json_output: bool) -> int:
    archive_root = resolved_archive_root()
    prune_stale_metadata(archive_root)
    if json_output:
        print(json.dumps({"safe_archive_root": str(archive_root)}, ensure_ascii=False, indent=2))
    else:
        print(str(archive_root))
    return 0


def archive_path(source_input: str, json_output: bool) -> int:
    archive_root = resolved_archive_root()
    prune_stale_metadata(archive_root)
    source_path = Path(source_input).expanduser().resolve(strict=False)

    if not path_exists_or_link(source_path):
        error(f"归档失败：路径不存在：{source_input}")
    if source_path == archive_root:
        error(f"归档失败：不能归档归档根目录：{source_path}")
    if source_path == metadata_dir(archive_root):
        error(f"归档失败：不能归档元数据目录：{source_path}")
    if source_path == archive_root or archive_root in source_path.parents:
        error(f"归档失败：目标已经位于归档目录中：{source_path}")

    ensure_archive_layout(archive_root)
    entry_id = generate_entry_id()
    destination_path = archive_root / source_path.name
    if path_exists_or_link(destination_path):
        destination_path = append_timestamp_path(destination_path)

    shutil.move(str(source_path), str(destination_path))
    metadata_path = metadata_dir(archive_root) / f"{entry_id}.json"
    payload = {
        "schema_version": 2,
        "id": entry_id,
        "archived_at": current_utc_timestamp(),
        "original_path": str(source_path),
        "archived_path": str(destination_path),
        "archived_name": destination_path.name,
        "kind": classify_kind(destination_path),
        "safe_archive_root": str(archive_root),
        "restore_status": "archived",
    }
    write_metadata(metadata_path, payload)

    if json_output:
        print(json.dumps({
            "action": "archive",
            "id": entry_id,
            "original_path": str(source_path),
            "archived_path": str(destination_path),
            "metadata_path": str(metadata_path),
            "safe_archive_root": str(archive_root),
            "kind": payload["kind"],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"已归档: {source_path}")
        print(f"Entry ID: {entry_id}")
        print(f"归档位置: {destination_path}")
    return 0


def restore_path(entry_id: str, restore_to: str | None, json_output: bool) -> int:
    archive_root = resolved_archive_root()
    prune_stale_metadata(archive_root)
    metadata_path = metadata_dir(archive_root) / f"{entry_id}.json"
    if not metadata_path.is_file():
        error(f"恢复失败：找不到 metadata：{metadata_path}")

    data = read_metadata(metadata_path)
    archived_path = Path(str(data["archived_path"]))
    if not path_exists_or_link(archived_path):
        error(f"恢复失败：归档对象不存在：{archived_path}")

    target_path = Path(restore_to).expanduser().resolve(strict=False) if restore_to else Path(str(data["original_path"]))
    if path_exists_or_link(target_path):
        error(f"恢复失败：目标路径已存在：{target_path}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(archived_path), str(target_path))
    data["restored_at"] = current_utc_timestamp()
    data["restored_to"] = str(target_path)
    data["restore_status"] = "restored"
    write_metadata(metadata_path, data)

    if json_output:
        print(json.dumps({
            "action": "restore",
            "id": entry_id,
            "restored_to": str(target_path),
            "metadata_path": str(metadata_path),
            "kind": data["kind"],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"已恢复 Entry: {entry_id}")
        print(f"恢复位置: {target_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-safe-delete.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    show_parser = subparsers.add_parser("show-archive-root")
    show_parser.add_argument("--json", action="store_true")

    archive_parser = subparsers.add_parser("archive")
    archive_parser.add_argument("path")
    archive_parser.add_argument("--json", action="store_true")

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("entry_id")
    restore_parser.add_argument("--to")
    restore_parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "show-archive-root":
        return show_archive_root(args.json)
    if args.command == "archive":
        return archive_path(args.path, args.json)
    if args.command == "restore":
        return restore_path(args.entry_id, args.to, args.json)
    parser.error("unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_agent_safe_delete -v`
Expected: PASS for the tests added in Tasks 1-2.

- [ ] **Step 5: Commit**

```bash
git add scripts/agent-safe-delete.py tests/test_agent_safe_delete.py
git commit -m "feat: add python safe delete cli"
```

### Task 3: Add output and conflict regression coverage

**Files:**
- Modify: `tests/test_agent_safe_delete.py`
- Modify: `scripts/agent-safe-delete.py`

- [ ] **Step 1: Write the failing tests for JSON fields, path conflicts, and restore collisions**

```python
    def test_archive_json_contains_expected_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            archive_root = Path(tmpdir) / "archive-root"
            workspace.mkdir()
            source = workspace / "example.txt"
            source.write_text("hello\n", encoding="utf-8")

            archive = run_cli("archive", str(source), "--json", env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)})
            payload = json.loads(archive.stdout)
            self.assertEqual(
                set(payload),
                {"action", "id", "original_path", "archived_path", "metadata_path", "safe_archive_root", "kind"},
            )

    def test_archive_renames_on_name_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            archive_root = Path(tmpdir) / "archive-root"
            workspace.mkdir()
            first = workspace / "example.txt"
            second = workspace / "second" / "example.txt"
            second.parent.mkdir()
            first.write_text("one\n", encoding="utf-8")
            second.write_text("two\n", encoding="utf-8")

            first_archive = json.loads(run_cli("archive", str(first), "--json", env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)}).stdout)
            second_archive = json.loads(run_cli("archive", str(second), "--json", env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)}).stdout)

            self.assertNotEqual(first_archive["archived_path"], second_archive["archived_path"])
            self.assertTrue(Path(second_archive["archived_path"]).is_file())

    def test_restore_fails_when_target_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            archive_root = Path(tmpdir) / "archive-root"
            workspace.mkdir()
            source = workspace / "example.txt"
            source.write_text("hello\n", encoding="utf-8")

            archive = json.loads(run_cli("archive", str(source), "--json", env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)}).stdout)
            source.write_text("occupied\n", encoding="utf-8")

            restore = run_cli("restore", archive["id"], env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)})
            self.assertNotEqual(restore.returncode, 0)
            self.assertIn("目标路径已存在", restore.stderr)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_agent_safe_delete -v`
Expected: FAIL because conflict handling and output assertions are not fully guaranteed yet.

- [ ] **Step 3: Adjust the Python CLI minimally to satisfy the tests**

```python
def append_timestamp_path(target_path: Path, is_directory: bool) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "".join(target_path.suffixes)
    stem = target_path.name[:-len(suffix)] if suffix else target_path.name
    if is_directory:
        candidate = target_path.with_name(f"{target_path.name}-{timestamp}")
    else:
        candidate = target_path.with_name(f"{stem}-{timestamp}{suffix}")

    counter = 1
    while path_exists_or_link(candidate):
        counter += 1
        if is_directory:
            candidate = target_path.with_name(f"{target_path.name}-{timestamp}-{counter}")
        else:
            candidate = target_path.with_name(f"{stem}-{timestamp}-{counter}{suffix}")
    return candidate


def archive_path(source_input: str, json_output: bool) -> int:
    archive_root = resolved_archive_root()
    prune_stale_metadata(archive_root)
    source_path = Path(source_input).expanduser().resolve(strict=False)
    source_kind = classify_kind(source_path)
    # ... keep existing validation ...
    destination_path = archive_root / source_path.name
    if path_exists_or_link(destination_path):
        destination_path = append_timestamp_path(destination_path, is_directory=(source_kind == "directory"))
    shutil.move(str(source_path), str(destination_path))
    payload = {
        # ... keep existing fields ...
        "kind": source_kind,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_agent_safe_delete -v`
Expected: PASS with all regression tests green.

- [ ] **Step 5: Commit**

```bash
git add scripts/agent-safe-delete.py tests/test_agent_safe_delete.py
git commit -m "test: cover python cli edge cases"
```

### Task 4: Migrate the smoke test to the Python entrypoint

**Files:**
- Modify: `tests/smoke.sh`
- Test: `tests/test_agent_safe_delete.py`

- [ ] **Step 1: Rewrite the smoke test to call the Python CLI directly**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLI=(python "$ROOT_DIR/scripts/agent-safe-delete.py")

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

export ASD_SAFE_ARCHIVE_ROOT="$tmpdir/archive-root"
workspace="$tmpdir/workspace"
mkdir -p "$workspace"
metadata_dir="$ASD_SAFE_ARCHIVE_ROOT/.agent-safe-delete"

show_root="$(${CLI[@]} show-archive-root)"
[ "$show_root" = "$ASD_SAFE_ARCHIVE_ROOT" ]

file_path="$workspace/example.txt"
printf 'hello\n' > "$file_path"

archive_file_json="$(${CLI[@]} archive "$file_path" --json)"
file_entry_id="$(printf '%s' "$archive_file_json" | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
file_archived_path="$(printf '%s' "$archive_file_json" | python -c 'import json,sys; print(json.load(sys.stdin)["archived_path"])')"
file_metadata_path="$(printf '%s' "$archive_file_json" | python -c 'import json,sys; print(json.load(sys.stdin)["metadata_path"])')"

[ ! -e "$file_path" ]
[ -f "$file_archived_path" ]
[ -f "$file_metadata_path" ]
[ -d "$metadata_dir" ]

${CLI[@]} restore "$file_entry_id" >/dev/null
[ -f "$file_path" ]
grep -q '^hello$' "$file_path"

dir_path="$workspace/example-dir"
mkdir -p "$dir_path/nested"
printf 'world\n' > "$dir_path/nested/value.txt"

archive_dir_json="$(${CLI[@]} archive "$dir_path" --json)"
dir_entry_id="$(printf '%s' "$archive_dir_json" | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

${CLI[@]} restore "$dir_entry_id" >/dev/null
[ -f "$dir_path/nested/value.txt" ]
grep -q '^world$' "$dir_path/nested/value.txt"

broken_target="$workspace/missing-target"
broken_link="$workspace/broken-link"
ln -s "$broken_target" "$broken_link"
broken_json="$(${CLI[@]} archive "$broken_link" --json)"
broken_entry_id="$(printf '%s' "$broken_json" | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

${CLI[@]} restore "$broken_entry_id" >/dev/null
[ -L "$broken_link" ]
[ ! -e "$broken_link" ]

echo "smoke test passed"
```

- [ ] **Step 2: Run smoke test to verify it passes**

Run: `./tests/smoke.sh`
Expected: `smoke test passed`

- [ ] **Step 3: Commit**

```bash
git add tests/smoke.sh
git commit -m "test: point smoke coverage at python cli"
```

### Task 5: Migrate docs and skill references

**Files:**
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `SKILL.md`

- [ ] **Step 1: Update the main README command examples and dependency wording**

```md
## 使用方式

### 1. 作为技能使用

把整个目录放到 Agent 可发现的 skills 目录中，并让技能调用 `python scripts/agent-safe-delete.py`。

### 2. 直接作为命令行工具使用

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
```

- [ ] **Step 2: Update the English README with the same launcher change**

```md
## Usage

### 1. As a skill

Place this directory in the agent's skill search path and have the skill invoke `python scripts/agent-safe-delete.py`.

### 2. As a command-line tool

```bash
python scripts/agent-safe-delete.py show-archive-root
python scripts/agent-safe-delete.py archive ./example.txt
python scripts/agent-safe-delete.py archive ./build --json
python scripts/agent-safe-delete.py restore ASD-20260401-101530-8f3k2m
python scripts/agent-safe-delete.py restore ASD-20260401-101530-8f3k2m --to ./restored.txt
```

## Runtime requirements

- A working `python` command is required.
- `bash` is no longer the only supported launcher.
- The same command works from PowerShell, macOS, and Linux shells.
```

- [ ] **Step 3: Update the skill file to advertise the Python entrypoint**

```md
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

执行脚本：

```bash
python scripts/agent-safe-delete.py <subcommand> [args]
```
```

- [ ] **Step 4: Run a focused grep to verify no user-facing `.sh` launcher references remain**

Run: `rg -n "agent-safe-delete\.sh|scripts/agent-safe-delete\.sh" README.md README.en.md SKILL.md`
Expected: no matches

- [ ] **Step 5: Commit**

```bash
git add README.md README.en.md SKILL.md
git commit -m "docs: switch skill docs to python cli"
```

### Task 6: Remove the legacy shell implementation and run full verification

**Files:**
- Delete: `scripts/agent-safe-delete.sh`
- Modify: `tests/smoke.sh`
- Test: `tests/test_agent_safe_delete.py`

- [ ] **Step 1: Delete the legacy shell implementation**

Remove `scripts/agent-safe-delete.sh` from the repository after confirming Tasks 1-5 are green.

- [ ] **Step 2: Run Python regression tests**

Run: `python -m unittest tests.test_agent_safe_delete -v`
Expected: PASS

- [ ] **Step 3: Run smoke verification**

Run: `./tests/smoke.sh`
Expected: `smoke test passed`

- [ ] **Step 4: Run repository grep to confirm the old shell entrypoint is gone**

Run: `rg -n "agent-safe-delete\.sh|scripts/agent-safe-delete\.sh"`
Expected: no matches

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: replace shell safe delete cli with python"
```
