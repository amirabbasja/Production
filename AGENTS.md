# When making an alter_version

Save all files in a directory called ALter_versions in a .zip file with an encoded password that you get from zip_password environment variable in the .env file.

The zip file's name should be in following format <project_name>_<code_version>_<profile_id>.zip

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
