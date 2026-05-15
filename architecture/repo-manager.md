# Repository Manager

**File:** `lp_triage/engine/repo_manager.py`

## Responsibilities

- Clone repositories on demand (blobless, `--filter=blob:none --no-checkout`)
- Fetch all tracked repos in parallel at the start of each run
- Provide `get_log`, `get_commit`, and `read_file` operations to the agent loop
- Enforce path scope so the agent cannot read outside the configured subdir

## Clone strategy

Blobless clones skip downloading file blobs at clone time, making the initial
clone fast. Blobs are fetched on demand by `git show`. If blobless clone fails,
a standard full clone is attempted as fallback.

Repos are stored in `{cache_dir}/repos/{dir_name}/` where `dir_name` is
derived from the repository URL's last path segment (`.git` suffix stripped).

## Path scope enforcement

`_scoped_path(subdir, path)` prevents directory traversal:

```python
clean = PurePosixPath(path)
if clean.is_absolute():
    raise PathScopeError(...)
combined = PurePosixPath(subdir) / clean
if ".." in combined.parts:
    raise PathScopeError(...)
return str(combined)
```

`".." in combined.parts` is used rather than `relative_to()` because
`relative_to()` does not resolve `..` components.

When `subdir` is empty the path resolves relative to the repo root, giving the
agent access to the whole repository.

## Git operations

All operations run `git` as a subprocess via `asyncio.create_subprocess_exec`:

| Method | Git command |
|--------|------------|
| `get_log(repo_dir, branch, subdir, n)` | `git log -{n} --oneline {branch} [-- {subdir}]` |
| `get_commit(repo_dir, hash)` | `git show --stat {hash}` |
| `read_file(repo_dir, branch, subdir, path)` | `git show {branch}:{scoped_path}` |

`get_log` omits the `-- {subdir}` filter when subdir is blank, returning the
full repo log instead.
