#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


class PathSafetyError(ValueError):
    pass


class UsageError(ValueError):
    pass


RISK_ORDER = {"low": 1, "medium": 2, "high": 3}
REMOTE_ARCHIVE_ROOT_ENV = "ASD_REMOTE_ARCHIVE_ROOT"

REMOTE_PAYLOAD_BOOTSTRAP = r'''
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


class RemoteArchiveError(Exception):
    pass


def fail(message):
    raise RemoteArchiveError(message)


def validate_remote_absolute_path(path):
    value = str(path).strip()
    if not value.startswith("/"):
        fail(f"remote path must be absolute: {path!r}")
    if value in {"", "/", ".", ".."}:
        fail(f"refusing unsafe remote path: {path!r}")
    if any(part == ".." for part in value.split("/")):
        fail(f"refusing path traversal: {path!r}")
    if any(ch in value for ch in "*?[]"):
        fail(f"refusing glob-like remote path: {path!r}")
    return value.rstrip("/")


def validate_archive_root(path):
    raw_value = str(path).strip()
    if raw_value == "~":
        fail("remote archive root cannot be ~")
    if raw_value.startswith("~/"):
        suffix_parts = raw_value[2:].split("/")
        if any(part == ".." for part in suffix_parts):
            fail(f"refusing path traversal: {path!r}")
        if any(ch in raw_value for ch in "*?[]"):
            fail(f"refusing glob-like remote path: {path!r}")
    expanded = os.path.expanduser(raw_value)
    value = validate_remote_absolute_path(expanded)
    if value == "/":
        fail("remote archive root cannot be /")
    return value


def remote_join(root, *parts):
    return "/".join([root.rstrip("/"), *(str(part).strip("/") for part in parts if part)])


def current_utc_timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def batch_timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def path_kind(path):
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    return "file"


def sha256_file(path):
    if not path.is_file() or path.is_symlink():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_risk(path, remote_project_root):
    normalized = str(path).strip().rstrip("/")
    basename = normalized.rsplit("/", 1)[-1].lower()
    lowered = normalized.lower()
    lower_path = lowered if lowered.startswith("/") else f"/{lowered}"
    project_root_name = str(remote_project_root).rstrip("/").rsplit("/", 1)[-1].lower()
    if basename in {".env", ".env.production", ".env.prod", ".env.test"}:
        return "high"
    if basename.endswith((".key", ".pem", ".crt", ".p12", ".db", ".sqlite")):
        return "high"
    if any(marker in lower_path for marker in ("/secrets/", "/secret/", "/certs/", "/certificates/", "/keys/", "/uploads/", "/media/", "/volumes/", "/docker/volumes/")):
        return "high"
    if normalized == project_root_name or lower_path == str(remote_project_root).rstrip("/").lower():
        return "high"
    if any(marker in lower_path for marker in ("/nginx/", "/systemd/", "/release/", "/releases/", "/deploy/", "/scripts/deploy")):
        return "medium"
    return "low"


def sh_quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def capture_metadata(path, remote_project_root):
    stat_result = path.lstat()
    return {
        "original_path": str(path),
        "kind": path_kind(path),
        "size": stat_result.st_size,
        "mode": oct(stat_result.st_mode & 0o7777),
        "uid": stat_result.st_uid,
        "gid": stat_result.st_gid,
        "mtime": int(stat_result.st_mtime),
        "sha256": sha256_file(path),
        "risk": classify_risk(str(path), remote_project_root),
    }


def highest_risk(items):
    order = {"low": 1, "medium": 2, "high": 3}
    result = "low"
    for item in items:
        risk = str(item.get("risk", "low"))
        if order[risk] > order[result]:
            result = risk
    return result


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def archive_paths(request):
    env = request["env"]
    if env not in {"test", "prod"}:
        fail("env must be test or prod")
    archive_root = validate_archive_root(request["remote_archive_root"])
    remote_paths = [validate_remote_absolute_path(path) for path in request["remote_paths"]]
    for path in remote_paths:
        if path == archive_root or path.startswith(f"{archive_root}/"):
            fail(f"refusing to archive path inside archive root: {path!r}")
    preflight = []
    for remote_path in remote_paths:
        local_path = Path(remote_path)
        if not (local_path.exists() or local_path.is_symlink()):
            fail(f"remote path does not exist: {remote_path}")
        preflight.append((remote_path, local_path, capture_metadata(local_path, request["remote_project_root"])))
    batch_dir = remote_join(archive_root, env, f"{batch_timestamp()}-{request['purpose']}")
    payload_dir = remote_join(batch_dir, "payload")
    Path(payload_dir).mkdir(parents=True, exist_ok=False)
    archived_items = []
    for index, (remote_path, local_path, metadata) in enumerate(preflight, start=1):
        destination = remote_join(payload_dir, f"{index:04d}-{local_path.name or 'root'}")
        shutil.move(str(local_path), destination)
        metadata["archived_path"] = destination
        metadata["restore_command"] = f"mkdir -p {sh_quote(os.path.dirname(remote_path))} && mv {sh_quote(destination)} {sh_quote(remote_path)}"
        archived_items.append(metadata)
    manifest = {
        "schema_version": 1,
        "source_mode": request["source_mode"],
        "created_at": current_utc_timestamp(),
        "env": env,
        "purpose": request["purpose"],
        "remote_project_root": request["remote_project_root"],
        "remote_archive_root": archive_root,
        "batch_dir": batch_dir,
        "payload_dir": payload_dir,
        "source_git_ref": request.get("source_git_ref"),
        "plan_sha256": request.get("plan_sha256"),
        "risk_level": highest_risk(archived_items),
        "items": archived_items,
    }
    batch_path = Path(batch_dir)
    write_json(batch_path / "manifest.json", manifest)
    (batch_path / "verify-before.txt").write_text("\n".join(item["original_path"] for item in archived_items) + "\n", encoding="utf-8")
    (batch_path / "verify-after.txt").write_text("\n".join(item["archived_path"] for item in archived_items) + "\n", encoding="utf-8")
    restore_path = batch_path / "restore.sh"
    restore_path.write_text("\n".join(["#!/bin/sh", "set -eu", *(item["restore_command"] for item in archived_items)]) + "\n", encoding="utf-8")
    restore_path.chmod(0o700)
    manifest["manifest_path"] = remote_join(batch_dir, "manifest.json")
    manifest["restore_script"] = remote_join(batch_dir, "restore.sh")
    return manifest


try:
    request = json.load(sys.stdin)
    if request.get("operation") != "archive-paths":
        fail("unsupported operation")
    print(json.dumps(archive_paths(request), ensure_ascii=False, sort_keys=True))
except Exception as exc:
    print(json.dumps({"error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    raise SystemExit(1)
'''


def parse_rsync_deletions(output: str) -> list[str]:
    return [
        line.removeprefix("*deleting ").strip()
        for line in output.splitlines()
        if line.startswith("*deleting ")
    ]


def validate_delete_entry(entry: str) -> str:
    value = entry.strip()
    if value in {"", "/", ".", ".."}:
        raise PathSafetyError(f"refusing unsafe remote path: {entry!r}")
    if value.startswith("/"):
        raise PathSafetyError(f"rsync delete entries must be relative: {entry!r}")
    if any(part == ".." for part in value.split("/")):
        raise PathSafetyError(f"refusing path traversal: {entry!r}")
    if any(ch in value for ch in "*?[]"):
        raise PathSafetyError(f"refusing glob-like remote path: {entry!r}")
    return value


def validate_remote_absolute_path(path: str) -> str:
    value = path.strip()
    if not value.startswith("/"):
        raise PathSafetyError(f"remote path must be absolute: {path!r}")
    if value in {"", "/", ".", ".."}:
        raise PathSafetyError(f"refusing unsafe remote path: {path!r}")
    if any(part == ".." for part in value.split("/")):
        raise PathSafetyError(f"refusing path traversal: {path!r}")
    if any(ch in value for ch in "*?[]"):
        raise PathSafetyError(f"refusing glob-like remote path: {path!r}")
    return value.rstrip("/") if value != "/" else value


def validate_archive_root(path: str) -> str:
    raw_value = path.strip()
    if raw_value == "~":
        raise PathSafetyError("remote archive root cannot be ~")
    if raw_value.startswith("~/"):
        suffix_parts = raw_value[2:].split("/")
        if any(part == ".." for part in suffix_parts):
            raise PathSafetyError(f"refusing path traversal: {path!r}")
        if any(ch in raw_value for ch in "*?[]"):
            raise PathSafetyError(f"refusing glob-like remote path: {path!r}")
        return raw_value.rstrip("/")
    value = validate_remote_absolute_path(raw_value)
    if value == "/":
        raise PathSafetyError("remote archive root cannot be /")
    return value


def remote_archive_root_from_args(args: argparse.Namespace) -> str:
    remote_archive_root = args.remote_archive_root or os.environ.get(REMOTE_ARCHIVE_ROOT_ENV)
    if not remote_archive_root:
        raise UsageError(f"remote archive root requires --remote-archive-root or {REMOTE_ARCHIVE_ROOT_ENV}")
    return validate_archive_root(remote_archive_root)


def map_remote_path(local_remote_root: Path | str, remote_path: str) -> Path:
    safe_remote_path = validate_remote_absolute_path(remote_path)
    relative_path = safe_remote_path.lstrip("/")
    root = Path(local_remote_root).resolve()
    mapped = (root / relative_path).resolve()
    if root != mapped and root not in mapped.parents:
        raise PathSafetyError(f"mapped path escapes fake remote root: {remote_path!r}")
    return mapped


def current_utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def batch_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def normalize_delete_entries(entries: list[str]) -> list[str]:
    validated = sorted(validate_delete_entry(entry) for entry in entries)
    normalized: list[str] = []
    directory_prefixes: list[str] = []

    for entry in validated:
        if any(entry == prefix.rstrip("/") or entry.startswith(prefix) for prefix in directory_prefixes):
            continue
        normalized.append(entry)
        if entry.endswith("/"):
            directory_prefixes.append(entry)
    return normalized


def classify_risk(path: str, remote_project_root: str) -> str:
    normalized = path.strip().rstrip("/")
    basename = normalized.rsplit("/", 1)[-1].lower()
    lowered = normalized.lower()
    lower_path = lowered if lowered.startswith("/") else f"/{lowered}"
    project_root_name = remote_project_root.rstrip("/").rsplit("/", 1)[-1].lower()

    high_names = {".env", ".env.production", ".env.prod", ".env.test"}
    high_markers = ("/secrets/", "/secret/", "/certs/", "/certificates/", "/keys/", "/uploads/", "/media/", "/volumes/", "/docker/volumes/")
    if basename in high_names or basename.endswith((".key", ".pem", ".crt", ".p12", ".db", ".sqlite")):
        return "high"
    if any(marker in lower_path for marker in high_markers):
        return "high"
    if normalized == project_root_name or lower_path == remote_project_root.rstrip("/").lower():
        return "high"

    medium_markers = ("/nginx/", "/systemd/", "/release/", "/releases/", "/deploy/", "/scripts/deploy")
    if any(marker in lower_path for marker in medium_markers):
        return "medium"
    return "low"


def highest_risk(items: list[dict[str, object]]) -> str:
    result = "low"
    for item in items:
        risk = str(item.get("risk", "low"))
        if RISK_ORDER[risk] > RISK_ORDER[result]:
            result = risk
    return result


def ensure_high_risk_confirmations(
    *,
    remote_paths: list[str],
    remote_project_root: str,
    confirm_high_risk: list[str] | None = None,
) -> None:
    confirmed = set(confirm_high_risk or [])
    missing = [
        path
        for path in remote_paths
        if classify_risk(path, remote_project_root) == "high" and path not in confirmed
    ]
    if missing:
        raise UsageError(f"high-risk paths require exact confirmation: {', '.join(missing)}")


def canonical_plan_hash(plan: dict[str, object]) -> str:
    hashable = {key: value for key, value in plan.items() if key != "plan_sha256"}
    encoded = json.dumps(hashable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    if env == "prod" and not source_git_ref:
        raise UsageError("prod requires source_git_ref")

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


def path_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    return "file"


def sha256_file(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_metadata(local_path: Path, remote_path: str, remote_project_root: str) -> dict[str, object]:
    stat_result = local_path.lstat()
    return {
        "original_path": remote_path,
        "kind": path_kind(local_path),
        "size": stat_result.st_size,
        "mode": oct(stat_result.st_mode & 0o7777),
        "uid": stat_result.st_uid,
        "gid": stat_result.st_gid,
        "mtime": int(stat_result.st_mtime),
        "sha256": sha256_file(local_path),
        "risk": classify_risk(remote_path, remote_project_root),
    }


def remote_join(root: str, *parts: str) -> str:
    return "/".join([root.rstrip("/"), *(part.strip("/") for part in parts if part)])


def write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def archive_items_local(
    *,
    local_remote_root: Path | str,
    remote_paths: list[str],
    env: str,
    remote_project_root: str,
    remote_archive_root: str,
    purpose: str,
    source_mode: str,
    source_git_ref: str | None = None,
    plan_sha256: str | None = None,
    confirm_high_risk: list[str] | None = None,
) -> dict[str, object]:
    if env not in {"test", "prod"}:
        raise UsageError("env must be test or prod")
    safe_archive_root = validate_archive_root(remote_archive_root)

    preflight: list[tuple[str, Path, dict[str, object]]] = []
    for remote_path in remote_paths:
        safe_remote_path = validate_remote_absolute_path(remote_path)
        if safe_remote_path == safe_archive_root or safe_remote_path.startswith(f"{safe_archive_root}/"):
            raise PathSafetyError(f"refusing to archive path inside archive root: {remote_path!r}")
        local_path = map_remote_path(local_remote_root, safe_remote_path)
        if not (local_path.exists() or local_path.is_symlink()):
            raise UsageError(f"remote path does not exist in fake root: {safe_remote_path}")
        preflight.append((safe_remote_path, local_path, capture_metadata(local_path, safe_remote_path, remote_project_root)))

    ensure_high_risk_confirmations(
        remote_paths=[remote_path for remote_path, _, _ in preflight],
        remote_project_root=remote_project_root,
        confirm_high_risk=confirm_high_risk,
    )

    batch_name = f"{batch_timestamp()}-{purpose}"
    remote_batch_dir = remote_join(safe_archive_root, env, batch_name)
    remote_payload_dir = remote_join(remote_batch_dir, "payload")
    local_batch_dir = map_remote_path(local_remote_root, remote_batch_dir)
    local_payload_dir = map_remote_path(local_remote_root, remote_payload_dir)
    local_payload_dir.mkdir(parents=True, exist_ok=False)

    archived_items: list[dict[str, object]] = []
    for index, (safe_remote_path, local_path, metadata) in enumerate(preflight, start=1):
        destination_name = f"{index:04d}-{local_path.name or 'root'}"
        local_destination = local_payload_dir / destination_name
        remote_destination = remote_join(remote_payload_dir, destination_name)
        shutil.move(str(local_path), str(local_destination))
        metadata["archived_path"] = remote_destination
        metadata["restore_command"] = f"mkdir -p {sh_quote(os.path.dirname(safe_remote_path))} && mv {sh_quote(remote_destination)} {sh_quote(safe_remote_path)}"
        archived_items.append(metadata)

    manifest: dict[str, object] = {
        "schema_version": 1,
        "source_mode": source_mode,
        "created_at": current_utc_timestamp(),
        "env": env,
        "purpose": purpose,
        "remote_project_root": remote_project_root,
        "remote_archive_root": safe_archive_root,
        "batch_dir": remote_batch_dir,
        "payload_dir": remote_payload_dir,
        "source_git_ref": source_git_ref,
        "plan_sha256": plan_sha256,
        "risk_level": highest_risk(archived_items),
        "items": archived_items,
    }
    write_json(local_batch_dir / "manifest.json", manifest)
    (local_batch_dir / "verify-before.txt").write_text(
        "\n".join(str(item["original_path"]) for item in archived_items) + "\n",
        encoding="utf-8",
    )
    (local_batch_dir / "verify-after.txt").write_text(
        "\n".join(str(item["archived_path"]) for item in archived_items) + "\n",
        encoding="utf-8",
    )
    restore_lines = ["#!/bin/sh", "set -eu"] + [str(item["restore_command"]) for item in archived_items]
    restore_path = local_batch_dir / "restore.sh"
    restore_path.write_text("\n".join(restore_lines) + "\n", encoding="utf-8")
    restore_path.chmod(0o700)

    manifest["manifest_path"] = remote_join(remote_batch_dir, "manifest.json")
    manifest["restore_script"] = remote_join(remote_batch_dir, "restore.sh")
    return manifest


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def archive_explicit_path_local(
    *,
    local_remote_root: Path | str,
    remote_path: str,
    env: str,
    remote_project_root: str,
    remote_archive_root: str,
    purpose: str,
    source_git_ref: str | None = None,
    confirm_high_risk: list[str] | None = None,
) -> dict[str, object]:
    if env == "prod" and not source_git_ref:
        raise UsageError("prod requires source_git_ref")
    return archive_items_local(
        local_remote_root=local_remote_root,
        remote_paths=[remote_path],
        env=env,
        remote_project_root=remote_project_root,
        remote_archive_root=remote_archive_root,
        purpose=purpose,
        source_mode="explicit-path",
        source_git_ref=source_git_ref,
        confirm_high_risk=confirm_high_risk,
    )


def build_rsync_dry_run_command(source: str, destination: str) -> list[str]:
    return ["rsync", "--dry-run", "--delete", "--itemize-changes", source, destination]


def run_subprocess(command: list[str], *, input_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, input=input_text, capture_output=True, text=True)


def archive_paths_ssh(
    *,
    ssh_target: str,
    remote_paths: list[str],
    env: str,
    remote_project_root: str,
    remote_archive_root: str,
    purpose: str,
    source_mode: str,
    source_git_ref: str | None = None,
    plan_sha256: str | None = None,
    confirm_high_risk: list[str] | None = None,
    runner=run_subprocess,
) -> dict[str, object]:
    if env not in {"test", "prod"}:
        raise UsageError("env must be test or prod")
    if env == "prod" and not source_git_ref:
        raise UsageError("prod requires source_git_ref")
    safe_archive_root = validate_archive_root(remote_archive_root)
    safe_paths = [validate_remote_absolute_path(path) for path in remote_paths]
    for path in safe_paths:
        if path == safe_archive_root or path.startswith(f"{safe_archive_root}/"):
            raise PathSafetyError(f"refusing to archive path inside archive root: {path!r}")
    safe_project_root = validate_remote_absolute_path(remote_project_root)
    ensure_high_risk_confirmations(
        remote_paths=safe_paths,
        remote_project_root=safe_project_root,
        confirm_high_risk=confirm_high_risk,
    )

    request = {
        "operation": "archive-paths",
        "remote_paths": safe_paths,
        "env": env,
        "remote_project_root": safe_project_root,
        "remote_archive_root": safe_archive_root,
        "purpose": purpose,
        "source_mode": source_mode,
        "source_git_ref": source_git_ref,
        "plan_sha256": plan_sha256,
    }
    command = ["ssh", ssh_target, "python3", "-c", REMOTE_PAYLOAD_BOOTSTRAP]
    completed = runner(command, input_text=json.dumps(request, ensure_ascii=False, sort_keys=True))
    if completed.returncode != 0:
        raise UsageError(completed.stderr.strip() or "remote archive command failed")
    return json.loads(completed.stdout)


def archive_explicit_path_ssh(
    *,
    ssh_target: str,
    remote_path: str,
    env: str,
    remote_project_root: str,
    remote_archive_root: str,
    purpose: str,
    source_git_ref: str | None = None,
    confirm_high_risk: list[str] | None = None,
    runner=run_subprocess,
) -> dict[str, object]:
    return archive_paths_ssh(
        ssh_target=ssh_target,
        remote_paths=[remote_path],
        env=env,
        remote_project_root=remote_project_root,
        remote_archive_root=remote_archive_root,
        purpose=purpose,
        source_mode="explicit-path",
        source_git_ref=source_git_ref,
        confirm_high_risk=confirm_high_risk,
        runner=runner,
    )


def plan_item_remote_path(plan: dict[str, object], item: dict[str, object]) -> str:
    path = str(item["path"])
    if path.startswith("/"):
        return validate_remote_absolute_path(path)
    return validate_remote_absolute_path(remote_join(str(plan["remote_project_root"]), path))


def high_risk_paths(plan: dict[str, object]) -> list[str]:
    result: list[str] = []
    for item in plan.get("items", []):
        if not isinstance(item, dict):
            raise UsageError("plan items must be objects")
        remote_path = plan_item_remote_path(plan, item)
        risk = str(item.get("risk") or classify_risk(remote_path, str(plan["remote_project_root"])))
        if risk == "high":
            result.append(remote_path)
    return result


def ensure_environment_gates(
    *,
    plan: dict[str, object],
    confirm_plan: str | None = None,
    confirm_high_risk: list[str] | None = None,
) -> None:
    env = str(plan.get("env", ""))
    if env not in {"test", "prod"}:
        raise UsageError("plan env must be test or prod")
    if env == "prod":
        if not plan.get("source_git_ref"):
            raise UsageError("prod requires source_git_ref")
        expected_hash = str(plan.get("plan_sha256", ""))
        if not confirm_plan:
            raise UsageError("prod archive-list requires confirm_plan")
        if confirm_plan != expected_hash:
            raise UsageError("confirm_plan does not match plan_sha256")

    confirmed = set(confirm_high_risk or [])
    missing = [path for path in high_risk_paths(plan) if path not in confirmed]
    if missing:
        raise UsageError(f"high-risk paths require exact confirmation: {', '.join(missing)}")


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
        source_mode="rsync-delete-plan",
        source_git_ref=str(plan["source_git_ref"]) if plan.get("source_git_ref") else None,
        plan_sha256=str(plan.get("plan_sha256", "")),
        confirm_high_risk=confirm_high_risk,
    )


def add_environment_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env", choices=["test", "prod"], required=True)
    parser.add_argument("--remote-project-root", required=True)
    parser.add_argument("--remote-archive-root")
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--source-git-ref")


def print_json(data: dict[str, object]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def command_plan_rsync_delete(args: argparse.Namespace) -> int:
    remote_archive_root = remote_archive_root_from_args(args)
    if args.dry_run_output:
        dry_run_output = Path(args.dry_run_output).read_text(encoding="utf-8")
    elif args.rsync_source and args.rsync_destination:
        command = build_rsync_dry_run_command(args.rsync_source, args.rsync_destination)
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise UsageError(completed.stderr.strip() or "rsync dry run failed")
        dry_run_output = completed.stdout
    else:
        raise UsageError("plan-rsync-delete requires --dry-run-output or --rsync-source with --rsync-destination")
    plan = build_rsync_delete_plan(
        dry_run_output=dry_run_output,
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
        print(f"planned {len(plan['items'])} remote delete item(s)")
        print(f"plan_sha256: {plan['plan_sha256']}")
    return 0


def command_archive_path(args: argparse.Namespace) -> int:
    remote_archive_root = remote_archive_root_from_args(args)
    if args.local_remote_root:
        result = archive_explicit_path_local(
            local_remote_root=Path(args.local_remote_root),
            remote_path=args.remote_path,
            env=args.env,
            remote_project_root=args.remote_project_root,
            remote_archive_root=remote_archive_root,
            purpose=args.purpose,
            source_git_ref=args.source_git_ref,
            confirm_high_risk=args.confirm_high_risk or [],
        )
    elif args.ssh_target:
        result = archive_explicit_path_ssh(
            ssh_target=args.ssh_target,
            remote_path=args.remote_path,
            env=args.env,
            remote_project_root=args.remote_project_root,
            remote_archive_root=remote_archive_root,
            purpose=args.purpose,
            source_git_ref=args.source_git_ref,
            confirm_high_risk=args.confirm_high_risk or [],
        )
    else:
        raise UsageError("archive-path requires --local-remote-root or --ssh-target")
    if args.json:
        print_json(result)
    else:
        print(f"archived {len(result['items'])} remote item(s)")
        print(f"manifest: {result['manifest_path']}")
        print(f"restore: {result['restore_script']}")
    return 0


def command_archive_list(args: argparse.Namespace) -> int:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    ensure_environment_gates(plan=plan, confirm_plan=args.confirm_plan, confirm_high_risk=args.confirm_high_risk or [])
    if args.local_remote_root:
        result = archive_plan_local(
            local_remote_root=Path(args.local_remote_root),
            plan=plan,
            confirm_plan=args.confirm_plan,
            confirm_high_risk=args.confirm_high_risk or [],
        )
    elif args.ssh_target:
        result = archive_paths_ssh(
            ssh_target=args.ssh_target,
            remote_paths=[plan_item_remote_path(plan, item) for item in plan.get("items", []) if isinstance(item, dict)],
            env=str(plan["env"]),
            remote_project_root=str(plan["remote_project_root"]),
            remote_archive_root=str(plan["remote_archive_root"]),
            purpose=str(plan["purpose"]),
            source_mode="rsync-delete-plan",
            source_git_ref=str(plan["source_git_ref"]) if plan.get("source_git_ref") else None,
            plan_sha256=str(plan.get("plan_sha256", "")),
        )
    else:
        raise UsageError("archive-list requires --local-remote-root or --ssh-target")
    if args.json:
        print_json(result)
    else:
        print(f"archived {len(result['items'])} remote item(s)")
        print(f"manifest: {result['manifest_path']}")
        print(f"restore: {result['restore_script']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="remote-safe-delete.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan-rsync-delete")
    plan_parser.add_argument("--dry-run-output")
    plan_parser.add_argument("--rsync-source")
    plan_parser.add_argument("--rsync-destination")
    plan_parser.add_argument("--output")
    plan_parser.add_argument("--json", action="store_true")
    add_environment_args(plan_parser)
    plan_parser.set_defaults(func=command_plan_rsync_delete)

    archive_path_parser = subparsers.add_parser("archive-path")
    archive_path_parser.add_argument("--local-remote-root")
    archive_path_parser.add_argument("--ssh-target")
    archive_path_parser.add_argument("--remote-path", required=True)
    archive_path_parser.add_argument("--confirm-high-risk", action="append")
    archive_path_parser.add_argument("--json", action="store_true")
    add_environment_args(archive_path_parser)
    archive_path_parser.set_defaults(func=command_archive_path)

    archive_list_parser = subparsers.add_parser("archive-list")
    archive_list_parser.add_argument("--local-remote-root")
    archive_list_parser.add_argument("--ssh-target")
    archive_list_parser.add_argument("--plan", required=True)
    archive_list_parser.add_argument("--confirm-plan")
    archive_list_parser.add_argument("--confirm-high-risk", action="append")
    archive_list_parser.add_argument("--json", action="store_true")
    archive_list_parser.set_defaults(func=command_archive_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, PathSafetyError, UsageError, json.JSONDecodeError) as exc:
        print(f"remote-safe-delete failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
