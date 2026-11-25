#!/usr/bin/env python3
"""Require one Main_version/VERSION increment for a code-changing task.

Local: python3 scripts/check_version.py --base HEAD
CI:    python3 scripts/check_version.py --base BASE_SHA --head HEAD_SHA

The local check includes staged, unstaged, and untracked files. Revision checks
compare with the merge base. A repository adopting versioning starts at 1.
"""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


VERSION_PATH = "Main_version/VERSION"
GENERATED_DIRS = {
    "__pycache__", ".ipynb_checkpoints", ".runtime", ".cache", ".pytest_cache",
    ".venv", "venv", "build", "dist", "node_modules",
}
DOCUMENTATION_SUFFIXES = {".md", ".rst", ".txt", ".pdf", ".png", ".jpg", ".svg"}
CODE_SUFFIXES = {
    ".py", ".pyi", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".cu", ".cuh",
    ".so", ".dll", ".dylib", ".sh", ".bash", ".zsh", ".ps1", ".bat",
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java",
    ".kt", ".swift", ".rb", ".pl", ".lua", ".dockerfile",
}
DEPENDENCY_FILES = {
    "pyproject.toml", "setup.cfg", "Pipfile", "Pipfile.lock", "poetry.lock",
    "uv.lock", "environment.yml", "environment.yaml", "conda-lock.yml",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Dockerfile", "Makefile", "CMakeLists.txt",
}


class VersionError(ValueError):
    """The changes do not satisfy the versioning policy."""


def git(repo, *arguments):
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments], capture_output=True, check=False
    )
    if result.returncode:
        raise VersionError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def read_file(repo, revision, path):
    if revision is None:
        target = repo / path
        return target.read_bytes() if target.is_file() else None
    if not git(repo, "ls-tree", "-z", revision, "--", path):
        return None
    return git(repo, "show", f"{revision}:{path}")


def read_version(repo, revision):
    content = read_file(repo, revision, VERSION_PATH)
    if content is None:
        return None
    if not re.fullmatch(rb"[1-9][0-9]*\r?\n?", content):
        raise VersionError(f"{VERSION_PATH} must contain one positive integer.")
    return int(content)


def notebook_code(content, path):
    if content is None:
        return ()
    try:
        cells = json.loads(content)["cells"]
        return tuple(
            "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]
            for cell in cells if cell["cell_type"] == "code"
        )
    except (ValueError, KeyError, TypeError) as error:
        raise VersionError(f"Cannot inspect code cells in {path}: invalid notebook.") from error


def is_executable(repo, revision, path):
    if revision is None:
        target = repo / path
        return target.is_file() and os.access(target, os.X_OK)
    entry = git(repo, "ls-tree", revision, "--", path)
    return entry.startswith(b"100755 ")


def changes_runtime(repo, base, head, path):
    relative = PurePosixPath(path).relative_to("Main_version")
    name = relative.name
    suffix = relative.suffix.lower()
    if path == VERSION_PATH or GENERATED_DIRS.intersection(relative.parts):
        return False
    if name == ".env" or name.startswith(".env.") or suffix in {".pyc", ".pyo", ".log"}:
        return False
    if suffix == ".ipynb":
        previous = read_file(repo, base, path) if base is not None else None
        return notebook_code(previous, path) != notebook_code(
            read_file(repo, head, path), path
        )
    if name in DEPENDENCY_FILES or (
        suffix in {".txt", ".in"} and name.startswith(("requirements", "constraints"))
    ):
        return True
    if suffix in DOCUMENTATION_SUFFIXES:
        return False
    if suffix in CODE_SUFFIXES or relative.parts[0] == "src":
        return True
    return is_executable(repo, base, path) or is_executable(repo, head, path)


def check_version(repo, base="HEAD", head=None):
    repo = Path(repo).resolve()
    if head is not None:
        head = git(repo, "rev-parse", "--verify", "--end-of-options", f"{head}^{{commit}}").decode().strip()
    if base and set(base) != {"0"}:
        base = git(repo, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").decode().strip()
        version_base = base
        base = git(repo, "merge-base", base, head or "HEAD").decode().strip()
    else:
        # A newly pushed branch has an all-zero 'before' SHA. Check its last
        # change, or initialize at version 1 if this is the first-ever commit.
        parents = git(repo, "rev-list", "--parents", "-n", "1", head or "HEAD").decode().split()
        base = parents[1] if len(parents) > 1 else None
        version_base = base

    current_version = read_version(repo, head)
    if current_version is None:
        raise VersionError(f"{VERSION_PATH} is required and cannot be deleted.")
    old_version = read_version(repo, version_base) if version_base is not None else None
    if base is None:
        paths = git(repo, "ls-tree", "-r", "--name-only", "-z", head or "HEAD", "--", "Main_version")
    else:
        revisions = [base, head] if head is not None else [base]
        paths = git(repo, "diff", "--name-only", "--no-renames", "-z", *revisions, "--", "Main_version")
    changed_paths = set(os.fsdecode(path) for path in paths.split(b"\0") if path)
    if head is None:
        untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z", "--", "Main_version")
        changed_paths.update(os.fsdecode(path) for path in untracked.split(b"\0") if path)
    runtime_paths = sorted(path for path in changed_paths if changes_runtime(repo, base, head, path))

    expected = 1 if old_version is None else old_version + bool(runtime_paths)
    if current_version != expected:
        reason = "runtime code or dependencies changed" if runtime_paths else "no runtime code or dependencies changed"
        previous = "unversioned" if old_version is None else str(old_version)
        raise VersionError(
            f"{reason}; {VERSION_PATH} must be {expected} (base: {previous}, found: {current_version})."
        )
    return old_version, current_version, runtime_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="HEAD", help="Base revision; defaults to HEAD for local checks.")
    parser.add_argument("--head", help="Head revision; omit to check the working tree.")
    args = parser.parse_args()
    try:
        old, new, paths = check_version(Path(__file__).resolve().parents[1], args.base, args.head)
    except (VersionError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    previous = "unversioned" if old is None else str(old)
    print(f"PASS: code version {previous} -> {new}; {len(paths)} runtime file(s) changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
