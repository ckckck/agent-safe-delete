# agent-safe-delete

[中文](README.md)

> Protect users from destructive AI agent file operations by turning delete into reversible archive moves.

`agent-safe-delete` is a safe-delete skill for AI agents. When an agent needs to delete a file or directory, it does not perform irreversible permanent deletion. Instead, it moves the target into a recoverable archive area and stores restore metadata as JSON files inside a hidden metadata directory.

## Core Idea

- Delete semantics are rewritten into reversible archive moves.
- It intercepts not only explicit user deletion requests, but also deletion, replacement, and cleanup actions inferred during agent execution.
- The archive root is controlled by a single environment variable: `ASD_SAFE_ARCHIVE_ROOT`.
- If the variable is not set, a platform default path is used.
- Archived objects keep their original names whenever possible and are placed directly in the archive root; timestamp suffixes are added only on collisions.
- Metadata is stored in a hidden `.agent-safe-delete/` directory under the archive root.
- `restore` can move files or directories back to their original paths or to a user-specified location.
- Stale metadata is automatically pruned before each command runs.
- Explicit deletion of ordinary files or directories is archived directly; only high-risk deletions require an extra confirmation.

## Default Archive Locations

- macOS: `~/Library/Application Support/agent-safe-delete/safe-archive`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/agent-safe-delete/safe-archive`
- Windows: `%LOCALAPPDATA%\\agent-safe-delete\\safe-archive`

Override with an environment variable:

```bash
export ASD_SAFE_ARCHIVE_ROOT="$HOME/Library/Application Support/agent-safe-delete/safe-archive"
```

## Repository Layout

```text
agent-safe-delete/
  .gitignore
  LICENSE
  README.md
  README.en.md
  SKILL.md
  scripts/
    agent-safe-delete.sh
  tests/
    smoke.sh
```

## Usage

### 1. Use as a skill

Place the repository in a skill-discoverable location and have the skill invoke `scripts/agent-safe-delete.sh`.

This skill is not designed only for cases where the user explicitly says “delete”. It is designed to take over deletion semantics whenever an agent is about to remove, replace, or clean up filesystem objects.

### 2. Use as a CLI tool

```bash
./scripts/agent-safe-delete.sh show-archive-root
./scripts/agent-safe-delete.sh archive ./example.txt
./scripts/agent-safe-delete.sh archive ./build --json
./scripts/agent-safe-delete.sh restore ASD-20260401-101530-8f3k2m
./scripts/agent-safe-delete.sh restore ASD-20260401-101530-8f3k2m --to ./restored.txt
```

## Why It Triggers Automatically

This project is not meant to be just a manual archiving command. Its purpose is to provide AI agents with a default safe deletion semantic.

So it should trigger not only when:

- the user explicitly says “delete”
- the user explicitly says “archive”

but also when:

- an agent infers it must delete an old file before rebuilding
- an agent needs to clean up obsolete directories, stale modules, broken outputs, or temporary artifacts
- an agent must remove an old object in order to replace it with a new one

In other words, whenever an agent is preparing to delete, replace, or clean up filesystem objects, this skill should take over and rewrite permanent deletion into recoverable archiving.

## Archive Layout

Archived objects are placed directly in the archive root, while metadata is stored in a hidden directory:

```text
<safe-archive-root>/
  LiteBanana/
  README.md
  README-20260401-101530.md
  .agent-safe-delete/
    ASD-20260401-101530-8f3k2m.json
```

Example metadata JSON:

```json
{
  "schema_version": 2,
  "id": "ASD-20260401-101530-8f3k2m",
  "archived_at": "2026-04-01T10:15:30Z",
  "original_path": "/path/to/project/example.txt",
  "archived_path": "/path/to/safe-archive/example.txt",
  "archived_name": "example.txt",
  "kind": "file",
  "safe_archive_root": "/path/to/safe-archive",
  "restore_status": "archived"
}
```

## Behavioral Guarantees

- Missing targets fail immediately.
- Paths already inside the archive root cannot be archived again.
- The archive root itself and the hidden metadata directory cannot be archived.
- Archiving uses `mv`, not copy.
- `restore` defaults to the original path and fails if the restore target already exists.
- Both files and directories produce structured metadata.
- If archived objects are manually removed, stale metadata is automatically cleaned up later.
- Ambiguous delete targets must be clarified first; only high-risk targets such as `.env`, credentials, system paths, repository roots, or large batch deletions require an extra confirmation.

## Local Verification

```bash
./tests/smoke.sh
```

The smoke test verifies:

- default archive root resolution
- file archive and restore
- directory archive and restore
- metadata JSON generation
- stale metadata auto-pruning

## Possible Next Steps

- `list` / `inspect` subcommands
- `restore --force`
- richer JSON output
