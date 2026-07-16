# btbkt Review Status And Skill Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct Bitbucket reviewer-status writes, make combined reviews recoverable and duplicate-safe, expose controlled raw REST access, and keep the installed workflow skill identical to its repository source.

**Architecture:** Keep endpoint mechanics in `client.py`, resolved identity in `context.py`, and multi-step workflow/error reporting in `cli.py`. Use the official participants endpoint for direct decisions and reserve `/review` for pending-review lifecycle operations. Keep skill deployment in a standalone standard-library script with a pure comparison/copy core.

**Tech Stack:** Python 3.9+, argparse, standard-library JSON/path/hash/file handling, pytest, Bitbucket Data Center REST API.

## Global Constraints

- Preserve Python `>=3.9` and add no runtime dependencies.
- Do not automatically retry any external write.
- Do not fall back from a pending review to a public comment.
- Preserve existing compact output fields and pagination semantics.
- Do not commit changes unless the user explicitly asks.

---

## File Structure

- Modify `src/btbkt/context.py`: retain resolved non-secret username in `BitbucketContext`.
- Modify `src/btbkt/client.py`: add participants and pending-review endpoint methods; remove incorrect decision delegation to `/review`.
- Modify `src/btbkt/cli.py`: resolve slug/commit, orchestrate decisions and pending review, expose recovery and raw commands, report partial results.
- Modify `tests/test_context.py`, `tests/test_client.py`, and `tests/test_cli.py`: lock observable contracts and failure recovery.
- Create `scripts/sync_skill.py`: exact canonical skill installation and check mode.
- Create `tests/test_sync_skill.py`: temporary-destination sync and parity tests.
- Modify `skills/using-btbkt-pr-workflows/SKILL.md`, `README.md`, `Makefile`, and the earlier review-loop design non-goal.
- Update `~/.agents/skills/using-btbkt-pr-workflows` only after repository verification passes.

### Task 1: Client Endpoints And Resolved Identity

**Files:**
- Modify: `src/btbkt/context.py`
- Modify: `src/btbkt/client.py`
- Test: `tests/test_context.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Produces: `BitbucketContext.username: Optional[str]`.
- Produces: `update_participant_status`, `get_pending_review`, `create_pending_comment`, `complete_pending_review`, and `discard_pending_review` methods.

- [ ] **Step 1: Write failing endpoint and context tests**

Add focused assertions equivalent to:

```python
def test_update_participant_status_uses_participant_endpoint_and_commit():
    client, transport = make_client()
    client.update_participant_status(12, "alice-slug", "NEEDS_WORK", "abc123")
    method, url, _headers, body = transport.requests[0]
    assert method == "PUT"
    assert url.endswith("/pull-requests/12/participants/alice-slug")
    assert json.loads(body) == {"status": "NEEDS_WORK", "lastReviewedCommit": "abc123"}

def test_pending_review_methods_use_review_lifecycle_endpoints():
    client, transport = make_client()
    client.get_pending_review(12, limit=25)
    client.create_pending_comment(12, text="Needs tests.")
    client.complete_pending_review(12, participant_status="NEEDS_WORK", last_reviewed_commit="abc123")
    client.discard_pending_review(12)
    assert [(r[0], urlparse(r[1]).path) for r in transport.requests] == [
        ("GET", "/rest/api/1.0/projects/ABC/repos/demo/pull-requests/12/review"),
        ("POST", "/rest/api/1.0/projects/ABC/repos/demo/pull-requests/12/comments"),
        ("PUT", "/rest/api/1.0/projects/ABC/repos/demo/pull-requests/12/review"),
        ("DELETE", "/rest/api/1.0/projects/ABC/repos/demo/pull-requests/12/review"),
    ]
    assert json.loads(transport.requests[1][3]) == {"text": "Needs tests.", "state": "PENDING"}
```

Also assert that the `BitbucketContext` returned from the existing explicit test inputs has `username == "alice"` without exposing the credential value.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
pytest -q tests/test_context.py tests/test_client.py
```

Expected: failures because the context field and endpoint methods do not exist and old decision methods still call `/review`.

- [ ] **Step 3: Implement the minimal client/context contracts**

Add `username` to the frozen context dataclass and return it from `resolve_context`. Implement the five endpoint methods with existing `_request` and `_repo_path` helpers. Validate status against `APPROVED`, `UNAPPROVED`, and `NEEDS_WORK`; include `lastReviewedCommit` only when present. Remove or redirect the old standalone helpers so none invoke `complete_pending_review`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: PASS.

### Task 2: CLI Decision And Pending-Review Workflows

**Files:**
- Modify: `src/btbkt/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1 client methods and `context.username`.
- Produces: direct decision orchestration, `review-pending`, `review-submit`, `review-discard`, and structured partial results.

- [ ] **Step 1: Write failing tests for all observable branches**

Create synthetic transports that return a PR shaped as:

```python
PULL_REQUEST = {
    "id": 42,
    "fromRef": {"latestCommit": "abc123"},
    "reviewers": [{"user": {"name": "alice", "slug": "alice-slug"}}],
}
```

Cover these exact expectations:

Use one parametrized test for `approve -> APPROVED`, `unapprove -> UNAPPROVED`, and `needs-work -> NEEDS_WORK`; every case asserts a GET PR followed by a PUT to `participants/alice-slug` whose body contains the expected status and `abc123`. Add separate tests that assert decision-only review uses the same path, comment-only review sends a public comment, combined review orders GET PR / GET review / POST pending comment / PUT review, completion failure exits 1 with the successful comment step retained, an existing pending comment exits 1 without POST, recovery commands map to GET / PUT / DELETE review, and a submit 404 preserves `NoSuchPullRequestReviewException` in stderr.

Use a transport-raised `BitbucketAPIError` with a body containing `NoSuchPullRequestReviewException` to prove the error remains visible.

- [ ] **Step 2: Run the new CLI tests and verify RED**

Run each new test node or `pytest -q tests/test_cli.py -k 'review or approve or unapprove or needs_work'`. Expected: endpoint/order/output assertions fail against the old one-request `/review` path.

- [ ] **Step 3: Implement identity, decision, and multi-step helpers**

Add small helpers with these exact signatures: `_pull_request_commit(pull_request: Mapping[str, Any]) -> Optional[str]`, `_current_user_slug(pull_request: Mapping[str, Any], username: str) -> str`, `_decision_status(args: argparse.Namespace) -> Optional[str]`, `_update_review_status(client, context, pr_id: int, status: str) -> Any`, and `_combined_review(client, context, pr_id: int, comment: str, status: str) -> CommandResult`.

`_combined_review` must preflight `GET /review`, stop before POST when returned `values` is non-empty, and catch only `BitbucketAPIError` around the completion step so the successful pending-comment response remains in stdout. Its failure payload contains `status`, ordered `steps`, and concrete `recovery` commands.

- [ ] **Step 4: Add parser/dispatch recovery commands**

Add `review-pending`, `review-submit`, and `review-discard`. Enforce exactly one decision flag for submit and review. Keep comment-only review as the existing public-comment operation.

- [ ] **Step 5: Run focused and full CLI tests**

Run:

```bash
pytest -q tests/test_cli.py
```

Expected: PASS with no old `/review` assertions remaining for standalone decisions.

### Task 3: Controlled Raw CLI

**Files:**
- Modify: `src/btbkt/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: existing `BitbucketClient.raw`.
- Produces: `btbkt raw METHOD /rest/... [--json JSON]` without project/repo requirements.

- [ ] **Step 1: Write failing raw CLI tests**

Cover GET without project/repo, PUT with strict JSON decoding, rejection of an absolute URL, rejection of `/plugins/...`, rejection of malformed JSON, and help output. Assert no transport call on validation failure.

- [ ] **Step 2: Verify RED**

Run `pytest -q tests/test_cli.py -k raw`. Expected: parser rejects the missing resource and tests fail.

- [ ] **Step 3: Implement the constrained parser and dispatch**

Add the top-level resource and method choices. Require a path beginning `/rest/` and reject `://`. Parse `--json` with `json.loads`; do not accept custom headers or absolute targets. Update `_context_requirements` so raw still requires auth/base URL but not project/repo.

- [ ] **Step 4: Verify GREEN**

Run the raw-focused tests and then all `tests/test_cli.py`. Expected: PASS.

### Task 4: Canonical Skill And Exact Sync

**Files:**
- Create: `scripts/sync_skill.py`
- Create: `tests/test_sync_skill.py`
- Modify: `skills/using-btbkt-pr-workflows/SKILL.md`
- Modify: `Makefile`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-23-btbkt-review-loop-design.md`

**Interfaces:**
- Produces: `sync_skill(source: Path, destination: Path) -> None` and `skills_match(source: Path, destination: Path) -> bool`.
- Produces: `python scripts/sync_skill.py [--check] [--destination PATH]`.

- [ ] **Step 1: Establish RED for skill guidance**

Use the current repository skill as the known failing reference case: it recommends combined `pr review` without explaining pending creation, lacks failed-mutation state re-reading, lacks the shell bootstrap rule, and promises raw fallback without a CLI command. Record these assertions in `tests/test_sync_skill.py` or a focused skill-contract test before changing the skill.

- [ ] **Step 2: Write failing sync/check tests**

Test copying a directory tree to a temporary destination, `--check` success after sync, `--check` failure after changing one file, and safe replacement of a destination symlink without following it recursively.

- [ ] **Step 3: Verify RED**

Run `pytest -q tests/test_sync_skill.py`. Expected: import/file-not-found failure because the script does not exist, plus the old guidance contract fails.

- [ ] **Step 4: Implement sync and check mode**

Use only `argparse`, `filecmp`/`hashlib`, `pathlib`, `shutil`, and `tempfile` as needed. Resolve the canonical source relative to the script, default the destination to `~/.agents/skills/using-btbkt-pr-workflows`, replace a symlink itself rather than traversing it, copy via a sibling temporary directory, and make `--check` read-only with nonzero exit when trees differ.

- [ ] **Step 5: Rewrite canonical workflow guidance**

Merge the installed shell-bootstrap rule and repository reply/diff guidance. Replace combined-review advice with public-comment, direct-decision, and pending-review distinctions. Document `review-pending`, `review-submit`, `review-discard`, raw fallback, partial-failure re-reading, and the prohibition on blindly resending comments. Keep `page.last` and default-target fallback correct.

- [ ] **Step 6: Add Makefile/README commands and correct the old non-goal**

Add `skill-sync` and `skill-check` targets, install instructions, and raw/review examples. Narrow the earlier design's “no broad raw escape hatch” statement to that historical review-loop scope so it does not contradict the controlled command.

- [ ] **Step 7: Verify GREEN**

Run:

```bash
pytest -q tests/test_sync_skill.py
python scripts/sync_skill.py --check --destination /tmp/nonexistent-skill
```

Expected: pytest PASS; explicit check returns nonzero for the intentionally missing destination.

### Task 5: Review, Full Verification, And Local Skill Deployment

**Files:**
- Review all changed files.
- External write after approval already granted by the handoff: `~/.agents/skills/using-btbkt-pr-workflows`.

- [ ] **Step 1: Run focused quick review and tester passes required by `AGENTS.md`**

Dispatch `quick_reviewer`; if clean, dispatch `reviewer`. Dispatch `tester` because behavior and tests changed. Resolve high/medium findings or state accepted residual risk.

- [ ] **Step 2: Run full verification**

```bash
pytest -q
python -m compileall -q src tests scripts
PYTHONPATH=src python -m btbkt --help
PYTHONPATH=src python -m btbkt pr --help
PYTHONPATH=src python -m btbkt pr review --help
PYTHONPATH=src python -m btbkt raw --help
git diff --check
```

Expected: every command exits 0 with no warnings or syntax failures.

- [ ] **Step 3: Install canonical skill and prove parity**

Run `python scripts/sync_skill.py`, then `python scripts/sync_skill.py --check`. Compare recursive hashes or the script's manifest output. Expected: sync succeeds and check exits 0.

- [ ] **Step 4: Confirm editable install observes the fix**

Run `/Users/cuiziqi/miniconda3/bin/btbkt pr review --help` and `/Users/cuiziqi/miniconda3/bin/btbkt raw --help`. Expected: installed CLI exposes pending-review and raw contracts from the edited checkout.

- [ ] **Step 5: Report without committing**

Summarize changed contracts, exact test results, installed-skill parity, and any residual risk. Leave the worktree uncommitted.
