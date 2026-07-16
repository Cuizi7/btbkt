# btbkt Review Status And Skill Sync Design

Date: 2026-07-16

## Context

`btbkt` currently sends standalone approval decisions and combined review actions to `PUT /pull-requests/{id}/review`. Bitbucket defines that endpoint as completing an existing pending review, so it returns `NoSuchPullRequestReviewException` when the authenticated user has no pending review. The CLI also exposes no raw command even though the Python client and workflow skill imply that capability, and the repository skill has drifted from the copy loaded from `~/.agents/skills`.

## Accepted Model

Use the complete Bitbucket review lifecycle for a comment plus decision, and the participants endpoint for a decision without a pending comment.

- `pr approve`, `pr unapprove`, `pr needs-work`, and `pr review` with only a decision update the authenticated user's participant status.
- `pr review --comment TEXT` without a decision posts a normal public comment.
- `pr review --comment TEXT --approve|--unapprove|--needs-work` creates a pending comment, then completes that pending review with the selected participant status.
- Explicit pending-review read, submit, and discard commands allow recovery after partial failure.

The repository copy of `skills/using-btbkt-pr-workflows/SKILL.md` is the canonical skill source. A checked sync command installs an exact copy under `~/.agents/skills`.

## Client API

Keep REST mechanics in `client.py`:

```python
update_participant_status(
    pr_id: int,
    user_slug: str,
    status: str,
    last_reviewed_commit: Optional[str] = None,
) -> Any

get_pending_review(pr_id: int, *, limit=None, start=None) -> Any
create_pending_comment(pr_id: int, *, text: str, anchor=None) -> Any
complete_pending_review(
    pr_id: int,
    *,
    participant_status: Optional[str] = None,
    last_reviewed_commit: Optional[str] = None,
) -> Any
discard_pending_review(pr_id: int) -> Any
```

`update_participant_status` sends `PUT .../participants/{userSlug}` with `status` and, when available, `lastReviewedCommit`. `complete_pending_review` is the only method that sends `PUT .../review`. Pending comments use the PR comments endpoint with `state: "PENDING"`.

## Identity And Commit Resolution

`BitbucketContext` retains the resolved non-secret username. Before changing reviewer state, the CLI fetches the PR and reads `fromRef.latestCommit`.

The user slug is resolved in this order:

1. Match the configured username against each PR participant's `user.slug` or `user.name`, then use that participant's slug.
2. Fall back to the configured username when no participant match exists; the participants endpoint can add the current user implicitly.

The CLI never decodes the Basic authorization header to recover identity. Supplying `lastReviewedCommit` makes stale decisions fail with a conflict instead of silently reviewing a newer PR revision.

## CLI Contract

Add these recovery surfaces:

```text
btbkt pr review-pending PR_ID [--limit N] [--start N]
btbkt pr review-submit PR_ID --approve|--unapprove|--needs-work
btbkt pr review-discard PR_ID
```

`review-submit` fetches the current PR commit and completes an existing pending review. `review-discard` deletes the authenticated user's pending review.

Before a combined review creates a pending comment, it reads the pending review. If pending comments already exist, it refuses to create another and returns a nonzero result directing the caller to inspect, submit, or discard them. This is the duplicate-comment retry guard.

For a new combined review, output records ordered steps. If comment creation succeeds but completion fails, stdout contains the created pending comment response, the completion error, `status: "partial"`, and recovery commands; exit status is nonzero. A blind retry cannot create a duplicate because the preflight sees the existing pending review.

## Raw Command

Add a top-level command:

```text
btbkt raw METHOD /rest/... [--json JSON]
```

It accepts only `GET`, `POST`, `PUT`, `PATCH`, and `DELETE`, rejects absolute URLs and paths outside `/rest/`, strictly parses `--json`, and uses the existing authenticated client and JSON error handling. Raw requests require base URL and authentication but do not require project or repository context.

## Skill Installation

Merge the installed skill's useful shell-bootstrap rule into the canonical repository skill while preserving newer `reply-many`, diff-context, reply-aware, target-branch, and `page.last` guidance. Update review guidance to distinguish public comments, pending comments, and reviewer status, and require state re-reading after any failed mutation.

Add `scripts/sync_skill.py` plus Makefile targets:

```text
make skill-sync
make skill-check
```

The script copies the canonical skill directory to the configured destination and `--check` compares file content/hashes without writing. It must handle an existing directory or symlink deliberately and never recurse through a self-referential link. Tests use a temporary destination. After verification, run the sync against the real `~/.agents/skills/using-btbkt-pr-workflows` destination and confirm parity.

## Testing

Use synthetic transports and test observable requests and CLI results:

- All three standalone statuses use the participants endpoint and correct body.
- The latest source commit is included and stale-commit conflicts remain visible.
- Decision-only `pr review` uses participants; comment-only review remains a public comment.
- Combined review preflights pending state, creates a pending comment, and completes `/review`.
- Missing pending review produces a visible `NoSuchPullRequestReviewException` error.
- Completion failure reports partial success and preserves the pending comment response.
- Retrying with an existing pending review does not post another comment.
- Pending read, submit, and discard commands map to GET, PUT, and DELETE `/review`.
- Raw parsing, path restrictions, context requirements, and request bodies are covered.
- Skill sync and `--check` prove repository and installed copies can be byte-identical.

Run `pytest -q`, `python -m compileall -q src tests scripts`, CLI help smoke checks, skill hash parity, and `git diff --check`.

## Compatibility And Non-Goals

- Python remains `>=3.9` with no runtime dependencies.
- Existing compact review output contracts remain unchanged.
- No automatic retry of mutating requests is added.
- No public comment is used as a fallback for a failed pending review.
- The implementation is not committed unless explicitly requested.

## Acceptance Criteria

The six affected decision commands no longer use `/review` without a pending review; combined review is recoverable and duplicate-safe; raw fallback is available through the CLI; workflow guidance matches the real API; and the installed skill copy matches the canonical repository source.
