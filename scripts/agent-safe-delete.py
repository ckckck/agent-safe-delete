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


def absolute_path(path_value: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(path_value)))


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
    if configured:
        return absolute_path(configured)
    return default_archive_root()


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
    suffix = "".join(target_path.suffixes)
    stem = target_path.name[:-len(suffix)] if suffix else target_path.name

    if target_path.is_dir():
        candidate = target_path.with_name(f"{target_path.name}-{timestamp}")
    else:
        candidate = target_path.with_name(f"{stem}-{timestamp}{suffix}")

    counter = 1
    while path_exists_or_link(candidate):
        counter += 1
        if target_path.is_dir():
            candidate = target_path.with_name(f"{target_path.name}-{timestamp}-{counter}")
        else:
            candidate = target_path.with_name(f"{stem}-{timestamp}-{counter}{suffix}")
    return candidate


def write_metadata(metadata_path: Path, data: dict[str, object]) -> None:
    metadata_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_metadata(metadata_path: Path) -> dict[str, object]:
    with metadata_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def prune_stale_metadata(archive_root: Path) -> None:
    ensure_archive_layout(archive_root)
    for json_path in metadata_dir(archive_root).glob("*.json"):
        try:
            data = read_metadata(json_path)
        except Exception:
            continue

        if data.get("restore_status") != "archived":
            continue

        archived_value = data.get("archived_path")
        if not isinstance(archived_value, str) or not archived_value:
            continue

        archived_path = Path(archived_value)
        if not path_exists_or_link(archived_path):
            json_path.unlink()


def classify_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
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
    source_path = absolute_path(source_input)

    if not path_exists_or_link(source_path):
        error(f"归档失败：路径不存在：{source_input}")
    if source_path == archive_root:
        error(f"归档失败：不能归档归档根目录：{source_path}")
    if source_path == metadata_dir(archive_root):
        error(f"归档失败：不能归档元数据目录：{source_path}")
    if archive_root in source_path.parents:
        error(f"归档失败：目标已经位于归档目录中：{source_path}")

    ensure_archive_layout(archive_root)

    entry_id = generate_entry_id()
    archived_at = current_utc_timestamp()
    destination_path = archive_root / source_path.name
    if path_exists_or_link(destination_path):
        destination_path = append_timestamp_path(destination_path)

    shutil.move(str(source_path), str(destination_path))

    kind = classify_kind(destination_path)
    metadata_path = metadata_dir(archive_root) / f"{entry_id}.json"
    metadata = {
        "schema_version": 2,
        "id": entry_id,
        "archived_at": archived_at,
        "original_path": str(source_path),
        "archived_path": str(destination_path),
        "archived_name": destination_path.name,
        "kind": kind,
        "safe_archive_root": str(archive_root),
        "restore_status": "archived",
    }
    write_metadata(metadata_path, metadata)

    if json_output:
        print(
            json.dumps(
                {
                    "action": "archive",
                    "id": entry_id,
                    "original_path": str(source_path),
                    "archived_path": str(destination_path),
                    "metadata_path": str(metadata_path),
                    "safe_archive_root": str(archive_root),
                    "kind": kind,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
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

    metadata = read_metadata(metadata_path)
    archived_path = Path(str(metadata["archived_path"]))
    if not path_exists_or_link(archived_path):
        error(f"恢复失败：归档对象不存在：{archived_path}")

    if restore_to:
        target_path = absolute_path(restore_to)
    else:
        target_path = Path(str(metadata["original_path"]))

    if path_exists_or_link(target_path):
        error(f"恢复失败：目标路径已存在：{target_path}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(archived_path), str(target_path))

    metadata["restored_at"] = current_utc_timestamp()
    metadata["restored_to"] = str(target_path)
    metadata["restore_status"] = "restored"
    write_metadata(metadata_path, metadata)

    if json_output:
        print(
            json.dumps(
                {
                    "action": "restore",
                    "id": entry_id,
                    "restored_to": str(target_path),
                    "metadata_path": str(metadata_path),
                    "kind": metadata["kind"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
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
