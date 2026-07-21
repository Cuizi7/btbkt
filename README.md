# btbkt

[![PyPI](https://img.shields.io/pypi/v/btbkt.svg)](https://pypi.org/project/btbkt/)
[![Python Versions](https://img.shields.io/pypi/pyversions/btbkt.svg)](https://pypi.org/project/btbkt/)
[![Wheel](https://img.shields.io/pypi/wheel/btbkt.svg)](https://pypi.org/project/btbkt/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

`btbkt` is an agent-facing CLI for Bitbucket Data Center / Server.

It is a thin wrapper over Bitbucket's REST API, with a small set of higher-level
repository and PR commands that return compact JSON for agent workflows:
credential-safe clone/fetch/update, opening PRs, reviewing others' PRs, reading
review comments, and responding after code changes.

## Install

For normal use:

```bash
python -m pip install btbkt
```

This installs the `btbkt` command:

```bash
btbkt --help
```

For local development from a source checkout:

```bash
python -m pip install -e .
```

Run from source without installing:

```bash
PYTHONPATH=src python -m btbkt --help
```

Python `>=3.9` is supported.

## Authentication

Only these environment variables are read for authentication:

```bash
export BITBUCKET_BASE_URL="https://bitbucket.internal"
export BITBUCKET_USERNAME="alice"
export BITBUCKET_TOKEN="..."
```

`BITBUCKET_PASSWORD` can be used instead of `BITBUCKET_TOKEN`.

The CLI always sends HTTP Basic auth using `BITBUCKET_USERNAME` plus
`BITBUCKET_PASSWORD` or `BITBUCKET_TOKEN`. Project, repo, source branch, and
target branch are not read from env; they come from CLI flags or the current git
checkout.

Repository commands also use the token/password for HTTP(S) Git authentication.
`btbkt` creates and cleans up its internal askpass resources; credentials are not
put in clone URLs, remotes, Git config, or command arguments. SSH clone links use
the caller's existing SSH agent or key. Plain HTTP is supported for compatibility
and reported with a warning because credentials are sent without TLS.

## Skill

Agent workflow guidance lives here:

```text
skills/using-btbkt-pr-workflows/SKILL.md
```

Use that skill when an agent needs to create a PR, review someone else's PR, or
address review feedback on its own PR.

Install the canonical repository copy for local agents and check for drift:

```bash
make skill-sync
make skill-check
```

The sync replaces the installed skill directory with an exact copy. To inspect
a different destination without changing it, run
`python scripts/sync_skill.py --check --destination PATH`.

## Examples

Clone a branch without constructing a clone URL or askpass helper:

```bash
btbkt --project TRAD --repo trading repo clone \
  --branch master \
  /home/runner/codebases/bitbucket/TRAD/trading
```

Idempotently maintain a long-lived checkout. A missing/empty path is cloned; an
existing matching checkout is fetched and updated only when the current branch
is clean and can be fast-forwarded:

```bash
btbkt --project TRAD --repo trading repo ensure \
  --ref master \
  /home/runner/codebases/bitbucket/TRAD/trading
```

Fetch a tag, commit, or PR source without changing an existing worktree:

```bash
btbkt --project TRAD --repo trading repo fetch --tag v1.2.3 PATH
btbkt --project TRAD --repo trading repo fetch --commit FULL_COMMIT_SHA PATH
btbkt --project TRAD --repo trading repo fetch --pr 390 PATH
```

Without an explicit ref, repository commands use Bitbucket's configured remote
default branch. Dirty, detached, or different-branch checkouts may fetch but are
not reset, switched, or overwritten; `repo ensure` reports that case as a
nonzero partial result with recovery guidance.

Find the PR for the current git branch:

```bash
btbkt pr current
```

Open a PR from the current branch:

```bash
btbkt pr create --title "feat: add config schema" --description "Adds schema validation." --reviewer alice
```

Start reviewing a PR:

```bash
btbkt pr review-summary 390
btbkt pr review-context 390 --path src/app.py --max-diff-lines 120
```

Read unresolved review comments, inspect terse comments with nearby diff context,
and reply after fixing:

```bash
btbkt pr review-comments 390 --state OPEN --with-diff-context 5
btbkt pr reply 390 15450 --text "Fixed and covered by tests."
```

Reply to multiple handled comments from a reviewed JSON file:

```bash
btbkt pr reply-many 390 --input replies.json --dry-run
btbkt pr reply-many 390 --input replies.json
```

`review-summary` reports Bitbucket open state separately from reply state. A
comment can remain `OPEN` after a reply, so use
`open_comments_without_replies` for the remaining-unreplied count.

Post only a public comment, or submit only a direct participant decision:

```bash
btbkt pr review 390 --comment "I have one question about the fallback."
btbkt pr approve 390
btbkt pr needs-work 390
```

When text and a decision belong to one review, use the pending-review lifecycle.
If completion fails after creating the pending comment, inspect state before
submitting or discarding it; do not blindly resend the comment:

```bash
btbkt pr review 390 --comment "Please add a regression test." --needs-work
btbkt pr review-pending 390
btbkt pr review-submit 390 --needs-work
btbkt pr review-discard 390
```

Use the controlled raw surface only when a compact command cannot express the
required operation. Paths must begin with `/rest/`, and mutating methods require
the same explicit authorization as other writes:

```bash
btbkt raw GET /rest/api/1.0/projects/PROJ/repos/demo/pull-requests/390
```

## Notes

`btbkt` prints JSON. The high-level PR commands intentionally omit large raw
Bitbucket payloads and include pagination metadata when the result may be partial.
