# btbkt Repository Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every behavior change. Do not commit or push this plan's work.

**Goal:** Add credential-safe clone, fetch, and idempotent ensure commands for Bitbucket repositories.

**Architecture:** A new `repo_access.py` service owns all Git/auth/safety behavior. `client.py` remains the REST boundary and `cli.py` remains thin command wiring with compact JSON/error handling.

**Tech Stack:** Python 3.9+, argparse, pathlib, subprocess, tempfile, pytest, Git CLI, Bitbucket Data Center REST.

## Global Constraints

- Never put a token/password in Git argv, URLs, remotes, config, output, exceptions, or results.
- Support HTTP, HTTPS, and SSH clone links; HTTP emits a warning.
- Never reset, force fetch/pull/checkout, stash, delete locks, overwrite changes, or mutate Bitbucket server state.
- Existing REST, PR, raw, checkout-inferred, and explicit context behavior must remain compatible.
- Do not commit or push.

---

### Task 1: REST and ref-selection contract

**Files:**
- Modify: `src/btbkt/client.py`
- Create: `tests/test_repo_access.py`
- Modify: `tests/test_client.py`

**Interfaces:**
- Produce: `BitbucketClient.get_default_branch() -> Any`.
- Produce: typed repository/ref metadata used by `RepositoryAccess`.

- [ ] Add failing tests for the default-branch endpoint and explicit/default/PR ref resolution.
- [ ] Run the focused tests and confirm they fail for missing behavior.
- [ ] Add the smallest client/ref model implementation.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Git authentication and command boundary

**Files:**
- Create: `src/btbkt/repo_access.py`
- Modify: `tests/test_repo_access.py`

**Interfaces:**
- Produce: `GitRunner.run(args, *, cwd=None, auth=None, check=True)` with sanitized failures.
- Produce: temporary askpass context with per-process username/secret environment.
- Produce: `GitOperationError` carrying only sanitized message/state/recovery data.

- [ ] Add failing tests proving secrets never enter argv, URLs, remotes, config, results, or errors.
- [ ] Add failing cleanup tests for success, process failure, exception, and interruption.
- [ ] Run focused tests and verify the expected failures.
- [ ] Implement askpass, disabled prompting/helpers, redaction, and cleanup.
- [ ] Run focused tests and confirm they pass.

### Task 3: Clone, fetch, validation, and safe ensure state machine

**Files:**
- Modify: `src/btbkt/repo_access.py`
- Modify: `tests/test_repo_access.py`

**Interfaces:**
- Produce: `RepositoryAccess.clone(...)`, `.fetch(...)`, and `.ensure(...)`.
- Produce: stable result dictionaries with required keys and partial-result metadata.

- [ ] Add failing tests for initial clone and missing/nonempty destinations.
- [ ] Add failing tests for no-op and fast-forward ensure.
- [ ] Add failing tests for branch/tag/commit/PR/default-branch resolution.
- [ ] Add failing tests for dirty, wrong branch, detached HEAD, wrong remote, HTTP/HTTPS/SSH, and fetch-then-blocked partial results.
- [ ] Run focused tests and verify each behavior fails before implementation.
- [ ] Implement clone-link normalization, remote validation, explicit fetch refspecs, state inspection, and ff-only update.
- [ ] Run focused tests and confirm they pass.

### Task 4: CLI integration and stable errors

**Files:**
- Modify: `src/btbkt/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Add: `repo clone`, `repo fetch`, and `repo ensure` parser actions.
- Preserve: existing `main()` dependency injection and JSON conventions.

- [ ] Add failing CLI tests for option exclusivity, explicit/default context, stable result fields, partial exit status, and sanitized Git errors.
- [ ] Run focused CLI tests and confirm failure.
- [ ] Wire the repository service without placing Git subprocess logic in handlers.
- [ ] Run focused CLI tests and confirm success.

### Task 5: Documentation and canonical skill

**Files:**
- Modify: `README.md`
- Modify: `skills/using-btbkt-pr-workflows/SKILL.md`
- Modify: `tests/test_sync_skill.py`

**Interfaces:**
- Skill must tell agents to use btbkt repository commands and never author their own askpass scripts.

- [ ] Add failing skill-contract tests for repository-command-first guidance and removal of DIY askpass guidance.
- [ ] Run the focused test and confirm failure.
- [ ] Update README examples and the canonical skill concisely.
- [ ] Run focused tests, sync the installed skill, and verify exact parity.

### Task 6: Full verification and review

**Files:**
- Inspect all changed files; modify only for concrete findings.

- [ ] Run `pytest -q`.
- [ ] Run `python -m compileall -q src tests scripts`.
- [ ] Run top-level and `repo` CLI help commands.
- [ ] Run `make check`, `make skill-check`, and `git diff --check`.
- [ ] Run quick review, deep security/correctness reviews, and a separate tester pass.
- [ ] If credentials and a safe read-only repository are available, run temporary-directory clone/fetch smoke tests without printing credentials; otherwise record the blocker.
