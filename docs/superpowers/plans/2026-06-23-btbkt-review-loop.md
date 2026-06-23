# btbkt Review Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `btbkt` reliably reply to Bitbucket review threads and report comment reply state separately from Bitbucket's `OPEN` state.

**Architecture:** Keep network behavior isolated in `src/btbkt/client.py`, command parsing and orchestration in `src/btbkt/cli.py`, and compact JSON shaping in `src/btbkt/compact.py`. Use existing pytest transport fixtures and pure compact-function tests; do not introduce runtime dependencies.

**Tech Stack:** Python 3.9+, argparse, standard-library JSON/file handling, pytest, Bitbucket Data Center / Server REST API.

---

## Scope

Implement the accepted parts of `docs/superpowers/specs/2026-06-23-btbkt-review-loop-design.md`:

- Fix `pr reply` to post a parent comment through `/pull-requests/{pr}/comments`.
- Add reply-derived fields and summary counts.
- Add `pr reply-many`.
- Add bounded `review-comments --with-diff-context`.
- Update workflow docs.

Do not implement `addressed-comments` or `reply-status` in this plan. The enhanced summary and comments output are the first compact final-check surface.

## File Structure

- Modify `src/btbkt/client.py`: change only the `reply_to_comment` endpoint payload.
- Modify `src/btbkt/compact.py`: add reply metadata, reply-aware counts, and pure diff-context enrichment helpers.
- Modify `src/btbkt/cli.py`: add parser flags/commands and orchestration helpers for batch replies and comment diff context.
- Modify `tests/test_client.py`: assert the exact reply endpoint and payload.
- Modify `tests/test_cli.py`: cover `pr reply`, `reply-many`, and `review-comments --with-diff-context`.
- Modify `tests/test_compact.py`: cover reply metadata, reply-aware summary counts, and pure diff-context enrichment.
- Modify `README.md`: document the new reply and final-check behavior.
- Modify `skills/using-btbkt-pr-workflows/SKILL.md`: teach the review-loop semantics to future agents.

## Task 1: Fix Single Reply Endpoint

**Files:**
- Modify: `src/btbkt/client.py`
- Test: `tests/test_client.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add a failing client endpoint test**

Append this test to `tests/test_client.py`:

```python
def test_reply_to_comment_posts_parent_comment_payload():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    client.reply_to_comment(12, 15466, text="Fixed and covered by tests.")

    method, url, _headers, body = transport.requests[0]
    assert method == "POST"
    assert url == "https://bitbucket.internal/rest/api/1.0/projects/ABC/repos/demo/pull-requests/12/comments"
    assert json.loads(body.decode("utf-8")) == {
        "text": "Fixed and covered by tests.",
        "parent": {"id": 15466},
    }
```

- [ ] **Step 2: Run the new client test and confirm it fails**

Run:

```bash
pytest -q tests/test_client.py::test_reply_to_comment_posts_parent_comment_payload
```

Expected: FAIL because current code posts to `/comments/15466/replies` with body `{"text": ...}`.

- [ ] **Step 3: Change `reply_to_comment` to use the parent payload**

Replace `BitbucketClient.reply_to_comment` in `src/btbkt/client.py` with:

```python
    def reply_to_comment(self, pr_id: int, comment_id: int, *, text: str) -> Any:
        return self._request(
            "POST",
            self._repo_path("pull-requests", str(pr_id), "comments"),
            json_body={"text": text, "parent": {"id": comment_id}},
        )
```

- [ ] **Step 4: Verify the client test passes**

Run:

```bash
pytest -q tests/test_client.py::test_reply_to_comment_posts_parent_comment_payload
```

Expected: PASS.

- [ ] **Step 5: Add a CLI test for `pr reply`**

Append this test to `tests/test_cli.py`:

```python
def test_cli_reply_posts_parent_comment_payload():
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        [
            "--project",
            "ABC",
            "--repo",
            "demo",
            "pr",
            "reply",
            "42",
            "15466",
            "--text",
            "Fixed and covered by tests.",
        ],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    method, url, _headers, body = transport.requests[0]
    assert method == "POST"
    assert url.endswith("/pull-requests/42/comments")
    assert json.loads(body.decode("utf-8")) == {
        "text": "Fixed and covered by tests.",
        "parent": {"id": 15466},
    }
```

- [ ] **Step 6: Run the CLI reply test**

Run:

```bash
pytest -q tests/test_cli.py::test_cli_reply_posts_parent_comment_payload
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

Run:

```bash
git add src/btbkt/client.py tests/test_client.py tests/test_cli.py
git commit -m "Fix Bitbucket review thread replies"
```

Expected: commit succeeds with only these three files staged for this task.

## Task 2: Add Reply Metadata And Summary Counts

**Files:**
- Modify: `src/btbkt/compact.py`
- Test: `tests/test_compact.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add compact tests for reply metadata**

Append this test to `tests/test_compact.py`:

```python
def test_compact_review_comments_adds_reply_metadata():
    activities = {
        "values": [
            {
                "comment": {
                    "id": 15466,
                    "text": "提高优先级",
                    "state": "OPEN",
                    "author": {"name": "reviewer"},
                    "comments": [
                        {
                            "id": 15480,
                            "text": "已提高优先级，并补了测试。",
                            "state": "OPEN",
                            "author": {"name": "alice"},
                            "createdDate": 1000,
                        },
                        {
                            "id": 15481,
                            "text": "补充说明验证命令。",
                            "state": "OPEN",
                            "author": {"name": "alice"},
                            "createdDate": 2000,
                        },
                    ],
                },
                "commentAnchor": {"path": "src/app.py", "line": 8, "lineType": "ADDED"},
            }
        ]
    }

    result = compact_review_comments(activities)

    comment = result["comments"][0]
    assert comment["reply_count"] == 2
    assert comment["has_replies"] is True
    assert comment["latest_reply_author"] == "alice"
    assert comment["latest_reply_created"] == "1970-01-01T00:00:02Z"
```

- [ ] **Step 2: Add summary-count coverage**

Append this test to `tests/test_compact.py`:

```python
def test_compact_review_summary_counts_open_comments_with_and_without_replies():
    pull_request = {"id": 390, "state": "OPEN", "reviewers": []}
    activities = {
        "values": [
            {
                "comment": {
                    "id": 1,
                    "text": "Already handled.",
                    "state": "OPEN",
                    "author": {"name": "reviewer"},
                    "comments": [{"id": 10, "text": "Fixed.", "author": {"name": "alice"}}],
                }
            },
            {
                "comment": {
                    "id": 2,
                    "text": "Still needs a reply.",
                    "state": "OPEN",
                    "author": {"name": "reviewer"},
                }
            },
            {
                "comment": {
                    "id": 3,
                    "text": "Resolved already.",
                    "state": "RESOLVED",
                    "author": {"name": "reviewer"},
                    "comments": [{"id": 11, "text": "Done.", "author": {"name": "alice"}}],
                }
            },
        ],
    }
    blocker_comments = {"values": []}

    result = compact_review_summary(pull_request, activities, blocker_comments)

    assert result["counts"]["open_comments"] == 2
    assert result["counts"]["open_comments_with_replies"] == 1
    assert result["counts"]["open_comments_without_replies"] == 1
```

- [ ] **Step 3: Run the new compact tests and confirm they fail**

Run:

```bash
pytest -q \
  tests/test_compact.py::test_compact_review_comments_adds_reply_metadata \
  tests/test_compact.py::test_compact_review_summary_counts_open_comments_with_and_without_replies
```

Expected: FAIL because the fields do not exist yet.

- [ ] **Step 4: Add reply helper functions**

Add these helpers near `_compact_reply` in `src/btbkt/compact.py`:

```python
def _latest_reply(replies: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not replies:
        return None
    return max(replies, key=lambda reply: reply.get("created") or "")


def _count_open_with_replies(comments: list[dict[str, Any]]) -> int:
    return sum(1 for comment in comments if comment.get("state") == "OPEN" and comment.get("has_replies") is True)


def _count_open_without_replies(comments: list[dict[str, Any]]) -> int:
    return sum(1 for comment in comments if comment.get("state") == "OPEN" and comment.get("has_replies") is not True)
```

- [ ] **Step 5: Add derived fields in `_compact_comment`**

In `src/btbkt/compact.py`, change `_compact_comment` so it computes `latest_reply` and includes reply fields:

```python
def _compact_comment(comment: Mapping[str, Any], *, anchor: Any = None) -> dict[str, Any]:
    comment_anchor = anchor if isinstance(anchor, Mapping) else comment.get("anchor")
    if not isinstance(comment_anchor, Mapping):
        comment_anchor = {}
    replies = [_compact_reply(reply) for reply in _as_list(comment.get("comments")) if isinstance(reply, Mapping)]
    tasks = [_compact_reply(task) for task in _as_list(comment.get("tasks")) if isinstance(task, Mapping)]
    latest_reply = _latest_reply(replies)
    result = {
        "id": comment.get("id"),
        "version": comment.get("version"),
        "state": comment.get("state"),
        "severity": comment.get("severity"),
        "author": _identity(comment.get("author")),
        "created": _time(comment.get("createdDate")),
        "updated": _time(comment.get("updatedDate")),
        "path": comment_anchor.get("path") or comment_anchor.get("srcPath"),
        "line": comment_anchor.get("line"),
        "line_type": comment_anchor.get("lineType"),
        "text": comment.get("text"),
        "replies": replies,
        "reply_count": len(replies),
        "has_replies": bool(replies),
        "latest_reply_author": latest_reply.get("author") if latest_reply else None,
        "latest_reply_created": latest_reply.get("created") if latest_reply else None,
        "tasks": tasks,
    }
    return _clean_dict(result)
```

- [ ] **Step 6: Add summary counts**

In `compact_review_summary`, add these count fields next to `open_comments`:

```python
            "open_comments_with_replies": _count_open_with_replies(comments),
            "open_comments_without_replies": _count_open_without_replies(comments),
```

The full `counts` block should still include blockers, review events, and reviewer counts exactly as before.

- [ ] **Step 7: Update existing expected JSON assertions**

Run:

```bash
pytest -q tests/test_compact.py tests/test_cli.py
```

Expected: FAIL in tests whose exact output now needs `reply_count` and `has_replies` fields.

Update affected expected dictionaries to include:

```python
"reply_count": 0,
"has_replies": False,
```

for comments without replies, and to include the new summary count fields:

```python
"open_comments_with_replies": 0,
"open_comments_without_replies": 1,
```

or the correct values for that fixture.

- [ ] **Step 8: Run compact and CLI tests**

Run:

```bash
pytest -q tests/test_compact.py tests/test_cli.py
```

Expected: PASS.

- [ ] **Step 9: Commit Task 2**

Run:

```bash
git add src/btbkt/compact.py tests/test_compact.py tests/test_cli.py
git commit -m "Add reply-aware review summary counts"
```

Expected: commit succeeds with only compact and test changes for this task.

## Task 3: Add Batch Reply Command

**Files:**
- Modify: `src/btbkt/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add CLI tests for dry-run validation**

Append this test to `tests/test_cli.py`:

```python
def test_cli_reply_many_dry_run_validates_input_without_requests(tmp_path):
    replies_path = tmp_path / "replies.json"
    replies_path.write_text(
        json.dumps([
            {"comment_id": 15466, "text": "Fixed first comment."},
            {"comment_id": 15467, "text": "Fixed second comment."},
        ])
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        [
            "--project",
            "ABC",
            "--repo",
            "demo",
            "pr",
            "reply-many",
            "42",
            "--input",
            str(replies_path),
            "--dry-run",
        ],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert transport.requests == []
    assert json.loads(stdout.getvalue()) == {
        "dry_run": True,
        "count": 2,
        "replies": [
            {"comment_id": 15466, "text": "Fixed first comment."},
            {"comment_id": 15467, "text": "Fixed second comment."},
        ],
    }
```

- [ ] **Step 2: Add CLI tests for invalid input**

Append this test to `tests/test_cli.py`:

```python
def test_cli_reply_many_rejects_unknown_fields_before_requests(tmp_path):
    replies_path = tmp_path / "replies.json"
    replies_path.write_text(json.dumps([{"comment_id": 15466, "body": "wrong key"}]))
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        [
            "--project",
            "ABC",
            "--repo",
            "demo",
            "pr",
            "reply-many",
            "42",
            "--input",
            str(replies_path),
        ],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert transport.requests == []
    error = json.loads(stderr.getvalue())
    assert error["error"] == "ValueError"
    assert "unknown fields" in error["message"]
```

- [ ] **Step 3: Add CLI tests for real batch execution**

Append this test to `tests/test_cli.py`:

```python
def test_cli_reply_many_posts_replies_sequentially(tmp_path):
    replies_path = tmp_path / "replies.json"
    replies_path.write_text(
        json.dumps([
            {"comment_id": 15466, "text": "Fixed first comment."},
            {"comment_id": 15467, "text": "Fixed second comment."},
        ])
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        [
            "--project",
            "ABC",
            "--repo",
            "demo",
            "pr",
            "reply-many",
            "42",
            "--input",
            str(replies_path),
        ],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert [request[0] for request in transport.requests] == ["POST", "POST"]
    assert [json.loads(request[3].decode("utf-8")) for request in transport.requests] == [
        {"text": "Fixed first comment.", "parent": {"id": 15466}},
        {"text": "Fixed second comment.", "parent": {"id": 15467}},
    ]
    output = json.loads(stdout.getvalue())
    assert output["count"] == 2
    assert output["attempted"] == 2
    assert output["failed"] == 0
    assert [result["status"] for result in output["results"]] == ["ok", "ok"]
```

- [ ] **Step 4: Run the new reply-many tests and confirm they fail**

Run:

```bash
pytest -q \
  tests/test_cli.py::test_cli_reply_many_dry_run_validates_input_without_requests \
  tests/test_cli.py::test_cli_reply_many_rejects_unknown_fields_before_requests \
  tests/test_cli.py::test_cli_reply_many_posts_replies_sequentially
```

Expected: FAIL because `reply-many` is not registered.

- [ ] **Step 5: Add `CommandResult` to preserve JSON with nonzero exit codes**

In `src/btbkt/cli.py`, add the import:

```python
from dataclasses import dataclass
```

Add this class near the imports:

```python
@dataclass(frozen=True)
class CommandResult:
    payload: Any
    exit_code: int = 0
```

In `main`, after `_dispatch` returns and before `_write_json(stdout, result)`, add:

```python
        exit_code = 0
        if isinstance(result, CommandResult):
            exit_code = result.exit_code
            result = result.payload
```

Then change the final return from:

```python
    return 0
```

to:

```python
    return exit_code
```

Do not change the existing exception handling behavior.

- [ ] **Step 6: Add parser support for `reply-many`**

In `build_parser`, after the `reply` parser, add:

```python
    reply_many_parser = pr_actions.add_parser("reply-many", help="Reply to multiple PR comments from a JSON file.")
    reply_many_parser.add_argument("pr_id", type=int)
    reply_many_parser.add_argument("--input", required=True, help="JSON file containing reply objects.")
    reply_many_parser.add_argument("--dry-run", action="store_true", help="Validate and print planned replies without posting.")
    reply_many_parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Attempt remaining replies after a failed reply.",
    )
```

- [ ] **Step 7: Add reply-many dispatch**

In `_dispatch_pr`, after the `reply` branch, add:

```python
    if args.action == "reply-many":
        replies = _load_reply_many_input(Path(args.input))
        if args.dry_run:
            return {"dry_run": True, "count": len(replies), "replies": replies}
        return _reply_many(
            client,
            args.pr_id,
            replies,
            continue_on_error=args.continue_on_error,
        )
```

- [ ] **Step 8: Add input validation helpers**

Add these helpers near `_current_pull_requests` in `src/btbkt/cli.py`:

```python
def _load_reply_many_input(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text())
    except OSError as exc:
        raise ValueError(f"Unable to read reply input file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Reply input file is not valid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("Reply input must be a JSON array.")
    replies = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"Reply item {index} must be an object.")
        unknown = sorted(set(item) - {"comment_id", "text"})
        if unknown:
            raise ValueError(f"Reply item {index} has unknown fields: {', '.join(unknown)}.")
        comment_id = item.get("comment_id")
        text = item.get("text")
        if isinstance(comment_id, bool) or not isinstance(comment_id, int):
            raise ValueError(f"Reply item {index} comment_id must be an integer.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Reply item {index} text must be a non-empty string.")
        replies.append({"comment_id": comment_id, "text": text})
    return replies
```

- [ ] **Step 9: Add execution helper**

Add this helper near `_load_reply_many_input`:

```python
def _reply_many(
    client: BitbucketClient,
    pr_id: int,
    replies: list[dict[str, Any]],
    *,
    continue_on_error: bool,
) -> CommandResult:
    results = []
    failed = 0
    for reply in replies:
        comment_id = reply["comment_id"]
        try:
            response = client.reply_to_comment(pr_id, comment_id, text=reply["text"])
        except BitbucketAPIError as exc:
            failed += 1
            results.append(
                {
                    "comment_id": comment_id,
                    "status": "error",
                    "error": {
                        "status": exc.status,
                        "url": exc.url,
                        "message": str(exc),
                    },
                }
            )
            if not continue_on_error:
                break
        else:
            results.append({"comment_id": comment_id, "status": "ok", "response": response})
    payload = {
        "count": len(replies),
        "attempted": len(results),
        "failed": failed,
        "results": results,
    }
    return CommandResult(payload=payload, exit_code=1 if failed else 0)
```

- [ ] **Step 10: Run reply-many tests**

Run:

```bash
pytest -q \
  tests/test_cli.py::test_cli_reply_many_dry_run_validates_input_without_requests \
  tests/test_cli.py::test_cli_reply_many_rejects_unknown_fields_before_requests \
  tests/test_cli.py::test_cli_reply_many_posts_replies_sequentially
```

Expected: PASS.

- [ ] **Step 11: Run full CLI tests**

Run:

```bash
pytest -q tests/test_cli.py
```

Expected: PASS.

- [ ] **Step 12: Commit Task 3**

Run:

```bash
git add src/btbkt/cli.py tests/test_cli.py
git commit -m "Add batch review comment replies"
```

Expected: commit succeeds with only CLI and CLI test changes for this task.

## Task 4: Add Bounded Diff Context For Review Comments

**Files:**
- Modify: `src/btbkt/compact.py`
- Modify: `src/btbkt/cli.py`
- Test: `tests/test_compact.py`
- Test: `tests/test_cli.py`

**Review correction:** Preserve Bitbucket anchor `fileType` as `file_type` and use it before `line_type` when deciding source vs destination line matching. Build context from the matched hunk only; do not flatten an entire file's hunks into one list for radius slicing.

- [ ] **Step 1: Add pure compact diff-context test**

Update the import in `tests/test_compact.py` to include `add_diff_context_to_comments`:

```python
from btbkt.compact import (
    add_diff_context_to_comments,
    compact_current_pull_requests,
    compact_review_comments,
    compact_review_context,
    compact_review_summary,
)
```

Append this test:

```python
def test_add_diff_context_to_comments_attaches_radius_around_anchor():
    comments = {
        "comments": [
            {
                "id": 15466,
                "path": "src/app.py",
                "line": 8,
                "line_type": "ADDED",
                "text": "提高优先级",
            }
        ],
        "count": 1,
    }
    diff_by_path = {
        "src/app.py": {
            "diffs": [
                {
                    "destination": {"toString": "src/app.py"},
                    "hunks": [
                        {
                            "segments": [
                                {
                                    "type": "CONTEXT",
                                    "lines": [{"source": 7, "destination": 7, "line": "before"}],
                                },
                                {
                                    "type": "ADDED",
                                    "lines": [
                                        {"destination": 8, "line": "target"},
                                        {"destination": 9, "line": "after"},
                                    ],
                                },
                            ]
                        }
                    ],
                }
            ]
        }
    }

    result = add_diff_context_to_comments(comments, diff_by_path, radius=1)

    assert result["comments"][0]["diff_context"] == {
        "path": "src/app.py",
        "line": 8,
        "line_type": "ADDED",
        "radius": 1,
        "truncated_before": False,
        "truncated_after": False,
        "lines": [
            {"type": "CONTEXT", "source": 7, "destination": 7, "text": "before"},
            {"type": "ADDED", "destination": 8, "text": "target"},
            {"type": "ADDED", "destination": 9, "text": "after"},
        ],
    }
```

- [ ] **Step 2: Add pure compact unavailable-context test**

Append this test to `tests/test_compact.py`:

```python
def test_add_diff_context_to_comments_marks_missing_anchor():
    comments = {"comments": [{"id": 15466, "text": "提高优先级"}], "count": 1}

    result = add_diff_context_to_comments(comments, {}, radius=1)

    assert result["comments"][0]["diff_context_unavailable"] == "missing_anchor"
```

- [ ] **Step 3: Run the new compact tests and confirm they fail**

Run:

```bash
pytest -q \
  tests/test_compact.py::test_add_diff_context_to_comments_attaches_radius_around_anchor \
  tests/test_compact.py::test_add_diff_context_to_comments_marks_missing_anchor
```

Expected: FAIL because `add_diff_context_to_comments` does not exist.

- [ ] **Step 4: Implement diff-context enrichment**

Add this public helper and private helpers to `src/btbkt/compact.py`:

```python
def add_diff_context_to_comments(
    review_comments: Mapping[str, Any],
    diff_by_path: Mapping[str, Mapping[str, Any]],
    *,
    radius: int,
) -> dict[str, Any]:
    if radius < 0:
        raise ValueError("radius must be non-negative.")
    result = dict(review_comments)
    enriched = []
    for comment in _as_list(review_comments.get("comments")):
        if not isinstance(comment, Mapping):
            continue
        compact = dict(comment)
        context = _comment_diff_context(compact, diff_by_path, radius=radius)
        if "lines" in context:
            compact["diff_context"] = context
        else:
            compact["diff_context_unavailable"] = context["reason"]
        enriched.append(compact)
    result["comments"] = enriched
    return result


def _comment_diff_context(
    comment: Mapping[str, Any],
    diff_by_path: Mapping[str, Mapping[str, Any]],
    *,
    radius: int,
) -> dict[str, Any]:
    path = _string_or_none(comment.get("path"))
    line = comment.get("line")
    if not path or not isinstance(line, int):
        return {"reason": "missing_anchor"}
    diff = diff_by_path.get(path)
    if not isinstance(diff, Mapping):
        return {"reason": "missing_diff"}
    if "body" in diff:
        return {"reason": "unsupported_diff_format"}
    lines = _flatten_diff_lines(diff, path)
    if not lines:
        return {"reason": "line_not_found"}
    line_type = _string_or_none(comment.get("line_type"))
    line_key = _anchor_line_key(line_type)
    match_index = _find_context_line(lines, line_key=line_key, line=line)
    if match_index is None and line_key != "destination":
        match_index = _find_context_line(lines, line_key="destination", line=line)
    if match_index is None:
        return {"reason": "line_not_found"}
    start = max(0, match_index - radius)
    end = min(len(lines), match_index + radius + 1)
    return {
        "path": path,
        "line": line,
        "line_type": line_type,
        "radius": radius,
        "truncated_before": start > 0,
        "truncated_after": end < len(lines),
        "lines": lines[start:end],
    }


def _flatten_diff_lines(diff: Mapping[str, Any], path: str) -> list[dict[str, Any]]:
    flattened = []
    for file_diff in _as_list(diff.get("diffs")):
        if not isinstance(file_diff, Mapping):
            continue
        destination = _path_text(file_diff.get("destination")) or _path_text(file_diff.get("source"))
        source = _path_text(file_diff.get("source")) or destination
        if destination != path and source != path:
            continue
        for hunk in _as_list(file_diff.get("hunks")):
            if not isinstance(hunk, Mapping):
                continue
            for segment in _as_list(hunk.get("segments")):
                if not isinstance(segment, Mapping):
                    continue
                segment_type = _string_or_none(segment.get("type")) or "CONTEXT"
                for line in _as_list(segment.get("lines")):
                    if not isinstance(line, Mapping):
                        continue
                    flattened.append(
                        _clean_dict(
                            {
                                "type": segment_type,
                                "source": line.get("source"),
                                "destination": line.get("destination"),
                                "text": line.get("line") if "line" in line else line.get("text"),
                            }
                        )
                    )
    return flattened


def _anchor_line_key(line_type: Optional[str]) -> str:
    normalized = (line_type or "").upper()
    if normalized in {"REMOVED", "DELETED", "DELETE", "FROM"}:
        return "source"
    return "destination"


def _find_context_line(lines: list[dict[str, Any]], *, line_key: str, line: int) -> Optional[int]:
    for index, entry in enumerate(lines):
        if entry.get(line_key) == line:
            return index
    return None
```

- [ ] **Step 5: Run compact diff-context tests**

Run:

```bash
pytest -q \
  tests/test_compact.py::test_add_diff_context_to_comments_attaches_radius_around_anchor \
  tests/test_compact.py::test_add_diff_context_to_comments_marks_missing_anchor
```

Expected: PASS.

- [ ] **Step 6: Add CLI transport fixture for comment diff context**

Append this class to `tests/test_cli.py` near other review transports:

```python
class ReviewCommentsContextTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if url.endswith("/pull-requests/42/activities?limit=100"):
            return {
                "start": 0,
                "limit": 100,
                "isLastPage": True,
                "values": [
                    {
                        "action": "COMMENTED",
                        "comment": {
                            "id": 15466,
                            "text": "提高优先级",
                            "state": "OPEN",
                            "author": {"name": "reviewer"},
                        },
                        "commentAnchor": {"path": "src/app.py", "line": 8, "lineType": "ADDED"},
                    },
                    {
                        "action": "COMMENTED",
                        "comment": {
                            "id": 15467,
                            "text": "没有锚点",
                            "state": "OPEN",
                            "author": {"name": "reviewer"},
                        },
                    },
                ],
            }
        if url.endswith("/pull-requests/42/diff?path=src%2Fapp.py"):
            return {
                "diffs": [
                    {
                        "destination": {"toString": "src/app.py"},
                        "hunks": [
                            {
                                "segments": [
                                    {
                                        "type": "CONTEXT",
                                        "lines": [{"source": 7, "destination": 7, "line": "before"}],
                                    },
                                    {
                                        "type": "ADDED",
                                        "lines": [{"destination": 8, "line": "target"}],
                                    },
                                ]
                            }
                        ],
                    }
                ]
            }
        raise AssertionError(f"Unexpected URL: {url}")
```

- [ ] **Step 7: Add CLI test for `--with-diff-context`**

Append this test to `tests/test_cli.py`:

```python
def test_cli_review_comments_with_diff_context_fetches_once_per_path():
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = ReviewCommentsContextTransport()

    exit_code = main(
        [
            "--project",
            "ABC",
            "--repo",
            "demo",
            "pr",
            "review-comments",
            "42",
            "--state",
            "OPEN",
            "--with-diff-context",
            "1",
        ],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    output = json.loads(stdout.getvalue())
    assert output["comments"][0]["diff_context"]["lines"] == [
        {"type": "CONTEXT", "source": 7, "destination": 7, "text": "before"},
        {"type": "ADDED", "destination": 8, "text": "target"},
    ]
    assert output["comments"][1]["diff_context_unavailable"] == "missing_anchor"
    assert [request[1].rsplit("/", 1)[-1] for request in transport.requests] == [
        "activities?limit=100",
        "diff?path=src%2Fapp.py",
    ]
```

- [ ] **Step 8: Run the new CLI test and confirm it fails**

Run:

```bash
pytest -q tests/test_cli.py::test_cli_review_comments_with_diff_context_fetches_once_per_path
```

Expected: FAIL because the parser does not know `--with-diff-context`.

- [ ] **Step 9: Import compact helper in `cli.py`**

In `src/btbkt/cli.py`, add `add_diff_context_to_comments` to the compact imports:

```python
from .compact import (
    add_diff_context_to_comments,
    compact_current_pull_requests,
    compact_review_comments,
    compact_review_context,
    compact_review_status,
    compact_review_summary,
)
```

- [ ] **Step 10: Add parser option**

In the `review-comments` parser block in `build_parser`, add:

```python
    review_comments_parser.add_argument(
        "--with-diff-context",
        type=int,
        help="Attach bounded diff context around each anchored comment using this line radius.",
    )
```

- [ ] **Step 11: Add CLI helper for path-scoped diff fetching**

Add this helper near `_reply_many` in `src/btbkt/cli.py`:

```python
def _diffs_for_comment_paths(
    client: BitbucketClient,
    pr_id: int,
    comments: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    paths = sorted(
        {
            path
            for comment in comments
            if isinstance(comment, Mapping)
            for path in [_comment_path(comment)]
            if path
        }
    )
    return {path: client.get_diff(pr_id, path=path) for path in paths}


def _comment_path(comment: Mapping[str, Any]) -> Optional[str]:
    value = comment.get("path")
    return value if isinstance(value, str) and value else None
```

- [ ] **Step 12: Wire the dispatch**

Replace the `review-comments` branch in `_dispatch_pr` with:

```python
    if args.action == "review-comments":
        activities = client.get_activities(args.pr_id, limit=args.limit, start=args.start)
        result = compact_review_comments(activities, state=args.state)
        if args.with_diff_context is not None:
            if args.with_diff_context < 0:
                raise ValueError("--with-diff-context must be zero or positive.")
            diff_by_path = _diffs_for_comment_paths(client, args.pr_id, result["comments"])
            result = add_diff_context_to_comments(result, diff_by_path, radius=args.with_diff_context)
        return result
```

- [ ] **Step 13: Run diff-context CLI tests**

Run:

```bash
pytest -q tests/test_cli.py::test_cli_review_comments_with_diff_context_fetches_once_per_path
```

Expected: PASS.

- [ ] **Step 14: Run compact and CLI suites**

Run:

```bash
pytest -q tests/test_compact.py tests/test_cli.py
```

Expected: PASS.

- [ ] **Step 15: Commit Task 4**

Run:

```bash
git add src/btbkt/compact.py src/btbkt/cli.py tests/test_compact.py tests/test_cli.py
git commit -m "Add diff context to review comments"
```

Expected: commit succeeds with compact, CLI, and test changes for this task.

## Task 5: Update Documentation And Run Final Verification

**Files:**
- Modify: `README.md`
- Modify: `skills/using-btbkt-pr-workflows/SKILL.md`

- [ ] **Step 1: Update README examples**

In `README.md`, replace the review comment example block:

````markdown
Read unresolved review comments and reply after fixing:

```bash
btbkt pr review-comments 390 --state OPEN
btbkt pr reply 390 15450 --text "Fixed and covered by tests."
```
````

with:

````markdown
Read unresolved review comments, inspect terse comments with nearby diff context, and reply after fixing:

```bash
btbkt pr review-comments 390 --state OPEN --with-diff-context 5
btbkt pr reply 390 15450 --text "Fixed and covered by tests."
```

Reply to multiple handled comments from a reviewed JSON file:

```bash
btbkt pr reply-many 390 --input replies.json --dry-run
btbkt pr reply-many 390 --input replies.json
```

`review-summary` reports Bitbucket open state separately from reply state. A comment can remain `OPEN` after a reply, so use `open_comments_without_replies` for the remaining-unreplied count.
````

- [ ] **Step 2: Update skill command table**

In `skills/using-btbkt-pr-workflows/SKILL.md`, update the command table rows for comments and replies to:

```markdown
| Read all review comments from activity stream | `btbkt pr review-comments PR_ID --state OPEN [--with-diff-context 5]` |
| Reply after one fix | `btbkt pr reply PR_ID COMMENT_ID --text TEXT` |
| Reply after multiple fixes | `btbkt pr reply-many PR_ID --input replies.json --dry-run`, then rerun without `--dry-run` |
```

- [ ] **Step 3: Update "Address Review On Your PR" workflow**

Replace that workflow section with:

```markdown
## Workflow: Address Review On Your PR

1. Run `btbkt pr review-comments PR_ID --state OPEN`.
2. For short or ambiguous comments, rerun with `btbkt pr review-comments PR_ID --state OPEN --with-diff-context 5`, or use `btbkt pr review-context PR_ID --path PATH --max-diff-lines N` when broader file context is needed.
3. Group comments by file and fix code.
4. Run relevant checks before replying.
5. Reply to each handled thread with `btbkt pr reply PR_ID COMMENT_ID --text TEXT`; include what changed and which verification passed.
6. For multiple handled threads, create `replies.json`, run `btbkt pr reply-many PR_ID --input replies.json --dry-run`, inspect the planned replies, then run the same command without `--dry-run`.
7. Run `btbkt pr review-summary PR_ID --state OPEN`.
8. Report `open_comments`, `open_comments_with_replies`, `open_comments_without_replies`, open blockers, source/target commits, and verification. Bitbucket can keep comments `OPEN` after replies, so do not describe replied open comments as unhandled.
```

- [ ] **Step 4: Update error handling and mistakes**

Add this bullet under "Context And Error Handling":

```markdown
- If `btbkt pr reply` returns 404, do not post a top-level fallback comment unless the user explicitly accepts the degraded behavior. A 404 can mean the CLI is using a reply endpoint that is incompatible with the local Bitbucket Server, or that the PR/comment context is wrong.
```

Add these bullets under "Mistakes To Avoid":

```markdown
- Treating `counts.open_comments` as the number of unhandled comments. Use `open_comments_without_replies` for unreplied open comments, and still preserve the raw open count.
- Guessing from terse comments such as "提高优先级" without inspecting nearby diff context.
- Posting a top-level PR comment when the intended action is replying to a review thread.
```

- [ ] **Step 5: Run documentation grep checks**

Run:

```bash
rg -n "open_comments_without_replies|reply-many|with-diff-context|top-level fallback" README.md skills/using-btbkt-pr-workflows/SKILL.md
```

Expected: output includes matches in both `README.md` and `skills/using-btbkt-pr-workflows/SKILL.md`.

- [ ] **Step 6: Run full verification**

Run:

```bash
pytest -q
PYTHONPATH=src python -m btbkt --help
PYTHONPATH=src python -m btbkt pr --help
PYTHONPATH=src python -m btbkt pr reply --help
PYTHONPATH=src python -m btbkt pr reply-many --help
PYTHONPATH=src python -m btbkt pr review-comments --help
python -m compileall -q src tests
```

Expected:

- `pytest -q` passes.
- Help commands exit 0 and show JSON-free argparse help.
- `reply-many` appears in `btbkt pr --help`.
- `--with-diff-context` appears in `btbkt pr review-comments --help`.
- `compileall` exits 0.

- [ ] **Step 7: Commit Task 5**

Run:

```bash
git add README.md skills/using-btbkt-pr-workflows/SKILL.md
git commit -m "Document reply-aware PR review workflow"
```

Expected: commit succeeds with only documentation changes for this task.

## Final Review Checklist

- [ ] `git status --short` contains no unexpected files. Preserve any unrelated staged files that existed before implementation.
- [ ] `btbkt pr reply` no longer calls `/comments/{id}/replies`.
- [ ] `review-summary` includes `open_comments`, `open_comments_with_replies`, and `open_comments_without_replies`.
- [ ] `reply-many --dry-run` validates input without network requests.
- [ ] `reply-many` reports per-comment results.
- [ ] `review-comments --with-diff-context 5` fetches each path diff once and marks missing context with `diff_context_unavailable`.
- [ ] Skill docs tell agents that Bitbucket `OPEN` does not mean unhandled.

## Spec Coverage Self-Review

- `pr reply` endpoint fix: Task 1.
- Reply metadata and summary counts: Task 2.
- Batch replies with dry-run and per-comment output: Task 3.
- Bounded context for vague comments: Task 4.
- Skill and README updates: Task 5.
- `addressed-comments` naming adjustment: covered by scope exclusion; no command is added under that misleading name.

Placeholder scan result: the plan has no placeholder sections. Type consistency result: helper names used by later tasks are defined in earlier task steps or in the same task.
