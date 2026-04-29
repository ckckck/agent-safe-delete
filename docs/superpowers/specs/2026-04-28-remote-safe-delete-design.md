# Remote Safe Delete Design

## Goal

`agent-safe-delete` must protect remote server file-system deletions the same way it protects local deletions. Any operation that would make a remote file, directory, or symlink disappear must first become an auditable remote archive move.

This design covers both test and production environments without hard-coding real hosts, users, project paths, domains, or credentials.

## Scope

In scope:

- Planning remote `rsync --delete` effects before a real sync.
- Archiving remote paths from a generated delete plan.
- Archiving an explicitly named remote path even when it never existed in the local repository.
- Environment gates for `test` and `prod`.
- Risk classification for sensitive paths.
- Manifest and restore script generation.
- Test safeguards that prove root and broad paths are rejected without touching real `/`.

Out of scope:

- Database row deletion.
- Git branch deletion.
- Automatic one-command production restore.
- Built-in knowledge of any specific server.

## Command Model

Add a separate CLI entrypoint instead of expanding the local-only CLI:

```text
scripts/remote-safe-delete.py
```

Commands:

- `plan-rsync-delete`: run or consume `rsync --dry-run --delete --itemize-changes`, parse `*deleting` lines, validate paths, and write a JSON plan.
- `archive-list`: read a plan and move every planned remote object into one archive batch.
- `archive-path`: archive one explicitly named remote file, directory, or symlink.

The existing `scripts/agent-safe-delete.py` remains the local archive tool and keeps its current behavior.

Real remote execution uses `ssh <ssh-target> python3 -c <bootstrap>` with a JSON request on stdin. The local side validates every path before opening the SSH process, and the remote bootstrap validates again before moving anything.

## Remote Archive Layout

Every remote archive operation creates one batch directory:

```text
<remote-archive-root>/<env>/<timestamp>-<purpose>/
  plan.json
  manifest.json
  verify-before.txt
  verify-after.txt
  restore.sh
  payload/
    0001-<basename>
    0002-<basename>
```

`manifest.json` records:

- schema version
- batch id
- environment
- purpose
- source mode: `rsync-delete-plan` or `explicit-path`
- remote project root
- remote archive root
- source git ref when supplied
- plan hash for plan-based operations
- risk level
- original path, archive path, type, size, mode, owner, group, mtime, and checksum where safe
- restore commands

Sensitive file contents are never printed or copied into logs. Metadata and checksums are allowed.

## Path Safety

Path safety is the first gate and must run before any remote command that can move files.

Reject these paths and plan entries:

- empty path
- `/`
- `.`
- `..`
- paths containing `..` as a segment
- glob-like entries containing `*`, `?`, `[`, or `]`
- remote archive root itself
- hidden metadata directory under the archive root
- the declared remote project root when it would archive the whole repository root

The root-directory safeguard is double-layered:

1. Pure parsing and classification reject `/`, `.`, `..`, and broad paths before any executor is chosen.
2. The local test executor maps remote paths into a temporary fake root and refuses to map invalid remote paths, so tests can assert root rejection without ever touching real `/`.

No test may create, move, or delete real `/`, `/tmp`, `/var`, `/home`, `/root`, or any real system directory. Tests that mention these paths must only use string validation or a temporary fake remote root.

## Risk Classification

Risk is computed per item and lifted to the whole batch by highest severity.

Low risk:

- old source remnants under a project directory
- stale build output
- one-off scripts

Medium risk:

- web server config
- service manager config
- deploy scripts
- old release directories

High risk:

- `.env` and runtime environment files
- keys, certificates, and credential files
- database files
- Docker volumes
- upload or media directories
- repository root or other broad project roots

High-risk objects require exact per-path confirmation for both plan-based archive and explicit path archive. A broad `all` confirmation is intentionally not accepted.

## Test Environment Gates

`test` allows quick execution once the path list is explicit and risk gates pass.

Rules:

- `plan-rsync-delete` may run from a dirty worktree but records the supplied source ref when present.
- low-risk plan items can be archived without additional confirmation.
- high-risk items still require exact per-path confirmation.
- `rsync --delete` is allowed only after a plan has been archived and a second dry run no longer reports unarchived delete entries.

## Production Environment Gates

`prod` is stricter because mistakes affect the live service.

Rules:

- `source_git_ref` is required for production plans and explicit path archives.
- plan-based archive execution requires `--confirm-plan <plan_sha256>`.
- high-risk paths require exact per-path confirmation in addition to the plan hash.
- the manifest must include enough evidence to identify the source version and restore the moved paths.
- production workflows should prefer release-directory switching over direct project-directory overwrite.

## Execution Backends

The CLI supports two execution modes:

- local fake remote root for tests and dry local rehearsals
- SSH target for real remote servers

The local fake backend is not a shortcut around safety. It runs the same path validation and maps remote absolute paths into a temporary root only after validation succeeds.

## Verification

Implementation is complete when these checks pass:

```bash
pytest -q
python scripts/agent-safe-delete.py --help
python scripts/remote-safe-delete.py --help
./tests/smoke.sh
```

The test suite must include explicit assertions that root and broad paths are rejected before any archive move is attempted.
