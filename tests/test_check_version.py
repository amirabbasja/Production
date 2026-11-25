"""Exercise the version policy against real temporary Git histories."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_version.py"
SPEC = importlib.util.spec_from_file_location("check_version", SCRIPT)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


class VersionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="code-version-test-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.git("init", "-q", "-b", "main")
        self.write("Main_version/VERSION", "1\n")
        self.write("Main_version/train.py", "print('original')\n")
        self.write("Main_version/requirements.txt", "numpy>=1.24\n")
        self.write_notebook("print('original')")
        self.base = self.commit()

    def git(self, *arguments):
        result = subprocess.run(
            ["git", "-C", str(self.repo), *arguments],
            check=True, capture_output=True, text=True,
        )
        return result.stdout.strip()

    def write(self, path, contents):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)

    def write_notebook(self, code, *, output=None, markdown="Initial notes"):
        self.write("Main_version/train.ipynb", json.dumps({
            "cells": [
                {"cell_type": "markdown", "source": [markdown]},
                {"cell_type": "code", "source": [code], "outputs": output or []},
            ],
            "metadata": {"arbitrary": output},
        }))

    def commit(self):
        self.git("add", ".")
        self.git(
            "-c", "user.name=Version Tests", "-c", "user.email=tests@example.invalid",
            "commit", "-q", "-m", "Fixture change",
        )
        return self.git("rev-parse", "HEAD")

    def check(self, *, base=None, head=None):
        return policy.check_version(self.repo, base or self.base, head)

    def test_unchanged_tree_passes(self):
        self.assertEqual(self.check(), (1, 1, []))

    def test_code_change_requires_exactly_one_increment(self):
        self.write("Main_version/train.py", "print('changed')\n")
        for version in ("1\n", "3\n"):
            with self.subTest(version=version):
                self.write("Main_version/VERSION", version)
                with self.assertRaisesRegex(policy.VersionError, "must be 2"):
                    self.check()
        self.write("Main_version/VERSION", "2\n")
        self.assertEqual(self.check(), (1, 2, ["Main_version/train.py"]))

    def test_multiple_files_and_commits_in_one_task_need_one_increment(self):
        self.write("Main_version/train.py", "print('changed')\n")
        self.commit()
        self.write("Main_version/requirements.txt", "numpy>=2.0\n")
        self.write("Main_version/VERSION", "2\n")
        head = self.commit()
        self.assertEqual(self.check(head=head)[:2], (1, 2))

    def test_untracked_source_requires_increment(self):
        self.write("Main_version/new_helper.py", "def helper(): return 1\n")
        with self.assertRaisesRegex(policy.VersionError, "must be 2"):
            self.check()

    def test_staged_source_requires_increment(self):
        self.write("Main_version/train.py", "print('changed')\n")
        self.git("add", "Main_version/train.py")
        with self.assertRaisesRegex(policy.VersionError, "must be 2"):
            self.check()

    def test_deleted_source_requires_increment(self):
        (self.repo / "Main_version/train.py").unlink()
        with self.assertRaisesRegex(policy.VersionError, "must be 2"):
            self.check()

    def test_dependencies_native_code_and_runtime_manifests_require_increment(self):
        for path in (
            "Main_version/requirements.txt", "Main_version/pyproject.toml",
            "Main_version/src/kernel.cu", "Main_version/src/lib.so",
            "Main_version/src/work_helpers/manifest.json",
        ):
            with self.subTest(path=path):
                self.write(path, "changed runtime content\n")
                with self.assertRaisesRegex(policy.VersionError, "must be 2"):
                    self.check()
                if path == "Main_version/requirements.txt":
                    self.write(path, "numpy>=1.24\n")
                else:
                    (self.repo / path).unlink()

    def test_extensionless_executables_require_increment(self):
        target = self.repo / "Main_version/runner"
        self.write("Main_version/runner", "#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
        with self.assertRaisesRegex(policy.VersionError, "must be 2"):
            self.check()

    def test_docs_generated_files_and_external_upload_helper_do_not_increment(self):
        for path in (
            "Main_version/README.md", "Main_version/src/AGENTS.md",
            "Main_version/__pycache__/cache.pyc",
            "Main_version/.runtime/generated.py", "Main_version/.env",
            "upload_helper.ipynb",
        ):
            self.write(path, "documentation or generated content\n")
        self.assertEqual(self.check(), (1, 1, []))

    def test_unnecessary_increment_is_rejected(self):
        self.write("Main_version/README.md", "Documentation only\n")
        self.write("Main_version/VERSION", "2\n")
        with self.assertRaisesRegex(policy.VersionError, "must be 1"):
            self.check()

    def test_notebook_outputs_metadata_and_markdown_do_not_increment(self):
        self.write_notebook("print('original')", output=[{"text": "new output"}], markdown="Updated notes")
        self.assertEqual(self.check(), (1, 1, []))

    def test_notebook_code_changes_require_increment(self):
        self.write_notebook("print('changed')")
        with self.assertRaisesRegex(policy.VersionError, "must be 2"):
            self.check()

    def test_invalid_or_deleted_version_is_rejected(self):
        for invalid in ("", "0\n", "-1\n", "1.0.0\n", "02\n", "two\n"):
            with self.subTest(version=invalid):
                self.write("Main_version/VERSION", invalid)
                with self.assertRaisesRegex(policy.VersionError, "positive integer"):
                    self.check()
        (self.repo / "Main_version/VERSION").unlink()
        with self.assertRaisesRegex(policy.VersionError, "cannot be deleted"):
            self.check()

    def test_adopting_versioning_starts_at_one(self):
        (self.repo / "Main_version/VERSION").unlink()
        base = self.commit()
        self.write("Main_version/VERSION", "1\n")
        self.assertEqual(self.check(base=base), (None, 1, []))
        self.write("Main_version/VERSION", "2\n")
        with self.assertRaisesRegex(policy.VersionError, "must be 1"):
            self.check(base=base)

    def test_new_branch_push_and_first_commit(self):
        zeros = "0" * 40
        self.assertEqual(self.check(base=zeros, head=self.base)[:2], (None, 1))
        self.write("Main_version/train.py", "print('changed')\n")
        self.write("Main_version/VERSION", "2\n")
        head = self.commit()
        self.assertEqual(self.check(base=zeros, head=head)[:2], (1, 2))

    def test_pr_uses_latest_base_version_to_reject_parallel_duplicates(self):
        self.git("checkout", "-q", "-b", "task")
        self.write("Main_version/train.py", "print('task')\n")
        self.write("Main_version/VERSION", "2\n")
        head = self.commit()
        self.git("checkout", "-q", "main")
        self.write("Main_version/src/other.py", "print('another task')\n")
        self.write("Main_version/VERSION", "2\n")
        newer_base = self.commit()
        with self.assertRaisesRegex(policy.VersionError, "must be 3"):
            self.check(base=newer_base, head=head)


if __name__ == "__main__":
    unittest.main()
