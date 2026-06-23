---
name: using-btbkt-pr-workflows
description: Use when an agent needs to create, review, update, respond to, or check Bitbucket pull requests with the btbkt CLI in a repository workflow.
---

# Using btbkt PR Workflows

## Overview

Use `btbkt` as the PR workflow surface. Prefer compact PR commands, keep secrets out of logs, and do not use lower-level escape hatches unless the user explicitly asks.

## Command Choice

| Need | Use first |
| --- | --- |
| Enter or triage a PR | `btbkt pr review-summary PR_ID` |
| Only read review comments | `btbkt pr review-comments PR_ID --state OPEN` |
| Only check reviewer state | `btbkt pr review-status PR_ID` |
| Find the PR for current branch | `btbkt pr current` |
| Create PR | `btbkt pr create --title TITLE --description TEXT [--reviewer USER]` |
| Inspect files and diff | `btbkt pr review-context PR_ID` |
| Reply after fixes | `btbkt pr reply PR_ID COMMENT_ID --text TEXT` |
| Submit a review decision | `btbkt pr review PR_ID --comment TEXT --approve|--needs-work` |
| Final merge check | `btbkt pr review-summary PR_ID --state OPEN` |

If a listed high-level command is missing, assume it is the desired API shape and report it as follow-up work.

## Workflow: Code Then Open PR

1. Work from the repo checkout so `btbkt` can infer project, repo, and branch.
2. Implement the code and run relevant verification.
3. Run `btbkt pr current`. If a PR exists, update the final note around that PR instead of opening a duplicate.
4. If none exists, run `btbkt pr create --title TITLE --description TEXT [--reviewer USER]`.
5. Run `btbkt pr review-summary PR_ID` and confirm branch pair.
6. Report PR id, branch pair, verification, reviewers, and blockers.

## Workflow: Review Someone Else's PR

1. Start with `btbkt pr review-summary PR_ID`.
2. Identify open comments, blockers, reviewer status, branch pair, and pagination.
3. For code inspection, run `btbkt pr review-context PR_ID`; focus on relevant files. The default diff is unified text; use `--diff-format structured` only when parsed hunk JSON is needed.
4. Use `btbkt pr review PR_ID --comment TEXT --needs-work` for required changes, or `--approve` when clean.
5. Keep feedback specific: path, risk, requested change, expected verification.

## Workflow: Address Review On Your PR

1. Run `btbkt pr review-comments PR_ID --state OPEN`.
2. Group comments by file and fix code.
3. Run relevant checks before replying.
4. Reply with `btbkt pr reply PR_ID COMMENT_ID --text TEXT`.
5. Run `btbkt pr review-summary PR_ID --state OPEN`.
6. If `page.*.last` is false, follow `page.*.next` with `--start`.

## Common Mistakes

- Dumping full PR payloads when `review-summary` is enough.
- Using path-scoped comment listing to discover all PR comments. Use `review-comments`.
- Treating local `--state` filtering as global when `page.*.last` is false. Follow pagination.
- Printing `BITBUCKET_TOKEN`, `BITBUCKET_PASSWORD`, or credentialed remotes.
- Creating a new PR before checking `btbkt pr current`.
