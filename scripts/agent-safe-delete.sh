#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
用法:
  agent-safe-delete.sh show-archive-root [--json]
  agent-safe-delete.sh archive <path> [--json]
  agent-safe-delete.sh restore <entry-id> [--to <path>] [--json]
EOF
}

error() {
  echo "$1" >&2
  exit 1
}

require_python() {
  command -v python3 >/dev/null 2>&1 || error "需要 python3 才能运行此脚本。"
}

default_archive_root() {
  local platform
  platform="$(uname -s)"

  case "$platform" in
    Darwin)
      printf '%s\n' "$HOME/Library/Application Support/agent-safe-delete/safe-archive"
      ;;
    Linux)
      if [ -n "${XDG_DATA_HOME:-}" ]; then
        printf '%s\n' "$XDG_DATA_HOME/agent-safe-delete/safe-archive"
      else
        printf '%s\n' "$HOME/.local/share/agent-safe-delete/safe-archive"
      fi
      ;;
    MINGW*|MSYS*|CYGWIN*)
      if [ -n "${LOCALAPPDATA:-}" ]; then
        printf '%s\n' "$LOCALAPPDATA/agent-safe-delete/safe-archive"
      else
        printf '%s\n' "$HOME/AppData/Local/agent-safe-delete/safe-archive"
      fi
      ;;
    *)
      printf '%s\n' "$HOME/.local/share/agent-safe-delete/safe-archive"
      ;;
  esac
}

abspath() {
  require_python
  python3 - "$1" <<'PY'
import os
import sys

print(os.path.abspath(os.path.expanduser(sys.argv[1])))
PY
}

current_utc_timestamp() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

generate_entry_id() {
  require_python
  local stamp
  stamp="$(date -u '+%Y%m%d-%H%M%S')"
  python3 - "$stamp" <<'PY'
import sys
import uuid

print(f"ASD-{sys.argv[1]}-{uuid.uuid4().hex[:8]}")
PY
}

safe_archive_root_raw="${ASD_SAFE_ARCHIVE_ROOT:-}"
if [ -z "$safe_archive_root_raw" ]; then
  safe_archive_root_raw="$(default_archive_root)"
fi
safe_archive_root="$(abspath "$safe_archive_root_raw")"
metadata_dir="$safe_archive_root/.agent-safe-delete"

ensure_archive_layout() {
  mkdir -p "$safe_archive_root"
  mkdir -p "$metadata_dir"
}

metadata_path_for_id() {
  printf '%s\n' "$metadata_dir/$1.json"
}

append_timestamp_path() {
  local target_path="$1"
  local base_name
  local dir_name
  local timestamp
  local stem
  local ext
  local candidate
  local counter=1

  dir_name="$(dirname "$target_path")"
  base_name="$(basename "$target_path")"
  timestamp="$(date '+%Y%m%d-%H%M%S')"

  if [ -d "$target_path" ]; then
    candidate="$dir_name/${base_name}-${timestamp}"
  else
    case "$base_name" in
      .*.*)
        stem="${base_name%.*}"
        ext=".${base_name##*.}"
        ;;
      *.*)
        stem="${base_name%.*}"
        ext=".${base_name##*.}"
        ;;
      *)
        stem="$base_name"
        ext=""
        ;;
    esac

    candidate="$dir_name/${stem}-${timestamp}${ext}"
  fi

  while [ -e "$candidate" ]; do
    counter=$((counter + 1))
    if [ -d "$target_path" ]; then
      candidate="$dir_name/${base_name}-${timestamp}-${counter}"
    else
      candidate="$dir_name/${stem}-${timestamp}-${counter}${ext}"
    fi
  done

  printf '%s\n' "$candidate"
}

print_json() {
  require_python
  python3 - "$@" <<'PY'
import json
import sys

keys = sys.argv[1::2]
values = sys.argv[2::2]
print(json.dumps(dict(zip(keys, values)), ensure_ascii=False, indent=2))
PY
}

write_metadata() {
  require_python
  local metadata_path="$1"
  local entry_id="$2"
  local archived_at="$3"
  local original_path="$4"
  local archived_path="$5"
  local kind="$6"
  local archive_root="$7"
  local archived_name="$8"

  python3 - "$metadata_path" "$entry_id" "$archived_at" "$original_path" "$archived_path" "$kind" "$archive_root" "$archived_name" <<'PY'
import json
import sys

metadata_path, entry_id, archived_at, original_path, archived_path, kind, archive_root, archived_name = sys.argv[1:]
data = {
    "schema_version": 2,
    "id": entry_id,
    "archived_at": archived_at,
    "original_path": original_path,
    "archived_path": archived_path,
    "archived_name": archived_name,
    "kind": kind,
    "safe_archive_root": archive_root,
    "restore_status": "archived",
}
with open(metadata_path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
}

read_metadata_field() {
  require_python
  local metadata_path="$1"
  local field_name="$2"

  python3 - "$metadata_path" "$field_name" <<'PY'
import json
import sys

metadata_path, field_name = sys.argv[1:]
with open(metadata_path, "r", encoding="utf-8") as handle:
    data = json.load(handle)
value = data.get(field_name)
if value is None:
    sys.exit(1)
print(value)
PY
}

update_metadata_restore() {
  require_python
  local metadata_path="$1"
  local restored_at="$2"
  local restored_to="$3"

  python3 - "$metadata_path" "$restored_at" "$restored_to" <<'PY'
import json
import sys

metadata_path, restored_at, restored_to = sys.argv[1:]
with open(metadata_path, "r", encoding="utf-8") as handle:
    data = json.load(handle)
data["restored_at"] = restored_at
data["restored_to"] = restored_to
data["restore_status"] = "restored"
with open(metadata_path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
}

prune_stale_metadata() {
  require_python
  ensure_archive_layout
  python3 - "$metadata_dir" <<'PY'
import json
import os
import sys

metadata_dir = sys.argv[1]
if not os.path.isdir(metadata_dir):
    sys.exit(0)

for name in os.listdir(metadata_dir):
    if not name.endswith('.json'):
        continue
    path = os.path.join(metadata_dir, name)
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except Exception:
        continue
    if data.get('restore_status') != 'archived':
        continue
    archived_path = data.get('archived_path')
    if not archived_path:
        continue
    if not os.path.exists(archived_path):
        os.remove(path)
PY
}

show_archive_root() {
  local json_output=0

  prune_stale_metadata

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --json)
        json_output=1
        ;;
      *)
        usage
        exit 1
        ;;
    esac
    shift
  done

  if [ "$json_output" -eq 1 ]; then
    print_json safe_archive_root "$safe_archive_root"
  else
    printf '%s\n' "$safe_archive_root"
  fi
}

archive_path() {
  local json_output=0
  local source_input=""

  prune_stale_metadata

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --json)
        json_output=1
        ;;
      --*)
        usage
        exit 1
        ;;
      *)
        if [ -n "$source_input" ]; then
          usage
          exit 1
        fi
        source_input="$1"
        ;;
    esac
    shift
  done

  [ -n "$source_input" ] || error "归档失败：缺少要归档的路径。"

  local source_path
  source_path="$(abspath "$source_input")"

  if [ ! -e "$source_path" ]; then
    error "归档失败：路径不存在：$source_input"
  fi

  if [ "$source_path" = "$safe_archive_root" ]; then
    error "归档失败：不能归档归档根目录：$source_path"
  fi

  case "$source_path" in
    "$safe_archive_root"|"$safe_archive_root"/*)
      error "归档失败：目标已经位于归档目录中：$source_path"
      ;;
  esac

  if [ "$source_path" = "$metadata_dir" ]; then
    error "归档失败：不能归档元数据目录：$source_path"
  fi

  ensure_archive_layout

  local entry_id archived_at archived_name destination_path metadata_path kind

  entry_id="$(generate_entry_id)"
  archived_at="$(current_utc_timestamp)"
  archived_name="$(basename "$source_path")"
  destination_path="$safe_archive_root/$archived_name"
  metadata_path="$(metadata_path_for_id "$entry_id")"

  if [ -d "$source_path" ]; then
    kind="directory"
  else
    kind="file"
  fi

  if [ -e "$destination_path" ]; then
    destination_path="$(append_timestamp_path "$destination_path")"
  fi

  archived_name="$(basename "$destination_path")"
  mv "$source_path" "$destination_path"

  write_metadata "$metadata_path" "$entry_id" "$archived_at" "$source_path" "$destination_path" "$kind" "$safe_archive_root" "$archived_name"

  if [ "$json_output" -eq 1 ]; then
    require_python
    python3 - "$entry_id" "$source_path" "$destination_path" "$metadata_path" "$safe_archive_root" "$kind" <<'PY'
import json
import sys

entry_id, original_path, archived_path, metadata_path, safe_archive_root, kind = sys.argv[1:]
print(json.dumps({
    "action": "archive",
    "id": entry_id,
    "original_path": original_path,
    "archived_path": archived_path,
    "metadata_path": metadata_path,
    "safe_archive_root": safe_archive_root,
    "kind": kind,
}, ensure_ascii=False, indent=2))
PY
  else
    echo "已归档: $source_path"
    echo "Entry ID: $entry_id"
    echo "归档位置: $destination_path"
  fi
}

restore_path() {
  local json_output=0
  local entry_id=""
  local restore_to=""

  prune_stale_metadata

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --json)
        json_output=1
        shift
        ;;
      --to)
        shift
        [ "$#" -gt 0 ] || error "恢复失败：--to 需要一个路径参数。"
        restore_to="$1"
        shift
        ;;
      --*)
        usage
        exit 1
        ;;
      *)
        if [ -n "$entry_id" ]; then
          usage
          exit 1
        fi
        entry_id="$1"
        shift
        ;;
    esac
  done

  [ -n "$entry_id" ] || error "恢复失败：缺少 entry-id。"

  local metadata_path original_path archived_path target_path restored_at kind

  metadata_path="$(metadata_path_for_id "$entry_id")"
  [ -f "$metadata_path" ] || error "恢复失败：找不到 metadata：$metadata_path"

  original_path="$(read_metadata_field "$metadata_path" original_path)"
  archived_path="$(read_metadata_field "$metadata_path" archived_path)"
  kind="$(read_metadata_field "$metadata_path" kind)"

  [ -e "$archived_path" ] || error "恢复失败：归档对象不存在：$archived_path"

  if [ -n "$restore_to" ]; then
    target_path="$(abspath "$restore_to")"
  else
    target_path="$original_path"
  fi

  if [ -e "$target_path" ]; then
    error "恢复失败：目标路径已存在：$target_path"
  fi

  mkdir -p "$(dirname "$target_path")"
  mv "$archived_path" "$target_path"

  restored_at="$(current_utc_timestamp)"
  update_metadata_restore "$metadata_path" "$restored_at" "$target_path"

  if [ "$json_output" -eq 1 ]; then
    require_python
    python3 - "$entry_id" "$target_path" "$metadata_path" "$kind" <<'PY'
import json
import sys

entry_id, restored_to, metadata_path, kind = sys.argv[1:]
print(json.dumps({
    "action": "restore",
    "id": entry_id,
    "restored_to": restored_to,
    "metadata_path": metadata_path,
    "kind": kind,
}, ensure_ascii=False, indent=2))
PY
  else
    echo "已恢复 Entry: $entry_id"
    echo "恢复位置: $target_path"
  fi
}

main() {
  [ "$#" -gt 0 ] || {
    usage
    exit 1
  }

  local command="$1"
  shift

  case "$command" in
    show-archive-root)
      show_archive_root "$@"
      ;;
    archive)
      archive_path "$@"
      ;;
    restore)
      restore_path "$@"
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
