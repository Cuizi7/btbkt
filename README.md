# btbkt

`btbkt` is an agent-facing CLI for Bitbucket Data Center / Server.

It is a thin wrapper over Bitbucket's REST API, with a small set of higher-level
PR commands that return compact JSON for agent workflows: opening PRs, reviewing
others' PRs, reading review comments, and responding after code changes.

## Install

```bash
python -m pip install -e .
```

Run without installing:

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

## Skill

Agent workflow guidance lives here:

```text
skills/using-btbkt-pr-workflows/SKILL.md
```

Use that skill when an agent needs to create a PR, review someone else's PR, or
address review feedback on its own PR.

## Examples

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

Read unresolved review comments and reply after fixing:

```bash
btbkt pr review-comments 390 --state OPEN
btbkt pr reply 390 15450 --text "Fixed and covered by tests."
```

Submit a review decision:

```bash
btbkt pr review 390 --comment "Looks good." --approve
btbkt pr review 390 --comment "Please add a regression test." --needs-work
```

## Notes

`btbkt` prints JSON. The high-level PR commands intentionally omit large raw
Bitbucket payloads and include pagination metadata when the result may be partial.
