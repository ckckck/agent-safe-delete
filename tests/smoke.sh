#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLI="$ROOT_DIR/scripts/agent-safe-delete.sh"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

export ASD_SAFE_ARCHIVE_ROOT="$tmpdir/archive-root"
workspace="$tmpdir/workspace"
mkdir -p "$workspace"
metadata_dir="$ASD_SAFE_ARCHIVE_ROOT/.agent-safe-delete"

show_root="$($CLI show-archive-root)"
[ "$show_root" = "$ASD_SAFE_ARCHIVE_ROOT" ]

file_path="$workspace/example.txt"
printf 'hello\n' > "$file_path"

archive_file_json="$($CLI archive "$file_path" --json)"
file_entry_id="$(printf '%s' "$archive_file_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
file_archived_path="$(printf '%s' "$archive_file_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["archived_path"])')"
file_metadata_path="$(printf '%s' "$archive_file_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["metadata_path"])')"

[ ! -e "$file_path" ]
[ -f "$file_archived_path" ]
[ -f "$file_metadata_path" ]
[ ! -d "$ASD_SAFE_ARCHIVE_ROOT/entries" ]
[ ! -d "$ASD_SAFE_ARCHIVE_ROOT/payload" ]
[ -d "$metadata_dir" ]

manifest_original_path="$(python3 - "$file_metadata_path" <<'PY'
import json
import sys

with open(sys.argv[1], 'r', encoding='utf-8') as handle:
    data = json.load(handle)
print(data['original_path'])
PY
)"
[ "$manifest_original_path" = "$file_path" ]

manifest_archived_path="$(python3 - "$file_metadata_path" <<'PY'
import json
import sys

with open(sys.argv[1], 'r', encoding='utf-8') as handle:
    data = json.load(handle)
print(data['archived_path'])
PY
)"
[ "$manifest_archived_path" = "$file_archived_path" ]

$CLI restore "$file_entry_id" >/dev/null
[ -f "$file_path" ]
grep -q '^hello$' "$file_path"

restore_status="$(python3 - "$file_metadata_path" <<'PY'
import json
import sys

with open(sys.argv[1], 'r', encoding='utf-8') as handle:
    data = json.load(handle)
print(data['restore_status'])
PY
)"
[ "$restore_status" = "restored" ]

dir_path="$workspace/example-dir"
mkdir -p "$dir_path/nested"
printf 'world\n' > "$dir_path/nested/value.txt"

archive_dir_json="$($CLI archive "$dir_path" --json)"
dir_entry_id="$(printf '%s' "$archive_dir_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
dir_archived_path="$(printf '%s' "$archive_dir_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["archived_path"])')"
dir_metadata_path="$(printf '%s' "$archive_dir_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["metadata_path"])')"
[ ! -e "$dir_path" ]
[ -d "$dir_archived_path" ]
[ -f "$dir_metadata_path" ]

$CLI restore "$dir_entry_id" >/dev/null
[ -f "$dir_path/nested/value.txt" ]
grep -q '^world$' "$dir_path/nested/value.txt"

orphan_dir="$workspace/orphan-dir"
mkdir -p "$orphan_dir"
orphan_json="$($CLI archive "$orphan_dir" --json)"
orphan_metadata_path="$(printf '%s' "$orphan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["metadata_path"])')"
orphan_archived_path="$(printf '%s' "$orphan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["archived_path"])')"
[ -e "$orphan_archived_path" ]
[ -f "$orphan_metadata_path" ]
rm -rf "$orphan_archived_path"

$CLI show-archive-root >/dev/null
[ ! -e "$orphan_metadata_path" ]

echo "smoke test passed"
