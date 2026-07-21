# btbkt Repository Access Design

## Goal

Add agent-facing repository clone, fetch, and idempotent ensure workflows that
reuse btbkt authentication without exposing credentials or overwriting local
work.

## Command Surface

Repository operations remain under the existing `repo` resource:

```bash
btbkt --project TRAD --repo trading repo clone --branch master PATH
btbkt --project TRAD --repo trading repo fetch --branch master PATH
btbkt --project TRAD --repo trading repo ensure --branch master PATH
```

Each operation accepts one of `--branch`, `--tag`, `--commit`, `--pr`, or
`--ref`. Typed selectors are unambiguous. `--ref` accepts a branch/tag ref, a
full 40- or 64-character commit ID, or a short name; a short name that exists
as both a branch and tag is rejected. Other internal refs are rejected; PRs use
`--pr`. Without an explicit selector, btbkt reads
the repository's configured default branch through Bitbucket REST.

`clone` requires a missing or empty destination. `fetch` requires an existing
checkout and never updates its worktree. `ensure` clones a missing checkout or
validates and fetches an existing checkout, then fast-forwards only a clean,
attached checkout already on the requested branch. Existing tag, commit, and PR
requests are fetch-and-resolve operations and never detach or switch an existing
worktree.

## Architecture

`src/btbkt/repo_access.py` owns Git command execution, askpass lifetime, clone
link selection, remote validation, ref resolution, checkout inspection, safe
update rules, result modeling, and sanitized Git errors. CLI handlers only
parse arguments and call this service.

`BitbucketClient` adds a thin `get_default_branch()` method. Existing
`get_repository()` supplies server-authoritative clone links and
`get_pull_request()` supplies PR source ref/commit data. The existing local
`origin/HEAD` inference remains unchanged for PR creation and is not used by
repository access.

## Authentication

HTTP and HTTPS Git operations use a temporary askpass program. The program
contains no credential; it reads a username and secret from per-process
environment variables. Git runs with terminal prompts and credential helpers
disabled. The token or password never appears in argv, clone URLs, remotes, Git
config, structured results, errors, stdout, or stderr. Cleanup is guaranteed by
a context manager for success, failure, exception, and interruption.

HTTPS is preferred when Bitbucket returns several clone links. HTTP remains
supported as requested and adds a plaintext-transport warning. SSH falls back
to the user's existing SSH agent/key and receives no HTTP secret environment.

Existing origins are matched against normalized server-provided clone links,
including host, port, project, and repository. Multiple fetch URLs and an
effective URL changed by Git `insteadOf` configuration are rejected. Network
commands use a one-shot random URL mapping so the executed endpoint stays bound
to the validated URL. A credentialed, query-bearing, unrecognized, or
wrong-repository origin is rejected without being rewritten.

## Safety State Machine

All fetches use explicit refspecs and never force. Worktree updates use
`git merge --ff-only` against the fetched remote-tracking ref; btbkt never runs
pull, reset, forced checkout,
stash, lock deletion, or an implicit branch switch.

Dirty, wrong-branch, and detached checkouts may fetch and resolve commits. An
`ensure` that fetched successfully but cannot update returns a structured
partial result and nonzero exit status. Hard Git failures return sanitized
error JSON with checkout state and actionable recovery guidance.

## Output Contract

Successful and partial operation results always contain `status`, `action`,
`project`, `repo`, `path`, `requested_ref`, `ref_kind`, `resolved_commit`,
`changed`, and `warning`. `action` is one of `cloned`, `fetched`,
`fast_forwarded`, or `unchanged`. Partial results additionally carry recovery
guidance and use a nonzero exit code.

## Verification

Tests use an injectable runner for argv/environment/cleanup/error assertions and
temporary local Git repositories for real clone, fetch, no-op, fast-forward,
dirty, wrong-branch, detached, tag, and commit behavior. Synthetic REST
transports cover clone-link/default-branch/PR resolution. Existing CLI and
Python 3.9 compatibility tests remain unchanged and passing. README and the
canonical workflow skill are updated, then the installed skill is synced and
checked for exact parity.
