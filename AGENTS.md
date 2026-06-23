# Repository Guidelines

## Project Structure & Module Organization

This is a Python package using a `src/` layout. Runtime code lives under `src/btbkt/`:

- `cli.py`: argparse entrypoint and command dispatch.
- `client.py`: thin Bitbucket REST transport wrapper.
- `context.py`: env/git context discovery and auth header construction.
- `compact.py`: agent-facing compact output models.

Tests live in `tests/`. Agent workflow guidance lives in `skills/using-btbkt-pr-workflows/SKILL.md`. Do not commit generated caches such as `__pycache__/`, `.pytest_cache/`, or `*.egg-info/`.

## Build, Test, and Development Commands

Install locally:

```bash
python -m pip install -e .
```

Run without install:

```bash
PYTHONPATH=src python -m btbkt --help
```

Run tests:

```bash
pytest -q
python -m compileall -q src tests
```

`pytest -q` is the primary behavior check. `compileall` catches syntax/import issues quickly.

## Coding Style & Naming Conventions

Support Python `>=3.9`. Keep changes small and idiomatic. Use 4-space indentation, explicit names, and standard-library tools unless the project already depends on something else. Prefer pure functions in `compact.py` for response shaping, and keep network access isolated in `client.py`.

## Testing Guidelines

Use pytest. Add focused tests for every new command, option, and compact output contract. CLI tests should use synthetic transports instead of real network calls. Keep compatibility coverage in `tests/test_python_compat.py` passing.

## Design Principles & Architecture Notes

`btbkt` should remain a thin wrapper over official Bitbucket REST behavior, but agent workflows should use high-level commands first: `pr current`, `pr review-summary`, `pr review-comments`, `pr review-status`, and `pr review-context`. Avoid dumping raw Bitbucket payloads into agent context. Compact outputs must include enough IDs, paths, line numbers, state, pagination, and reviewer status for follow-up actions.

Prefer local git inference for project/repo/branch. Only authentication should come from env: `BITBUCKET_BASE_URL`, `BITBUCKET_USERNAME`, and either `BITBUCKET_TOKEN` or `BITBUCKET_PASSWORD`.

## Commit & Pull Request Guidelines

History currently uses short imperative summaries, for example `Add agent-facing Bitbucket CLI`. Keep commits scoped and descriptive. PRs should include: purpose, user-facing command changes, test results, and any compatibility or security notes.

## Security & Configuration Tips

Never print tokens, passwords, Basic auth headers, or credentialed remotes. For live Bitbucket checks, use read-only commands unless the user explicitly asks for writes.
