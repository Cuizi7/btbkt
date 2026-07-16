---
name: using-btbkt-pr-workflows
description: Use when creating, finding, reviewing, repairing, or deciding Bitbucket Data Center pull requests with btbkt, especially when comments, pending reviews, reviewer status, pagination, or mutation recovery affect correctness.
---

# Using btbkt PR Workflows

## Core Principle

Use compact read commands to establish state before and after writes. A public comment, a direct participant decision, and a pending review are different Bitbucket operations; choose the one matching the user's intent and recover from partial writes without duplicating comments.

## Bootstrap And Safety

- Infer project, repository, and branches from the current git checkout, or put explicit global flags before `pr`: `btbkt --project PROJECT --repo REPO --source-branch BRANCH --target-branch BRANCH pr ...`.
- If `btbkt` is missing from `PATH` or required `BITBUCKET_*` variables are missing, run `source ~/.zshrc` and retry. Never print credential values loaded from the shell profile.
- Authentication comes from `BITBUCKET_BASE_URL`, `BITBUCKET_USERNAME`, and `BITBUCKET_TOKEN` or `BITBUCKET_PASSWORD`. Never print tokens, passwords, auth headers, or credentialed remotes.
- Treat `create`, `comment`, `task`, `reply`, `reply-many`, review decisions, pending-review writes, `merge`, `decline`, `reopen`, and non-GET `raw` calls as externally visible mutations. Run them only with user authorization.
- Treat output as JSON. Inspect branch pair, commits, reviewers, comments, blockers, counts, truncation, and `page` metadata before concluding.

## Quick Reference

| Need | Use first |
| --- | --- |
| Find a PR for the source branch | `btbkt pr current` |
| Create a PR | `btbkt pr create --title TITLE --description TEXT [--reviewer USER]` |
| Triage or final-check | `btbkt pr review-summary PR_ID --state OPEN` |
| Read review threads | `btbkt pr review-comments PR_ID --state OPEN [--with-diff-context 5]` |
| Inspect files and capped diff | `btbkt pr review-context PR_ID [--path PATH] [--max-diff-lines N]` |
| Check participant decisions | `btbkt pr review-status PR_ID` |
| Add only a public comment | `btbkt pr review PR_ID --comment TEXT` |
| Set only your participant decision | `btbkt pr approve|unapprove|needs-work PR_ID` |
| Create a pending comment and decision | `btbkt pr review PR_ID --comment TEXT --approve|--unapprove|--needs-work` |
| Inspect pending state | `btbkt pr review-pending PR_ID` |
| Complete existing pending state | `btbkt pr review-submit PR_ID --approve|--unapprove|--needs-work` |
| Discard existing pending state | `btbkt pr review-discard PR_ID` |
| Reply to one thread | `btbkt pr reply PR_ID COMMENT_ID --text TEXT` |
| Safely plan multiple replies | `btbkt pr reply-many PR_ID --input replies.json --dry-run` |
| Controlled REST fallback | `btbkt raw METHOD /rest/... [--json JSON]` |

`review-context` defaults to a unified diff capped at 200 lines. Focus broad
reviews with `--path PATH --max-diff-lines N`; use `--diff-format structured`
only when parsed hunk JSON is required.

## Choose The Correct Review Operation

### Public comment only

Use `btbkt pr review PR_ID --comment TEXT` or `btbkt pr comment PR_ID --text TEXT` when the user wants a normal public comment without changing reviewer status. This is not a pending review.

### Direct participant decision

Use `btbkt pr approve PR_ID`, `btbkt pr unapprove PR_ID`, or `btbkt pr needs-work PR_ID` when the user wants only a reviewer decision. These commands update the authenticated user's participant status directly; they do not require a pending review.

### Pending comment plus decision

Use combined `pr review` only when the user wants one review containing both text and a decision:

```bash
btbkt pr review 390 --comment "Please add a regression test." --needs-work
```

This preflights `GET /review`, creates a comment with Bitbucket state `PENDING`, then completes the pending review. If pending content already exists, the command refuses to create another comment and directs recovery through `review-pending`, `review-submit`, or `review-discard`.

Do not substitute a public comment plus a direct participant decision for this lifecycle: the two writes can be observed separately and do not provide pending-review recovery.

## Mutation Failure And Recovery

After any failed mutation, reread the affected server state before choosing the next write; never assume the failed request made no change. Use the smallest relevant reads:

```bash
btbkt pr review-pending PR_ID
btbkt pr review-status PR_ID
btbkt pr review-summary PR_ID --state OPEN
```

If combined review reports `status: "partial"`, the pending comment was created but completion failed. Preserve the returned step results, inspect `review-pending`, then explicitly submit or discard:

```bash
btbkt pr review-submit PR_ID --needs-work
btbkt pr review-discard PR_ID
```

`NoSuchPullRequestReviewException` means `/review` was asked to complete a pending review that does not exist. Reread pending and participant state; use a direct participant decision if no comment is intended, or start a new combined review if both text and a decision are intended.

Do not blindly resend a public comment, pending comment, reply, task, batch, or combined review after an error or timeout. A request can reach Bitbucket even when the client does not receive a successful response. Reread first and avoid duplicate external writes.

## Workflow: Code Then Open PR

1. Establish checkout context or explicit `--project`, `--repo`, `--source-branch`, and optional `--target-branch` flags.
2. Implement and verify the change.
3. Run `btbkt pr current`. Outside a checkout use `btbkt --project PROJECT --repo REPO --source-branch BRANCH pr current`.
4. If `count > 0`, use the existing PR. Otherwise create it with `btbkt pr create --title TITLE --description TEXT [--reviewer USER]`.
5. Run `btbkt pr review-summary PR_ID --state OPEN` and report the branch pair, commits, reviewers, comments, blockers, verification, and partial-result metadata.

For creation, the target uses an inferred default branch when available and `main` only as the final fallback. Pass `--target` or global `--target-branch` whenever the intended base differs.

## Workflow: Review Someone Else's PR

1. Run `btbkt pr review-summary PR_ID --state OPEN`.
2. Inspect `pull_request.from`, `pull_request.to`, commits, author, reviewer state, open comments, blockers, review events, and pagination.
3. Run `btbkt pr review-context PR_ID`. If `counts.diff_truncated` is true or the change is broad, focus with `--path PATH --max-diff-lines N`.
4. Follow every non-last activity, blocker, or changes page before claiming the PR is clean.
5. Choose a public comment, direct participant decision, or pending comment plus decision using the distinctions above.
6. Reread `review-pending` after a combined-review failure. Finish with `review-status` and `review-summary` after a successful decision.

Feedback should identify the path, risk, requested change, and expected verification.

## Workflow: Address Review On Your PR

1. Run `btbkt pr review-comments PR_ID --state OPEN`.
2. For terse or ambiguous comments, add `--with-diff-context 5`, or use `review-context --path PATH --max-diff-lines N` for broader context.
3. Group comments by file, fix the code, and run relevant checks.
4. Reply to each handled thread with `btbkt pr reply PR_ID COMMENT_ID --text TEXT`.
5. For multiple replies, validate the complete input before any writes:

   ```bash
   btbkt pr reply-many PR_ID --input replies.json --dry-run
   btbkt pr reply-many PR_ID --input replies.json
   ```

6. If a reply or batch partially fails, reread `review-comments`; do not rerun the whole batch blindly.
7. Finish with `review-summary` and report `open_comments`, `open_comments_with_replies`, `open_comments_without_replies`, blockers, commits, and verification.

Bitbucket can keep a replied comment `OPEN`. Do not call every open comment unhandled; reply state and resolution state are different facts.

If `btbkt pr reply` returns 404, do not post a top-level fallback comment unless
the user explicitly accepts that degraded behavior. Diagnose whether the local
Bitbucket Server rejects the reply endpoint or the PR/comment context is wrong.

## Pagination And Partial Results

- Compact commands include `page` metadata. Follow the next page whenever `page.last` is false. Also inspect nested activity, blocker, and changes page objects rather than assuming the top-level result is complete.
- `--state OPEN` filters only the returned page. It does not prove later pages have no open comments.
- `pr current` scans outgoing PR pages until a branch match or the final page; inspect `scan.stopped` when the result is surprising.
- Never claim zero comments, blockers, or changes from a partial page.

## Controlled Raw Fallback

Prefer compact commands. When they cannot express a required Bitbucket operation, inspect `btbkt raw --help` and use the controlled form:

```bash
btbkt raw METHOD /rest/... [--json JSON]
```

Only `GET`, `POST`, `PUT`, `PATCH`, and `DELETE` are accepted, the path must begin with `/rest/`, and absolute URLs are rejected. Raw output may be large and REST shapes may vary by server version. Use read-only `GET` for investigation; obtain authorization before a mutating method, use strict JSON, and reread state after failure. Never add auth material to the path or body.

## Common Mistakes

- Combining comment and decision without understanding the pending-review lifecycle.
- Retrying a failed combined review and duplicating the pending comment instead of inspecting `review-pending`.
- Calling `/review` for a standalone decision instead of updating participant status.
- Dumping raw payloads when `review-summary`, `review-comments`, or `review-context` is enough.
- Using path-scoped `comments` to discover all review comments; use `review-comments` or `activities`.
- Treating page-scoped counts as global when `page.last` is false.
- Treating `open_comments` as unhandled count; retain both open and reply-aware counts.
- Posting a top-level comment when the intended action is a thread reply.
- Guessing from a terse comment without nearby diff context.
- Creating a duplicate PR before running `pr current`.
- Printing secrets or credentialed remotes.
