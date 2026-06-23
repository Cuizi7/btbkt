# btbkt PR Review Loop Semantics Design

Date: 2026-06-23

## Context

`btbkt` is an agent-facing Bitbucket Data Center / Server CLI. Its highest-value PR commands are compact workflow surfaces such as `pr review-summary`, `pr review-comments`, `pr review-context`, and `pr reply`.

Current code already reads review comments and diff context well enough for many review tasks, but it has one critical write-back gap: `pr reply` posts to `/pull-requests/{pr}/comments/{comment}/replies`. On the target Bitbucket Server this returns 404. The available compatible operation is to create a comment under the PR comments endpoint with a `parent.id` payload:

```json
{"parent": {"id": 15466}, "text": "Fixed and covered by tests."}
```

The second practical gap is semantic. Bitbucket can leave a comment in `OPEN` state even after a reply is posted. Agents that report only `open_comments` can incorrectly say comments remain unhandled when they were actually replied to.

## Critical Assessment Of The Suggestions

The suggestions are directionally correct, but not all should be accepted literally.

- Fixing `btbkt pr reply` is fully accepted. It is a correctness bug, and the CLI must preserve review-thread semantics. It must not silently fall back to a top-level PR comment.
- Adding reply-aware review summary counts is fully accepted. `OPEN` state and "has been replied to" are separate facts and must be represented separately.
- Adding batch replies is accepted with guardrails. It is useful for agents, but it is mutating and needs validation, dry-run support, and per-comment result reporting.
- Adding diff context around comments is accepted as a workflow need, but the interface should avoid uncontrolled output growth and repeated broad diff fetches.
- Adding `addressed-comments` is adjusted, not accepted as named. "Addressed" is not a Bitbucket state. A safer concept is a reply-status view that says whether each open comment has replies, who last replied, and whether it appears unreplied.
- Updating the companion skill is accepted. The skill should teach the Bitbucket semantics explicitly so future agents do not confuse open state with unresolved work.

## Goals

1. Make `btbkt pr reply` reliably write into the original review thread on the current Bitbucket Server.
2. Make compact review output distinguish open comments from unreplied comments.
3. Give agents a compact path from vague comments to nearby diff context.
4. Provide a safer batch reply command for the common "fix N comments, then reply to each" workflow.
5. Update the local workflow skill so agents report final PR status precisely.

## Non-Goals

- Do not implement a general Bitbucket comment resolution workflow unless the API and local server behavior are verified.
- Do not rename Bitbucket's `OPEN` / `RESOLVED` states or claim a replied comment is resolved.
- Do not add a broad raw REST escape hatch for this workflow.
- Do not make top-level comments as an automatic fallback for failed replies.

## API And Command Design

### `btbkt pr reply`

`btbkt pr reply PR_ID COMMENT_ID --text TEXT` keeps its existing CLI shape, but changes the REST request:

- Method: `POST`
- Path: `/rest/api/1.0/projects/{project}/repos/{repo}/pull-requests/{pr}/comments`
- Body: `{"text": TEXT, "parent": {"id": COMMENT_ID}}`

The client method should remain named `reply_to_comment` because that is the domain operation. The endpoint detail stays isolated in `client.py`.

If the server returns 404, the error should remain explicit. The CLI should not retry as a top-level comment. The skill should tell agents that a 404 here means the CLI/server reply implementation is incompatible or permissions/context are wrong.

### Reply Status Fields

Compact comments should include derived reply metadata:

```json
{
  "id": 15466,
  "state": "OPEN",
  "text": "提高优先级",
  "replies": [...],
  "reply_count": 1,
  "has_replies": true,
  "latest_reply_author": "alice",
  "latest_reply_created": "2026-06-23T07:30:00Z"
}
```

`review-summary` should add counts that preserve Bitbucket state and reply state separately:

```json
{
  "open_comments": 8,
  "open_comments_with_replies": 8,
  "open_comments_without_replies": 0
}
```

The counts apply only to the returned activity page, matching existing `--state` and pagination semantics. The output must keep page metadata visible so agents cannot overclaim global completeness when `page.activities.last` is false.

### Comment Context

For vague comments, agents need nearby diff context. The preferred CLI addition is:

```bash
btbkt pr review-comments PR_ID --state OPEN --with-diff-context 5
```

The option should:

- Preserve the existing compact comment list.
- Add a small `diff_context` field only for comments with path/line anchors.
- Reuse existing diff compaction logic from `compact.py` where practical.
- Fetch diff data once per relevant path, not once per comment.
- Treat `5` as a line radius within the matched diff hunk: include up to 5 diff content lines before the anchored line, the anchored line when it can be matched, and up to 5 diff content lines after it. Do not cross into another hunk to satisfy the radius.
- Match comment anchors using Bitbucket's `fileType` side when available: `FROM` maps to source lines and `TO` maps to destination lines. Fall back to `lineType` only when `fileType` is absent.
- Omit `diff_context` and include a concise reason when a comment has no path/line anchor or the hunk cannot be matched.

This is intentionally narrower than full `review-context`. Agents should still use `btbkt pr review-context PR_ID --path PATH --max-diff-lines N` when they need broader file context.

### Batch Replies

Add:

```bash
btbkt pr reply-many PR_ID --input replies.json
btbkt pr reply-many PR_ID --input replies.json --dry-run
btbkt pr reply-many PR_ID --input replies.json --continue-on-error
```

Input is a JSON array:

```json
[
  {"comment_id": 15466, "text": "Fixed and covered by tests."}
]
```

Validation rules:

- The input must be a JSON array.
- Each item must contain integer `comment_id` and non-empty string `text`.
- Unknown fields fail validation before any network write, so misspelled agent output is caught early.
- `--dry-run` performs validation and returns the planned replies without making requests.

Execution rules:

- Replies are posted sequentially using the same `reply_to_comment` implementation as `pr reply`.
- Results are reported per comment with `comment_id`, `status`, and either `response` or `error`.
- Default behavior stops on the first failed request.
- `--continue-on-error` attempts the remaining replies and reports all failures.

The final implementation should preserve current CLI JSON behavior. If supporting nonzero exit status with partial-result JSON requires a small internal dispatch result wrapper, keep it local to `cli.py`.

### Reply Status View

Do not add `addressed-comments` as a first-class command name in the initial design. The term is too likely to be confused with Bitbucket resolution state.

If a compact final-check view is added, name it around the actual semantics, for example:

```bash
btbkt pr reply-status PR_ID --state OPEN
```

This command would be a focused projection over review comments:

- `comment_id`
- `state`
- `path`
- `line`
- `author`
- `has_replies`
- `reply_count`
- `latest_reply_author`
- `latest_reply_created`
- `appears_unreplied`

This is optional. `review-summary` reply counts and enhanced `review-comments` may be enough for the first implementation.

## Skill Updates

Update `skills/using-btbkt-pr-workflows/SKILL.md` with these rules:

- Bitbucket `OPEN` comment state does not mean the comment is unhandled. A reply may leave the comment open.
- Final reports should distinguish:
  - open comments count
  - open comments with replies
  - open comments without replies
  - blockers
  - verification result
  - PR source and target commits
- If `btbkt pr reply` returns 404, do not post a top-level fallback comment unless the user explicitly accepts that degraded behavior.
- For short or ambiguous review comments, inspect nearby diff context before changing code or replying.
- Prefer `reply-many --dry-run` before batch posting multiple replies.

## Testing Plan

Add focused pytest coverage following existing patterns:

- `tests/test_client.py`: `reply_to_comment` posts to `/comments` with `parent.id`.
- `tests/test_cli.py`: `pr reply` uses the new endpoint and body.
- `tests/test_compact.py`: compact comments expose reply metadata and summary counts distinguish open-with-replies from open-without-replies.
- `tests/test_cli.py`: `reply-many --dry-run` validates input without transport calls.
- `tests/test_cli.py`: `reply-many` posts sequential replies and reports per-comment results.
- `tests/test_cli.py` or `tests/test_compact.py`: `review-comments --with-diff-context` attaches bounded context only where anchors can be matched.
- `skills/using-btbkt-pr-workflows/SKILL.md`: documentation examples reflect the new final-check semantics.

Use existing verification baseline:

```bash
pytest -q
PYTHONPATH=src python -m btbkt --help
PYTHONPATH=src python -m btbkt pr --help
PYTHONPATH=src python -m btbkt pr reply --help
python -m compileall -q src tests
```

## Risks And Mitigations

- Batch replies can partially mutate remote state. Mitigate with input validation before network writes, `--dry-run`, per-comment result output, and clear stop/continue semantics.
- Reply status can be overinterpreted. Mitigate by naming fields around observable facts: `has_replies`, `reply_count`, and `open_comments_without_replies`.
- Diff context can bloat output. Mitigate with an explicit line radius and path-grouped diff fetching.
- Pagination can still hide comments on later pages. Mitigate by preserving existing page metadata and documenting that counts are page-scoped unless all pages are fetched.

## Acceptance Criteria

- `btbkt pr reply` uses the parent-comment payload and no longer calls `/comments/{id}/replies`.
- Reply metadata appears on compact comments when replies exist.
- `review-summary` reports open comments with and without replies separately.
- Batch reply input can be validated with `--dry-run`, and real batch execution reports item-level outcomes.
- The skill tells agents not to equate Bitbucket `OPEN` with "unhandled".
- Tests cover endpoint shape, compact output shape, batch reply validation/execution, and the ambiguous-comment context path.
