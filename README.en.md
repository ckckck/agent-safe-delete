# agent-safe-delete

[中文](README.md)

> Protect users from destructive AI agent file operations by turning delete into reversible archive moves.

`agent-safe-delete` is a safe-delete skill for AI agents. When an agent needs to delete a file, directory, or symlink, it does not perform irreversible permanent deletion. Instead, it moves the target into a recoverable archive area and stores restore metadata as JSON files inside a hidden metadata directory.

## Core Idea

- Delete semantics are rewritten into reversible archive moves.
- It intercepts not only explicit user deletion requests, but also deletion, replacement, and cleanup actions inferred during agent execution.
- The archive root is controlled by a single environment variable: `ASD_SAFE_ARCHIVE_ROOT`.
- If the variable is not set, a platform default path is used.
- Archived objects keep their original names whenever possible and are placed directly in the archive root; timestamp suffixes are added only on collisions.
- Metadata is stored in a hidden `.agent-safe-delete/` directory under the archive root.
- `restore` can move files, directories, or symlinks back to their original paths or to a user-specified location.
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
    agent-safe-delete.py
    remote-safe-delete.py
  tests/
    test_agent_safe_delete.py
    test_remote_safe_delete.py
    smoke.sh
```

## Usage

### 1. Use as a skill

Place the repository in a skill-discoverable location and treat `scripts/agent-safe-delete.py` as the bundled CLI entrypoint for the skill.

There are two different execution contexts to keep separate:

- For repo-local development, testing, or manual runs, you can execute `python scripts/agent-safe-delete.py ...` from the repository root.
- For an installed skill, do not assume the agent's current working directory is the skill directory, and do not assume the current workspace contains `scripts/agent-safe-delete.py`. In that case, the host platform or installation layer should resolve the skill's actual installed directory, or provide a stable wrapper command, and then invoke this Python entrypoint.

This skill is not designed only for cases where the user explicitly says “delete”. It is designed to take over deletion semantics whenever an agent is about to remove, replace, or clean up filesystem objects.

### 2. Use as a CLI tool

The examples below assume the current directory is the repository root:

```bash
python scripts/agent-safe-delete.py show-archive-root
python scripts/agent-safe-delete.py archive ./example.txt
python scripts/agent-safe-delete.py archive ./build --json
python scripts/agent-safe-delete.py restore ASD-20260401-101530-8f3k2m
python scripts/agent-safe-delete.py restore ASD-20260401-101530-8f3k2m --to ./restored.txt
```

### 3. Remote server safe archive

Remote archiving does not pass `ssh:` paths to the local archive command. It creates an archive batch on the target server and moves the remote object there. The tool does not embed real hosts, users, project paths, domains, or credentials; hosts and project roots are passed as arguments, and the archive root comes from `--remote-archive-root` or the `ASD_REMOTE_ARCHIVE_ROOT` environment variable.

`--remote-archive-root` takes precedence over `ASD_REMOTE_ARCHIVE_ROOT`. If neither is provided, the remote archive command fails instead of guessing a project- or server-specific directory. The recommended generic value is:

```bash
export ASD_REMOTE_ARCHIVE_ROOT="~/.agent-safe-delete"
```

In SSH mode, `~/.agent-safe-delete` is expanded by the target server for the current SSH user, so different servers or login users naturally use their own home directories. The safe-delete skill does not depend on project-specific server-management skills to determine the archive root; project skills can provide the SSH target and project root, while this generic environment variable or explicit argument controls the archive root.

Create a delete plan before `rsync --delete`:

```bash
python scripts/remote-safe-delete.py plan-rsync-delete \
  --dry-run-output <dry-run-output.txt> \
  --env test \
  --remote-project-root <remote-project-root> \
  --remote-archive-root <remote-archive-root> \
  --purpose <purpose> \
  --output <plan.json>
```

The script can also run the dry run:

```bash
python scripts/remote-safe-delete.py plan-rsync-delete \
  --rsync-source <local-source>/ \
  --rsync-destination <ssh-target>:<remote-project-root>/ \
  --env test \
  --remote-project-root <remote-project-root> \
  --remote-archive-root <remote-archive-root> \
  --purpose <purpose> \
  --output <plan.json>
```

Archive the planned remote delete list:

```bash
python scripts/remote-safe-delete.py archive-list \
  --ssh-target <ssh-target> \
  --plan <plan.json>
```

Archive one explicit remote path:

```bash
python scripts/remote-safe-delete.py archive-path \
  --ssh-target <ssh-target> \
  --remote-path <remote-absolute-path> \
  --env test \
  --remote-project-root <remote-project-root> \
  --remote-archive-root <remote-archive-root> \
  --purpose <purpose>
```

Production is stricter: `plan-rsync-delete` requires `--source-git-ref <commit-or-tag>`, and `archive-list` requires `--confirm-plan <plan_sha256>`. High-risk paths also require exact `--confirm-high-risk <remote-absolute-path>` confirmations.

For example, when explicitly archiving a high-risk remote path:

```bash
python scripts/remote-safe-delete.py archive-path \
  --ssh-target <ssh-target> \
  --remote-path <remote-absolute-path> \
  --env test \
  --remote-project-root <remote-project-root> \
  --remote-archive-root <remote-archive-root> \
  --purpose <purpose> \
  --confirm-high-risk <remote-absolute-path>
```

## Runtime Requirements

- A working `python` command is required.
- `bash` is no longer the only supported launcher.
- The same command works from PowerShell, macOS, and Linux shells.

## Why It Triggers Automatically

This project is not meant to be just a manual archiving command. Its purpose is to provide AI agents with a default safe deletion semantic.

So it should trigger not only when:

- the user explicitly says “delete”
- the user explicitly says “archive”

but also when:

- an agent infers it must delete an old file before rebuilding
- an agent needs to clean up obsolete directories, stale modules, broken outputs, or temporary artifacts
- an agent must remove an old object in order to replace it with a new one

That cleanup scope includes not only obviously obsolete files, but also intermediate files, temporary files, conversion source files, cache files, and one-off generated artifacts. If the agent is about to remove them from the filesystem, this skill should trigger.

For example, cleaning up an intermediate `html` file after generating the final `docx`, deleting temporary images after an export succeeds, or removing a source-format file after a successful conversion should not be treated as exceptions that can be deleted directly.

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

- Missing targets fail immediately; broken symlinks are handled as symlink objects rather than treated as missing paths.
- Paths already inside the archive root cannot be archived again.
- The archive root itself and the hidden metadata directory cannot be archived.
- Archiving uses move semantics, not copy.
- `restore` defaults to the original path and fails if the restore target already exists.
- Files, directories, and symlinks produce structured metadata.
- If archived objects are manually removed, stale metadata is automatically cleaned up later.
- Even when the target exists only as an intermediate file, temporary file, cache file, or conversion source file created to produce a final deliverable, the agent must not use direct `rm`; it must still archive the target.
- Ambiguous delete targets must be clarified first; only high-risk targets such as `.env`, credentials, system paths, repository roots, or large batch deletions require an extra confirmation.
- The remote script rejects empty paths, `/`, `.`, `..`, paths containing `..`, glob-like paths, the archive root itself, and paths inside the archive root.
- The remote archive root is controlled by `--remote-archive-root` or `ASD_REMOTE_ARCHIVE_ROOT`, with the CLI argument taking precedence; the recommended generic value is `~/.agent-safe-delete`.
- Root-directory tests use string validation and a temporary fake remote root only; they must never move real system directories.

## Remote Archive Batch Layout

```text
<remote-archive-root>/<env>/<timestamp>-<purpose>/
  manifest.json
  verify-before.txt
  verify-after.txt
  restore.sh
  payload/
```

`manifest.json` records original paths, archived paths, types, sizes, modes, owners, mtimes, checksums, risk levels, environment, plan hash, and restore commands. Sensitive files only expose metadata, never contents.

## Local Verification

```bash
./tests/smoke.sh
python scripts/remote-safe-delete.py --help
```

The smoke test verifies:

- default archive root resolution
- file archive and restore
- directory archive and restore
- broken symlink archive and restore
- metadata JSON generation
- stale metadata auto-pruning
- remote delete plan parsing
- root and broad path rejection
- fake remote root archive and restore instruction generation
- test and production environment gates
- exact confirmation for high-risk remote paths

## License

This project is licensed under the MIT License. See `LICENSE` for details.

## Acknowledgements

- [Linux Do Community](https://linux.do/)
