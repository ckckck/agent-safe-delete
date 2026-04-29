import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parent.parent
REMOTE_CLI = ROOT_DIR / "scripts" / "remote-safe-delete.py"


def load_remote_safe_delete():
    spec = importlib.util.spec_from_file_location("remote_safe_delete", REMOTE_CLI)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


remote_safe_delete = load_remote_safe_delete()


def test_parse_rsync_deleting_lines():
    output = "*deleting old.txt\n*deleting old-dir/\n>f+++++++++ keep.txt\n"

    assert remote_safe_delete.parse_rsync_deletions(output) == ["old.txt", "old-dir/"]


def test_rejects_root_and_broad_delete_entries_before_execution():
    for value in ["", "/", ".", "..", "../escape", "*/wide", "/etc/passwd"]:
        with pytest.raises(remote_safe_delete.PathSafetyError):
            remote_safe_delete.validate_delete_entry(value)


def test_explicit_remote_absolute_path_still_allows_safe_absolute_paths():
    assert remote_safe_delete.validate_remote_absolute_path("/srv/app/tmp.txt") == "/srv/app/tmp.txt"


def test_build_rsync_delete_plan_contains_stable_hash_and_normalized_items():
    output = "*deleting old/\n*deleting old/file.txt\n*deleting stale.txt\n"

    plan = remote_safe_delete.build_rsync_delete_plan(
        dry_run_output=output,
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/srv/archive",
        purpose="deploy-sync",
        source_git_ref="dirty-worktree",
    )

    assert plan["source_mode"] == "rsync-delete-plan"
    assert plan["plan_sha256"]
    assert [item["path"] for item in plan["items"]] == ["old/", "stale.txt"]


def test_prod_plan_requires_source_git_ref():
    with pytest.raises(remote_safe_delete.UsageError):
        remote_safe_delete.build_rsync_delete_plan(
            dry_run_output="*deleting old.txt\n",
            env="prod",
            remote_project_root="/srv/app",
            remote_archive_root="/srv/archive",
            purpose="prod-sync",
        )


def test_archive_explicit_path_moves_fake_remote_file_and_writes_manifest(tmp_path):
    fake_root = tmp_path / "remote-root"
    fake_file = fake_root / "srv" / "app" / "tmp.txt"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("temporary\n", encoding="utf-8")

    result = remote_safe_delete.archive_explicit_path_local(
        local_remote_root=fake_root,
        remote_path="/srv/app/tmp.txt",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="manual-cleanup",
    )

    manifest_path = fake_root / result["manifest_path"].lstrip("/")
    restore_path = fake_root / result["restore_script"].lstrip("/")
    payload_path = fake_root / result["items"][0]["archived_path"].lstrip("/")

    assert not fake_file.exists()
    assert payload_path.read_text(encoding="utf-8") == "temporary\n"
    assert manifest_path.is_file()
    assert restore_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["items"][0]["original_path"] == "/srv/app/tmp.txt"


def test_archive_explicit_path_rejects_root_without_touching_fake_remote_root(tmp_path):
    fake_root = tmp_path / "remote-root"
    fake_root.mkdir()
    sentinel = fake_root / "sentinel.txt"
    sentinel.write_text("do not move\n", encoding="utf-8")

    with pytest.raises(remote_safe_delete.PathSafetyError):
        remote_safe_delete.archive_explicit_path_local(
            local_remote_root=fake_root,
            remote_path="/",
            env="test",
            remote_project_root="/srv/app",
            remote_archive_root="/archive",
            purpose="unsafe-root",
        )

    assert sentinel.read_text(encoding="utf-8") == "do not move\n"
    assert not (fake_root / "archive").exists()


def test_archive_explicit_high_risk_path_requires_exact_confirmation(tmp_path):
    fake_root = tmp_path / "remote-root"
    env_file = fake_root / "srv" / "app" / ".env.production"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("REDACTED=value\n", encoding="utf-8")

    with pytest.raises(remote_safe_delete.UsageError):
        remote_safe_delete.archive_explicit_path_local(
            local_remote_root=fake_root,
            remote_path="/srv/app/.env.production",
            env="test",
            remote_project_root="/srv/app",
            remote_archive_root="/archive",
            purpose="manual-cleanup",
        )

    assert env_file.read_text(encoding="utf-8") == "REDACTED=value\n"

    result = remote_safe_delete.archive_explicit_path_local(
        local_remote_root=fake_root,
        remote_path="/srv/app/.env.production",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="manual-cleanup",
        confirm_high_risk=["/srv/app/.env.production"],
    )

    assert result["risk_level"] == "high"
    assert not env_file.exists()


def test_archive_explicit_project_root_requires_confirmation_without_moving(tmp_path):
    fake_root = tmp_path / "remote-root"
    project_root = fake_root / "srv" / "app"
    sentinel = project_root / "sentinel.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("do not move\n", encoding="utf-8")

    with pytest.raises(remote_safe_delete.UsageError):
        remote_safe_delete.archive_explicit_path_local(
            local_remote_root=fake_root,
            remote_path="/srv/app",
            env="test",
            remote_project_root="/srv/app",
            remote_archive_root="/archive",
            purpose="manual-cleanup",
        )

    assert sentinel.read_text(encoding="utf-8") == "do not move\n"
    assert project_root.is_dir()


def test_archive_plan_prod_requires_confirm_plan(tmp_path):
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
        source_git_ref="v1.2.3",
    )

    with pytest.raises(remote_safe_delete.UsageError):
        remote_safe_delete.archive_plan_local(local_remote_root=fake_root, plan=plan)

    assert stale.read_text(encoding="utf-8") == "stale\n"


def test_archive_plan_prod_rejects_wrong_confirm_plan(tmp_path):
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
        source_git_ref="v1.2.3",
    )

    with pytest.raises(remote_safe_delete.UsageError):
        remote_safe_delete.archive_plan_local(local_remote_root=fake_root, plan=plan, confirm_plan="wrong")

    assert stale.read_text(encoding="utf-8") == "stale\n"


def test_archive_plan_high_risk_requires_exact_path_confirmation(tmp_path):
    fake_root = tmp_path / "remote-root"
    env_file = fake_root / "srv" / "app" / ".env.production"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("REDACTED=value\n", encoding="utf-8")
    plan = remote_safe_delete.build_rsync_delete_plan(
        dry_run_output="*deleting .env.production\n",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="test-sync",
    )

    with pytest.raises(remote_safe_delete.UsageError):
        remote_safe_delete.archive_plan_local(local_remote_root=fake_root, plan=plan)

    assert env_file.read_text(encoding="utf-8") == "REDACTED=value\n"

    result = remote_safe_delete.archive_plan_local(
        local_remote_root=fake_root,
        plan=plan,
        confirm_high_risk=["/srv/app/.env.production"],
    )

    assert result["risk_level"] == "high"
    assert not env_file.exists()


def run_remote_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REMOTE_CLI), *args],
        capture_output=True,
        text=True,
        cwd=ROOT_DIR,
        env=env,
    )


def test_cli_help_exits_zero():
    result = run_remote_cli("--help")

    assert result.returncode == 0
    assert "remote-safe-delete.py" in result.stdout


def test_cli_plan_rsync_delete_writes_plan(tmp_path):
    dry_run_output = tmp_path / "dry-run.txt"
    plan_path = tmp_path / "plan.json"
    dry_run_output.write_text("*deleting old.txt\n", encoding="utf-8")

    result = run_remote_cli(
        "plan-rsync-delete",
        "--dry-run-output",
        str(dry_run_output),
        "--env",
        "test",
        "--remote-project-root",
        "/srv/app",
        "--remote-archive-root",
        "/archive",
        "--purpose",
        "deploy-sync",
        "--output",
        str(plan_path),
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["items"][0]["path"] == "old.txt"


def test_cli_plan_rsync_delete_uses_remote_archive_root_env(tmp_path):
    dry_run_output = tmp_path / "dry-run.txt"
    plan_path = tmp_path / "plan.json"
    dry_run_output.write_text("*deleting old.txt\n", encoding="utf-8")

    result = run_remote_cli(
        "plan-rsync-delete",
        "--dry-run-output",
        str(dry_run_output),
        "--env",
        "test",
        "--remote-project-root",
        "/srv/app",
        "--purpose",
        "deploy-sync",
        "--output",
        str(plan_path),
        env={**os.environ, "ASD_REMOTE_ARCHIVE_ROOT": "/archive-from-env"},
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["remote_archive_root"] == "/archive-from-env"


def test_cli_remote_archive_root_arg_overrides_env(tmp_path):
    dry_run_output = tmp_path / "dry-run.txt"
    plan_path = tmp_path / "plan.json"
    dry_run_output.write_text("*deleting old.txt\n", encoding="utf-8")

    result = run_remote_cli(
        "plan-rsync-delete",
        "--dry-run-output",
        str(dry_run_output),
        "--env",
        "test",
        "--remote-project-root",
        "/srv/app",
        "--remote-archive-root",
        "/archive-from-arg",
        "--purpose",
        "deploy-sync",
        "--output",
        str(plan_path),
        env={**os.environ, "ASD_REMOTE_ARCHIVE_ROOT": "/archive-from-env"},
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["remote_archive_root"] == "/archive-from-arg"


def test_cli_plan_rsync_delete_requires_archive_root_arg_or_env(tmp_path):
    dry_run_output = tmp_path / "dry-run.txt"
    dry_run_output.write_text("*deleting old.txt\n", encoding="utf-8")
    env = dict(os.environ)
    env.pop("ASD_REMOTE_ARCHIVE_ROOT", None)

    result = run_remote_cli(
        "plan-rsync-delete",
        "--dry-run-output",
        str(dry_run_output),
        "--env",
        "test",
        "--remote-project-root",
        "/srv/app",
        "--purpose",
        "deploy-sync",
        env=env,
    )

    assert result.returncode == 1
    assert "ASD_REMOTE_ARCHIVE_ROOT" in result.stderr


def test_home_relative_remote_archive_root_is_preserved_for_ssh_runner():
    def runner(command, *, input_text):
        assert command == ["ssh", "<ssh-target>", "python3", "-c", remote_safe_delete.REMOTE_PAYLOAD_BOOTSTRAP]
        assert '"remote_archive_root": "~/.agent-safe-delete"' in input_text
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}\n', stderr="")

    result = remote_safe_delete.archive_explicit_path_ssh(
        ssh_target="<ssh-target>",
        remote_path="/srv/app/tmp.txt",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="~/.agent-safe-delete",
        purpose="manual-cleanup",
        runner=runner,
    )

    assert result == {"ok": True}


def test_home_relative_remote_archive_root_rejects_traversal_and_globs():
    for value in ["~", "~/../escape", "~/*"]:
        with pytest.raises(remote_safe_delete.PathSafetyError):
            remote_safe_delete.validate_archive_root(value)


def test_cli_archive_path_archives_fake_remote_file(tmp_path):
    fake_root = tmp_path / "remote-root"
    fake_file = fake_root / "srv" / "app" / "tmp.txt"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("temporary\n", encoding="utf-8")

    result = run_remote_cli(
        "archive-path",
        "--local-remote-root",
        str(fake_root),
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
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert not fake_file.exists()
    assert payload["items"][0]["original_path"] == "/srv/app/tmp.txt"


def test_build_rsync_dry_run_command_includes_delete_and_itemize_flags():
    command = remote_safe_delete.build_rsync_dry_run_command(
        source="./dist/",
        destination="<ssh-target>:/srv/app/",
    )

    assert command[:4] == ["rsync", "--dry-run", "--delete", "--itemize-changes"]
    assert command[-2:] == ["./dist/", "<ssh-target>:/srv/app/"]


def test_archive_path_ssh_validates_before_runner():
    calls = []

    def runner(command, *, input_text):
        calls.append((command, input_text))
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}\n', stderr="")

    with pytest.raises(remote_safe_delete.PathSafetyError):
        remote_safe_delete.archive_explicit_path_ssh(
            ssh_target="<ssh-target>",
            remote_path="/",
            env="test",
            remote_project_root="/srv/app",
            remote_archive_root="/archive",
            purpose="unsafe-root",
            runner=runner,
        )

    assert calls == []


def test_archive_path_ssh_requires_high_risk_confirmation_before_runner():
    calls = []

    def runner(command, *, input_text):
        calls.append((command, input_text))
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}\n', stderr="")

    with pytest.raises(remote_safe_delete.UsageError):
        remote_safe_delete.archive_explicit_path_ssh(
            ssh_target="<ssh-target>",
            remote_path="/srv/app/.env.production",
            env="test",
            remote_project_root="/srv/app",
            remote_archive_root="/archive",
            purpose="manual-cleanup",
            runner=runner,
        )

    assert calls == []


def test_archive_path_ssh_sends_encoded_payload_to_runner():
    def runner(command, *, input_text):
        assert command == ["ssh", "<ssh-target>", "python3", "-c", remote_safe_delete.REMOTE_PAYLOAD_BOOTSTRAP]
        assert '"remote_paths": ["/srv/app/tmp.txt"]' in input_text
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}\n', stderr="")

    result = remote_safe_delete.archive_explicit_path_ssh(
        ssh_target="<ssh-target>",
        remote_path="/srv/app/tmp.txt",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="manual-cleanup",
        runner=runner,
    )

    assert result == {"ok": True}
