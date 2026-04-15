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

    def test_archive_json_contains_expected_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            archive_root = Path(tmpdir) / "archive-root"
            workspace.mkdir()
            source = workspace / "example.txt"
            source.write_text("hello\n", encoding="utf-8")

            archive = run_cli("archive", str(source), "--json", env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)})
            self.assertEqual(archive.returncode, 0, archive.stderr)
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

            first_archive = json.loads(
                run_cli("archive", str(first), "--json", env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)}).stdout
            )
            second_archive = json.loads(
                run_cli("archive", str(second), "--json", env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)}).stdout
            )

            self.assertNotEqual(first_archive["archived_path"], second_archive["archived_path"])
            self.assertTrue(Path(second_archive["archived_path"]).is_file())

    def test_restore_fails_when_target_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            archive_root = Path(tmpdir) / "archive-root"
            workspace.mkdir()
            source = workspace / "example.txt"
            source.write_text("hello\n", encoding="utf-8")

            archive = json.loads(
                run_cli("archive", str(source), "--json", env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)}).stdout
            )
            source.write_text("occupied\n", encoding="utf-8")

            restore = run_cli("restore", archive["id"], env={"ASD_SAFE_ARCHIVE_ROOT": str(archive_root)})
            self.assertNotEqual(restore.returncode, 0)
            self.assertIn("目标路径已存在", restore.stderr)


if __name__ == "__main__":
    unittest.main()
