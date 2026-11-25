# AGENT instructions

## Code versioning

Main_version/VERSION is the authoritative code version, stored as an integer.

For every completed task that changes executable code or runtime dependencies
inside Main_version:

- Increment VERSION by exactly 1, once for the whole task.
- Include the version change alongside the code changes.
- Report the old and new versions in the final response.

Documentation-only changes, generated files, and uploads do not increment it.

## Code versioning

`Main_version/VERSION` is the authoritative code version: one positive integer.
For each completed task that changes executable code or runtime dependencies in
`Main_version`, increment it by exactly 1, once for the whole task. Include that
increment with the code changes and report the old and new versions in the final
response. Do not increment it for documentation-only changes, generated files,
notebook outputs/metadata, or uploads.

Follow `Main_version/AGENTS.md` when working inside that directory. The version
check covers source files, native libraries, executable files, runtime files in
`Main_version/src`, and dependency/build manifests. For notebooks it compares
code cells only. Never edit `.env` or generated files to satisfy the check.

Before finishing, run `python3 scripts/check_version.py --base HEAD` from
`Production` to check the working tree, including untracked files. A task may
contain multiple commits, but the version increases only once across the task.
GitHub Actions checks each pull request or push as one change set; keep one
completed task per pull request or push. Rebase and resolve the version if the
base branch advanced.

The CI job is named `Check code version`. Repository branch protection must make
that status check required to prevent merging a failed check.
