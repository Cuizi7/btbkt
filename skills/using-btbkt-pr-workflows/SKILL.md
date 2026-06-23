---
name: using-btbkt-pr-workflows
description: Guide agent-friendly Bitbucket Data Center / Server pull request workflows with the btbkt CLI. Use when Codex needs to create or find a PR for a source branch, review another PR, inspect compact PR context, read or answer review comments, check reviewer state, submit approve/needs-work decisions, or perform final PR readiness checks while preserving pagination and credential safety.
---

# Using btbkt PR Workflows

## Core Rules

- Provide repository context either from the current git checkout or with explicit global flags. Running inside the checkout is a convenience, not a requirement.
- Use global flags before the resource when context is not inferable: `btbkt --project PROJECT --repo REPO --source-branch BRANCH --target-branch BRANCH pr ...`.
- Keep credentials out of command text, logs, PR descriptions, and comments. Authentication comes from `BITBUCKET_BASE_URL`, `BITBUCKET_USERNAME`, and `BITBUCKET_TOKEN` or `BITBUCKET_PASSWORD`; never print those values.
- Prefer compact high-level PR commands. Use raw REST-shaped commands only when the compact command cannot answer the question.
- Treat all output as JSON. Inspect `counts`, `page`, `pull_request.from`, `pull_request.to`, reviewers, blockers, comments, and truncation fields before concluding.
- Treat `create`, `comment`, `task`, `reply`, `review`, `approve`, `unapprove`, `needs-work`, `merge`, `decline`, and `reopen` as externally visible mutating commands. Run them only when the user asked for that action or it is the explicit next step in the assigned PR workflow.

## Command Choice

| Need | Use first |
| --- | --- |
| Find existing PR for source branch | `btbkt pr current` |
| Create PR | `btbkt pr create --title TITLE --description TEXT [--reviewer USER]` |
| Enter, triage, or final-check a PR | `btbkt pr review-summary PR_ID --state OPEN` |
| Read all review comments from activity stream | `btbkt pr review-comments PR_ID --state OPEN` |
| Only check reviewer state | `btbkt pr review-status PR_ID` |
| Inspect files and capped diff | `btbkt pr review-context PR_ID [--path PATH] [--max-diff-lines N]` |
| Reply after fixes | `btbkt pr reply PR_ID COMMENT_ID --text TEXT` |
| Submit review decision with context | `btbkt pr review PR_ID --comment TEXT --approve|--needs-work|--unapprove` |
| List changed files only | `btbkt pr changes PR_ID` |
| Add a new top-level comment | `btbkt pr comment PR_ID --text TEXT` |
| Create blocker task | `btbkt pr task PR_ID --text TEXT [--anchor-json JSON]` |
| Explicit merge/decline/reopen | `btbkt pr merge|decline|reopen PR_ID [--version VERSION]` |

Notes:

- `btbkt pr comments` requires `--path`; do not use it to discover all PR comments. Use `review-comments` or `activities` for global discovery.
- `review-context` defaults to unified diff text capped at 200 lines. Use `--path` and a larger `--max-diff-lines` for focused follow-up. Use `--diff-format structured` only when parsed hunk JSON is needed.
- If a listed compact command is missing, assume the installed CLI is stale or incomplete; report the command gap instead of falling back to noisy raw payload dumps.

## Workflow: Code Then Open PR

1. Establish context from the repo checkout or explicit global flags: `--project`, `--repo`, `--source-branch`, and, when needed, `--target-branch`.
2. Implement the code and run relevant verification.
3. Run `btbkt pr current`, or `btbkt --project PROJECT --repo REPO --source-branch BRANCH pr current` outside a checkout. If `count > 0`, use the existing PR and do not open a duplicate.
4. If no PR exists, run `btbkt pr create --title TITLE --description TEXT [--reviewer USER]`, adding explicit global flags or `--source`/`--target` when not relying on git inference. Add multiple reviewers by repeating `--reviewer`.
5. Run `btbkt pr review-summary PR_ID --state OPEN`, again with explicit `--project` and `--repo` when outside a checkout, and confirm `pull_request.from`, `pull_request.to`, reviewers, open comments, and open blockers.
6. Report PR id, branch pair, verification, reviewers, open comments/blockers, and any missing context.

## Workflow: Review Someone Else's PR

1. Start with `btbkt pr review-summary PR_ID --state OPEN`.
2. Identify branch pair, author, reviewers, open comments, open blockers, review events, and pagination.
3. Inspect code with `btbkt pr review-context PR_ID`. If the diff is broad or `counts.diff_truncated` is true, rerun with `--path PATH --max-diff-lines N` for relevant files.
4. Follow pagination for partial activity, blocker, or changes pages before claiming the PR has no comments or blockers.
5. Use `btbkt pr review PR_ID --comment TEXT --needs-work` for required changes, or `--approve` when clean and verified.
6. Keep feedback specific: path, risk, requested change, and expected verification.

## Workflow: Address Review On Your PR

1. Run `btbkt pr review-comments PR_ID --state OPEN`.
2. Group comments by file and fix code.
3. Run relevant checks before replying.
4. Reply to each handled thread with `btbkt pr reply PR_ID COMMENT_ID --text TEXT`; include what changed and which verification passed.
5. Run `btbkt pr review-summary PR_ID --state OPEN`.
6. If open comments or blockers remain, report them explicitly instead of saying the PR is clean.

## Pagination And Partial Results

- Compact commands include `page` metadata. If `page.last` is false, or a nested page under `activities`, `blockers`, or `changes` is not last, rerun the same command with `--start NEXT_PAGE_START`.
- `--state OPEN` on compact commands is a local filter over the returned page. It does not prove there are no open comments on later pages.
- `btbkt pr current` scans outgoing PR pages until it finds a branch match or reaches the last page; inspect `scan.stopped` if the result is surprising.
- `review-summary` combines PR status, activity comments, blocker tasks, review events, and reviewer counts. It is the safest first and last check.

## Context And Error Handling

- If `btbkt` reports missing context, pass the missing global flags or run inside a Bitbucket git checkout; do not guess project or repo names.
- If source branch is missing, pass `--source` to `pr create` or `--source-branch` globally.
- If target branch is not explicit, `pr create` uses the inferred default branch when available and uses `main` only as the final fallback; pass `--target` or `--target-branch` when the intended base is different.
- For auth, network, or permission failures, report the failing command, HTTP/status context if present, and the missing access boundary without exposing secrets.

## Mistakes To Avoid

- Dumping full PR payloads when `review-summary` is enough.
- Using path-scoped comment listing to discover all PR comments. Use `review-comments`.
- Treating local `--state` filtering as global when `page.*.last` is false. Follow pagination.
- Printing `BITBUCKET_TOKEN`, `BITBUCKET_PASSWORD`, or credentialed remotes.
- Creating a new PR before checking `btbkt pr current`.
- Posting approval or needs-work before inspecting `review-summary` and relevant `review-context`.
- Ignoring `counts.diff_truncated` or paginated `changes` output when reviewing large PRs.
