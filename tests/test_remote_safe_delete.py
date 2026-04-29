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


@pytest.mark.parametrize("value", ["", "/", ".", "..", "../escape", "old/../escape", "*/wide", "wide?", "[abc]", "/etc/passwd"])
def test_rejects_root_and_broad_delete_entries_before_execution(value):
    with pytest.raises(remote_safe_delete.PathSafetyError):
        remote_safe_delete.validate_delete_entry(value)


def test_explicit_remote_absolute_path_still_allows_safe_absolute_paths():
    assert remote_safe_delete.validate_remote_absolute_path("/srv/app/tmp.txt") == "/srv/app/tmp.txt"


@pytest.mark.parametrize("remote_path", ["/.", "/./", "//", "//srv/app", "/srv/./app", "/srv//app", "/./archive/file"])
def test_noncanonical_remote_absolute_paths_are_rejected(remote_path):
    with pytest.raises(remote_safe_delete.PathSafetyError):
        remote_safe_delete.validate_remote_absolute_path(remote_path)


@pytest.mark.parametrize("archive_root", ["/.", "/./", "//", "//archive", "/archive/./root", "/archive//root", "~", "~/", "~//", "~/../escape", "~/*"])
def test_noncanonical_archive_roots_are_rejected(archive_root):
    with pytest.raises(remote_safe_delete.PathSafetyError):
        remote_safe_delete.validate_archive_root(archive_root)


@pytest.mark.parametrize("purpose", ["", ".", "..", "../escape", "a/../../escape", "bad name", "wide*", "name?", "[abc]"])
def test_build_plan_rejects_unsafe_purpose(purpose):
    with pytest.raises(remote_safe_delete.UsageError, match="purpose must be a safe slug"):
        remote_safe_delete.build_explicit_path_plan(
            remote_path="/srv/app/tmp.txt",
            env="test",
            remote_project_root="/srv/app",
            remote_archive_root="/archive",
            purpose=purpose,
        )


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
    assert result["items"][0]["verified_source_missing"] is True
    assert result["items"][0]["verified_archive_exists"] is True
    assert manifest["items"][0]["verified_source_missing"] is True
    assert manifest["items"][0]["verified_archive_exists"] is True


def test_archive_explicit_path_local_direct_execution_is_disabled_without_touching_fake_remote_root(tmp_path):
    fake_root = tmp_path / "remote-root"
    fake_root.mkdir()
    fake_file = fake_root / "srv" / "app" / "tmp.txt"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("temporary\n", encoding="utf-8")

    with pytest.raises(remote_safe_delete.UsageError, match="requires plan_sha256"):
        remote_safe_delete.archive_explicit_path_local(
            local_remote_root=fake_root,
            remote_path="/srv/app/tmp.txt",
            env="test",
            remote_project_root="/srv/app",
            remote_archive_root="/archive",
            purpose="unsafe-root",
        )

    assert fake_file.read_text(encoding="utf-8") == "temporary\n"
    assert not (fake_root / "archive").exists()


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
        dry_run_output="*deleting stale.txt\n",
        env="test",
        remote_project_root="/srv/app",
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


@pytest.mark.parametrize("remote_path", ["", ".", "..", "/", "/.", "/./", "//", "//srv/app", "/srv/../escape", "/srv/./app", "/srv//app", "/srv/*", "/srv/name?", "/srv/[abc]"])
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


def test_archive_explicit_project_root_requires_confirmation_without_moving(tmp_path):
    fake_root = tmp_path / "remote-root"
    project_root = fake_root / "srv" / "app"
    sentinel = project_root / "sentinel.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("do not move\n", encoding="utf-8")
    plan = remote_safe_delete.build_explicit_path_plan(
        remote_path="/srv/app",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="manual-cleanup",
    )

    with pytest.raises(remote_safe_delete.UsageError):
        remote_safe_delete.archive_plan_local(
            local_remote_root=fake_root,
            plan=plan,
            confirm_plan=plan["plan_sha256"],
        )

    assert sentinel.read_text(encoding="utf-8") == "do not move\n"
    assert project_root.is_dir()


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
    assert result["items"][0]["verified_source_missing"] is True
    assert result["items"][0]["verified_archive_exists"] is True


def test_archive_plan_rejects_tampered_plan_content_before_touching_files(tmp_path):
    fake_root = tmp_path / "remote-root"
    stale = fake_root / "srv" / "app" / "stale.txt"
    other = fake_root / "srv" / "app" / "other.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n", encoding="utf-8")
    other.write_text("other\n", encoding="utf-8")
    plan = remote_safe_delete.build_rsync_delete_plan(
        dry_run_output="*deleting stale.txt\n",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="test-sync",
    )
    original_hash = plan["plan_sha256"]
    plan = json.loads(json.dumps(plan))
    plan["items"][0]["path"] = "other.txt"

    with pytest.raises(remote_safe_delete.UsageError, match="plan_sha256 does not match plan content"):
        remote_safe_delete.archive_plan_local(
            local_remote_root=fake_root,
            plan=plan,
            confirm_plan=original_hash,
        )

    assert stale.read_text(encoding="utf-8") == "stale\n"
    assert other.read_text(encoding="utf-8") == "other\n"
    assert not (fake_root / "archive").exists()


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
        confirm_plan=plan["plan_sha256"],
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
    plan = remote_safe_delete.build_explicit_path_plan(
        remote_path="/srv/app/tmp.txt",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="~/.agent-safe-delete",
        purpose="manual-cleanup",
    )

    def runner(command, *, input_text):
        assert command == ["ssh", "<ssh-target>", "python3", "-c", remote_safe_delete.REMOTE_PAYLOAD_BOOTSTRAP]
        assert '"remote_archive_root": "~/.agent-safe-delete"' in input_text
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}\n', stderr="")

    result = remote_safe_delete.archive_paths_ssh(
        ssh_target="<ssh-target>",
        remote_paths=["/srv/app/tmp.txt"],
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="~/.agent-safe-delete",
        purpose="manual-cleanup",
        source_mode="explicit-path-plan",
        plan_sha256=plan["plan_sha256"],
        runner=runner,
    )

    assert result == {"ok": True}


def test_home_relative_remote_archive_root_rejects_traversal_and_globs():
    for value in ["~", "~/../escape", "~/*"]:
        with pytest.raises(remote_safe_delete.PathSafetyError):
            remote_safe_delete.validate_archive_root(value)


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
        remote_safe_delete.archive_paths_ssh(
            ssh_target="<ssh-target>",
            remote_paths=["/"],
            env="test",
            remote_project_root="/srv/app",
            remote_archive_root="/archive",
            purpose="unsafe-root",
            source_mode="explicit-path-plan",
            plan_sha256="known-plan",
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


def test_archive_paths_ssh_rejects_missing_plan_sha256_before_runner():
    calls = []

    def runner(command, *, input_text):
        calls.append((command, input_text))
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}\n', stderr="")

    with pytest.raises(remote_safe_delete.UsageError, match="requires plan_sha256"):
        remote_safe_delete.archive_paths_ssh(
            ssh_target="<ssh-target>",
            remote_paths=["/srv/app/tmp.txt"],
            env="test",
            remote_project_root="/srv/app",
            remote_archive_root="/archive",
            purpose="manual-cleanup",
            source_mode="explicit-path-plan",
            runner=runner,
        )

    assert calls == []


def test_archive_explicit_path_ssh_direct_execution_is_disabled_before_runner():
    calls = []

    def runner(command, *, input_text):
        calls.append((command, input_text))
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}\n', stderr="")

    with pytest.raises(remote_safe_delete.UsageError, match="requires plan_sha256"):
        remote_safe_delete.archive_explicit_path_ssh(
            ssh_target="<ssh-target>",
            remote_path="/srv/app/tmp.txt",
            env="test",
            remote_project_root="/srv/app",
            remote_archive_root="/archive",
            purpose="manual-cleanup",
            runner=runner,
        )

    assert calls == []


def test_archive_paths_ssh_rejects_unsafe_purpose_before_runner():
    calls = []

    def runner(command, *, input_text):
        calls.append((command, input_text))
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}\n', stderr="")

    with pytest.raises(remote_safe_delete.UsageError, match="purpose must be a safe slug"):
        remote_safe_delete.archive_paths_ssh(
            ssh_target="<ssh-target>",
            remote_paths=["/srv/app/tmp.txt"],
            env="test",
            remote_project_root="/srv/app",
            remote_archive_root="/archive",
            purpose="../escape",
            source_mode="explicit-path-plan",
            plan_sha256="known-plan",
            runner=runner,
        )

    assert calls == []


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


def test_archive_path_ssh_sends_encoded_payload_to_runner():
    plan = remote_safe_delete.build_explicit_path_plan(
        remote_path="/srv/app/tmp.txt",
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="manual-cleanup",
    )

    def runner(command, *, input_text):
        assert command == ["ssh", "<ssh-target>", "python3", "-c", remote_safe_delete.REMOTE_PAYLOAD_BOOTSTRAP]
        assert '"remote_paths": ["/srv/app/tmp.txt"]' in input_text
        assert f'"plan_sha256": "{plan["plan_sha256"]}"' in input_text
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}\n', stderr="")

    result = remote_safe_delete.archive_paths_ssh(
        ssh_target="<ssh-target>",
        remote_paths=["/srv/app/tmp.txt"],
        env="test",
        remote_project_root="/srv/app",
        remote_archive_root="/archive",
        purpose="manual-cleanup",
        source_mode="explicit-path-plan",
        plan_sha256=plan["plan_sha256"],
        runner=runner,
    )

    assert result == {"ok": True}
