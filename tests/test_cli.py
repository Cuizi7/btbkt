import io
import json

import pytest

from btbkt.client import BitbucketAPIError
from btbkt.cli import main


class CapturingTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        return {"url": url, "method": method}


PULL_REQUEST = {
    "id": 42,
    "fromRef": {"latestCommit": "abc123"},
    "reviewers": [{"user": {"name": "alice", "slug": "alice-slug"}}],
}


class ReviewWorkflowTransport:
    def __init__(self, *, pending=None, fail_completion=False):
        self.requests = []
        self.pending = {"values": []} if pending is None else pending
        self.fail_completion = fail_completion

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if method == "GET" and url.endswith("/pull-requests/42"):
            return PULL_REQUEST
        if method == "GET" and "/pull-requests/42/review" in url:
            return self.pending
        if method == "POST" and url.endswith("/pull-requests/42/comments"):
            return {"id": 99, "state": "PENDING", "text": "LGTM"}
        if method == "PUT" and url.endswith("/pull-requests/42/review"):
            if self.fail_completion:
                raise BitbucketAPIError(409, url, "completion conflict")
            return {"completed": True}
        return {"ok": True}


ENV = {
    "BITBUCKET_BASE_URL": "https://bitbucket.internal",
    "BITBUCKET_USERNAME": "alice",
    "BITBUCKET_TOKEN": "token",
}


class FailingReplyTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        payload = json.loads(body.decode("utf-8")) if body else {}
        if payload.get("parent", {}).get("id") == 15466:
            raise BitbucketAPIError(404, url, "missing reply endpoint")
        return {"ok": True, "method": method}


class ReviewTransport:
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
                            "id": 100,
                            "text": "Please add a test.",
                            "state": "OPEN",
                            "severity": "NORMAL",
                            "author": {"name": "reviewer"},
                        },
                        "commentAnchor": {"path": "src/app.py", "line": 8, "lineType": "ADDED"},
                        "diff": {"large": "ignored"},
                    }
                ],
            }
        if url.endswith("/pull-requests/42"):
            return {
                "id": 42,
                "version": 3,
                "title": "Add API",
                "state": "OPEN",
                "open": True,
                "author": {"user": {"name": "alice"}, "status": "UNAPPROVED", "approved": False},
                "reviewers": [
                    {"user": {"name": "reviewer"}, "status": "APPROVED", "approved": True}
                ],
            }
        if url.endswith("/pull-requests/42/blocker-comments?limit=100"):
            return {"start": 0, "limit": 100, "isLastPage": True, "values": []}
        raise AssertionError(f"Unexpected URL: {url}")


class StateFilterTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if url.endswith("/pull-requests/42"):
            return {
                "id": 42,
                "version": 3,
                "title": "Add API",
                "state": "OPEN",
                "open": True,
                "reviewers": [],
            }
        if url.endswith("/pull-requests/42/activities?limit=100"):
            return {
                "start": 0,
                "limit": 100,
                "isLastPage": True,
                "values": [
                    {
                        "action": "COMMENTED",
                        "comment": {
                            "id": 100,
                            "text": "Open comment.",
                            "state": "OPEN",
                            "severity": "NORMAL",
                            "author": {"name": "reviewer"},
                        },
                        "commentAnchor": {"path": "src/app.py", "line": 8, "lineType": "ADDED"},
                    },
                    {
                        "action": "COMMENTED",
                        "comment": {
                            "id": 101,
                            "text": "Resolved comment.",
                            "state": "RESOLVED",
                            "severity": "NORMAL",
                            "author": {"name": "reviewer"},
                        },
                        "commentAnchor": {"path": "src/app.py", "line": 9, "lineType": "ADDED"},
                    },
                ],
            }
        if url.endswith("/pull-requests/42/blocker-comments?limit=100"):
            return {
                "start": 0,
                "limit": 100,
                "isLastPage": True,
                "values": [
                    {
                        "id": 200,
                        "text": "Open blocker.",
                        "state": "OPEN",
                        "severity": "BLOCKER",
                        "author": {"name": "reviewer"},
                    },
                    {
                        "id": 201,
                        "text": "Resolved blocker.",
                        "state": "RESOLVED",
                        "severity": "BLOCKER",
                        "author": {"name": "reviewer"},
                    },
                ],
            }
        raise AssertionError(f"Unexpected URL: {url}")


class CurrentPullRequestTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if url.endswith("/pull-requests?state=ALL&direction=OUTGOING&limit=50&start=25"):
            return {
                "start": 25,
                "limit": 50,
                "isLastPage": True,
                "values": [
                    {
                        "id": 42,
                        "title": "Current branch PR",
                        "state": "OPEN",
                        "fromRef": {"id": "refs/heads/feature/current", "displayId": "feature/current"},
                        "toRef": {"id": "refs/heads/main", "displayId": "main"},
                    },
                    {
                        "id": 43,
                        "title": "Other branch PR",
                        "state": "OPEN",
                        "fromRef": {"id": "refs/heads/feature/other", "displayId": "feature/other"},
                        "toRef": {"id": "refs/heads/main", "displayId": "main"},
                    },
                ],
            }
        raise AssertionError(f"Unexpected URL: {url}")


class PagedCurrentPullRequestTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if url.endswith("/pull-requests?state=OPEN&direction=OUTGOING&limit=1"):
            return {
                "start": 0,
                "limit": 1,
                "isLastPage": False,
                "nextPageStart": 1,
                "values": [
                    {
                        "id": 41,
                        "title": "Other branch PR",
                        "state": "OPEN",
                        "fromRef": {"id": "refs/heads/feature/other", "displayId": "feature/other"},
                    }
                ],
            }
        if url.endswith("/pull-requests?state=OPEN&direction=OUTGOING&limit=1&start=1"):
            return {
                "start": 1,
                "limit": 1,
                "isLastPage": True,
                "values": [
                    {
                        "id": 42,
                        "title": "Current branch PR",
                        "state": "OPEN",
                        "fromRef": {"id": "refs/heads/feature/current", "displayId": "feature/current"},
                    }
                ],
            }
        raise AssertionError(f"Unexpected URL: {url}")


class NoMatchCurrentPullRequestTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if url.endswith("/pull-requests?state=OPEN&direction=OUTGOING&limit=1"):
            return {
                "start": 0,
                "limit": 1,
                "isLastPage": False,
                "nextPageStart": 1,
                "values": [
                    {
                        "id": 41,
                        "title": "Other branch PR",
                        "state": "OPEN",
                        "fromRef": {"id": "refs/heads/feature/other", "displayId": "feature/other"},
                    }
                ],
            }
        if url.endswith("/pull-requests?state=OPEN&direction=OUTGOING&limit=1&start=1"):
            return {
                "start": 1,
                "limit": 1,
                "isLastPage": True,
                "values": [
                    {
                        "id": 43,
                        "title": "Another branch PR",
                        "state": "OPEN",
                        "fromRef": {"id": "refs/heads/feature/another", "displayId": "feature/another"},
                    }
                ],
            }
        raise AssertionError(f"Unexpected URL: {url}")


class ReviewContextTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if url.endswith("/pull-requests/42"):
            return {
                "id": 42,
                "title": "Add API",
                "state": "OPEN",
                "fromRef": {"id": "refs/heads/feature/api", "displayId": "feature/api"},
                "toRef": {"id": "refs/heads/main", "displayId": "main"},
            }
        if url.endswith("/pull-requests/42/changes?limit=100"):
            return {
                "start": 0,
                "limit": 100,
                "isLastPage": True,
                "values": [{"path": {"toString": "src/app.py"}, "type": "MODIFY", "nodeType": "FILE"}],
            }
        if url.endswith("/pull-requests/42/diff?path=src%2Fapp.py"):
            return {
                "diffs": [
                    {
                        "destination": {"toString": "README.md"},
                        "hunks": [
                            {
                                "segments": [
                                    {
                                        "type": "ADDED",
                                        "lines": [{"destination": 1, "line": "readme"}],
                                    }
                                ]
                            }
                        ],
                    },
                    {
                        "destination": {"toString": "src/app.py"},
                        "hunks": [
                            {
                                "segments": [
                                    {
                                        "type": "ADDED",
                                        "lines": [{"destination": 8, "line": "new api"}],
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        raise AssertionError(f"Unexpected URL: {url}")


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


class TruncatedUnifiedReviewContextTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if url.endswith("/pull-requests/42"):
            return {"id": 42, "title": "Add API", "state": "OPEN"}
        if url.endswith("/pull-requests/42/changes?limit=100"):
            return {"values": [{"path": {"toString": "src/app.py"}, "type": "MODIFY", "nodeType": "FILE"}]}
        if url.endswith("/pull-requests/42/diff?path=src%2Fapp.py"):
            return {
                "diffs": [
                    {
                        "destination": {"toString": "src/app.py"},
                        "hunks": [
                            {
                                "segments": [
                                    {
                                        "type": "ADDED",
                                        "lines": [
                                            {"destination": 8, "line": "first line"},
                                            {"destination": 9, "line": "second line"},
                                        ],
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        raise AssertionError(f"Unexpected URL: {url}")


@pytest.mark.parametrize(
    ("action", "status"),
    [("approve", "APPROVED"), ("unapprove", "UNAPPROVED"), ("needs-work", "NEEDS_WORK")],
)
def test_cli_standalone_decisions_update_current_participant(action, status):
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = ReviewWorkflowTransport()
    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", action, "42"],
        env=ENV,
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert [request[0] for request in transport.requests] == ["GET", "PUT"]
    assert transport.requests[1][1].endswith("/pull-requests/42/participants/alice-slug")
    assert json.loads(transport.requests[1][3].decode("utf-8")) == {
        "status": status,
        "lastReviewedCommit": "abc123",
    }


@pytest.mark.parametrize(
    ("flag", "status"),
    [("--approve", "APPROVED"), ("--unapprove", "UNAPPROVED"), ("--needs-work", "NEEDS_WORK")],
)
def test_cli_decision_only_review_updates_current_participant(flag, status):
    stdout = io.StringIO()
    transport = ReviewWorkflowTransport()

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "review", "42", flag],
        env=ENV,
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    assert [request[0] for request in transport.requests] == ["GET", "PUT"]
    assert transport.requests[1][1].endswith("/pull-requests/42/participants/alice-slug")
    assert json.loads(transport.requests[1][3].decode("utf-8")) == {
        "status": status,
        "lastReviewedCommit": "abc123",
    }


def test_cli_comment_only_review_posts_public_comment():
    stdout = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "review", "42", "--comment", "LGTM"],
        env=ENV,
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    method, url, _headers, body = transport.requests[0]
    assert method == "POST"
    assert url.endswith("/pull-requests/42/comments")
    assert json.loads(body.decode("utf-8")) == {"text": "LGTM"}


@pytest.mark.parametrize("comment", ["", "   "])
@pytest.mark.parametrize("decision", [[], ["--approve"]])
def test_cli_review_rejects_blank_comment_before_any_request(comment, decision):
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        [
            "--project", "ABC", "--repo", "demo", "pr", "review", "42",
            "--comment", comment, *decision,
        ],
        env=ENV,
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert transport.requests == []
    assert "non-empty" in json.loads(stderr.getvalue())["message"]


def test_cli_combined_review_creates_and_completes_pending_review_in_order():
    stdout = io.StringIO()
    transport = ReviewWorkflowTransport()

    exit_code = main(
        [
            "--project", "ABC", "--repo", "demo", "pr", "review", "42",
            "--comment", "LGTM", "--approve",
        ],
        env=ENV,
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    assert [request[0] for request in transport.requests] == ["GET", "GET", "POST", "PUT"]
    assert json.loads(transport.requests[2][3].decode("utf-8")) == {
        "text": "LGTM",
        "state": "PENDING",
    }
    assert json.loads(transport.requests[3][3].decode("utf-8")) == {
        "participantStatus": "APPROVED",
        "lastReviewedCommit": "abc123",
    }
    output = json.loads(stdout.getvalue())
    assert output["status"] == "completed"
    assert [step["operation"] for step in output["steps"]] == ["create_pending_comment", "complete_pending_review"]


def test_cli_combined_review_retains_comment_when_completion_fails():
    stdout = io.StringIO()
    transport = ReviewWorkflowTransport(fail_completion=True)

    exit_code = main(
        [
            "--project", "ABC", "--repo", "demo", "pr", "review", "42",
            "--comment", "LGTM", "--needs-work",
        ],
        env=ENV,
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 1
    output = json.loads(stdout.getvalue())
    assert output["status"] == "partial"
    assert output["steps"][0]["response"]["id"] == 99
    assert output["steps"][1]["error"]["status"] == 409
    assert (
        "btbkt --base-url https://bitbucket.internal --username alice --project ABC --repo demo "
        "pr review-submit 42 --needs-work"
    ) in output["recovery"]


def test_cli_combined_review_refuses_duplicate_pending_comment():
    stdout = io.StringIO()
    transport = ReviewWorkflowTransport(pending={"values": [{"id": 98, "text": "already pending"}]})

    exit_code = main(
        [
            "--project", "ABC", "--repo", "demo", "pr", "review", "42",
            "--comment", "LGTM", "--approve",
        ],
        env=ENV,
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 1
    assert [request[0] for request in transport.requests] == ["GET", "GET"]
    output = json.loads(stdout.getvalue())
    assert output["status"] == "blocked"
    assert output["recovery"] == [
        "btbkt --base-url https://bitbucket.internal --username alice --project ABC --repo demo "
        "pr review-pending 42",
        "btbkt --base-url https://bitbucket.internal --username alice --project ABC --repo demo "
        "pr review-submit 42 --approve",
        "btbkt --base-url https://bitbucket.internal --username alice --project ABC --repo demo "
        "pr review-discard 42",
    ]


def test_cli_combined_review_recovery_commands_include_shell_quoted_explicit_context():
    stdout = io.StringIO()
    transport = ReviewWorkflowTransport(pending={"values": [{"id": 98}]})

    exit_code = main(
        [
            "--base-url", "https://bitbucket.internal/bitbucket",
            "--username", "alice ops",
            "--token", "secret-token",
            "--project", "ABC TEAM",
            "--repo", "demo repo",
            "pr", "review", "42", "--comment", "LGTM", "--unapprove",
        ],
        env={},
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 1
    assert json.loads(stdout.getvalue())["recovery"] == [
        "btbkt --base-url https://bitbucket.internal/bitbucket --username 'alice ops' "
        "--project 'ABC TEAM' --repo 'demo repo' pr review-pending 42",
        "btbkt --base-url https://bitbucket.internal/bitbucket --username 'alice ops' "
        "--project 'ABC TEAM' --repo 'demo repo' pr review-submit 42 --unapprove",
        "btbkt --base-url https://bitbucket.internal/bitbucket --username 'alice ops' "
        "--project 'ABC TEAM' --repo 'demo repo' pr review-discard 42",
    ]
    assert "secret-token" not in stdout.getvalue()


def test_cli_combined_review_recovery_strips_base_url_userinfo():
    stdout = io.StringIO()
    transport = ReviewWorkflowTransport(pending={"values": [{"id": 98}]})

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "review", "42", "--comment", "LGTM", "--approve"],
        env={
            "BITBUCKET_BASE_URL": "https://url-user:url-password@bitbucket.internal:8443/bitbucket",
            "BITBUCKET_USERNAME": "configured-user",
            "BITBUCKET_TOKEN": "secret-token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 1
    output_text = stdout.getvalue()
    assert "url-user" not in output_text
    assert "url-password" not in output_text
    assert "secret-token" not in output_text
    output = json.loads(output_text)
    assert output["recovery"][0].startswith(
        "btbkt --base-url https://bitbucket.internal:8443/bitbucket "
        "--username configured-user --project ABC --repo demo pr review-pending 42"
    )


def test_cli_combined_review_partial_error_url_strips_base_url_userinfo():
    stdout = io.StringIO()
    transport = ReviewWorkflowTransport(fail_completion=True)

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "review", "42", "--comment", "LGTM", "--approve"],
        env={
            "BITBUCKET_BASE_URL": "https://url-user:url-password@bitbucket.internal:8443/bitbucket",
            "BITBUCKET_USERNAME": "configured-user",
            "BITBUCKET_TOKEN": "secret-token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 1
    output_text = stdout.getvalue()
    assert "url-user" not in output_text
    assert "url-password" not in output_text
    assert "secret-token" not in output_text
    output = json.loads(output_text)
    assert output["steps"][1]["error"]["url"] == (
        "https://bitbucket.internal:8443/bitbucket/rest/api/1.0/projects/ABC/repos/demo/pull-requests/42/review"
    )


def test_cli_combined_review_partial_error_redacts_known_secrets():
    class SecretEchoingTransport(ReviewWorkflowTransport):
        authorization = ""

        def __call__(self, method, url, headers, body):
            if method == "PUT" and url.endswith("/pull-requests/42/review"):
                self.authorization = headers["Authorization"]
                error_body = (
                    "NoSuchPullRequestReviewException raw-password raw-token "
                    f"{self.authorization} {url} decoded-user=url-user decoded-password=url-password"
                )
                raise BitbucketAPIError(409, url + "?echo=raw-token", error_body)
            return super().__call__(method, url, headers, body)

    stdout = io.StringIO()
    transport = SecretEchoingTransport()

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "review", "42", "--comment", "LGTM", "--approve"],
        env={
            "BITBUCKET_BASE_URL": "https://url%2Duser:url%2Dpassword@bitbucket.internal/bitbucket",
            "BITBUCKET_USERNAME": "configured-user",
            "BITBUCKET_PASSWORD": "raw-password",
            "BITBUCKET_TOKEN": "raw-token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 1
    output_text = stdout.getvalue()
    for secret in (
        "url%2Duser", "url-user", "url%2Dpassword", "url-password",
        "raw-password", "raw-token", "Basic ",
    ):
        assert secret not in output_text
    assert transport.authorization not in output_text
    error = json.loads(output_text)["steps"][1]["error"]
    assert error["status"] == 409
    assert "NoSuchPullRequestReviewException" in error["message"]


def test_cli_create_defaults_source_and_target_from_context():
    stdout = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        [
            "--project",
            "ABC",
            "--repo",
            "demo",
            "--source-branch",
            "feature/context",
            "--target-branch",
            "develop",
            "pr",
            "create",
            "--title",
            "feat: cli",
        ],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    method, url, _headers, body = transport.requests[0]
    assert method == "POST"
    assert url.endswith("/pull-requests")
    payload = json.loads(body.decode("utf-8"))
    assert payload["fromRef"]["id"] == "refs/heads/feature/context"
    assert payload["toRef"]["id"] == "refs/heads/develop"


def test_cli_review_pending_forwards_pagination():
    stdout = io.StringIO()
    transport = ReviewWorkflowTransport()

    exit_code = main(
        [
            "--project", "ABC", "--repo", "demo", "pr", "review-pending", "42",
            "--limit", "10", "--start", "20",
        ],
        env=ENV,
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    assert len(transport.requests) == 1
    assert transport.requests[0][0] == "GET"
    assert transport.requests[0][1].endswith("/pull-requests/42/review?limit=10&start=20")


def test_cli_review_submit_fetches_commit_then_completes_review():
    stdout = io.StringIO()
    transport = ReviewWorkflowTransport()

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "review-submit", "42", "--needs-work"],
        env=ENV,
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    assert [request[0] for request in transport.requests] == ["GET", "PUT"]
    assert transport.requests[0][1].endswith("/pull-requests/42")
    assert transport.requests[1][1].endswith("/pull-requests/42/review")
    assert json.loads(transport.requests[1][3].decode("utf-8")) == {
        "participantStatus": "NEEDS_WORK",
        "lastReviewedCommit": "abc123",
    }


def test_cli_review_discard_deletes_pending_review():
    stdout = io.StringIO()
    transport = ReviewWorkflowTransport()

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "review-discard", "42"],
        env=ENV,
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    assert len(transport.requests) == 1
    assert transport.requests[0][0] == "DELETE"
    assert transport.requests[0][1].endswith("/pull-requests/42/review")
    assert transport.requests[0][3] is None


def test_cli_review_submit_surfaces_missing_pending_review_error():
    class MissingReviewTransport(ReviewWorkflowTransport):
        def __call__(self, method, url, headers, body):
            if method == "PUT" and url.endswith("/pull-requests/42/review"):
                raise BitbucketAPIError(404, url, "NoSuchPullRequestReviewException: no pending review")
            return super().__call__(method, url, headers, body)

    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "review-submit", "42", "--approve"],
        env=ENV,
        stdout=stdout,
        stderr=stderr,
        transport=MissingReviewTransport(),
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert "NoSuchPullRequestReviewException" in stderr.getvalue()


@pytest.mark.parametrize(
    "argv",
    [
        ["pr", "review", "42"],
        ["pr", "review", "42", "--approve", "--unapprove"],
        ["pr", "review-submit", "42"],
        ["pr", "review-submit", "42", "--unapprove", "--needs-work"],
    ],
)
def test_cli_review_commands_require_exactly_one_selected_decision(argv):
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", *argv],
        env=ENV,
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert transport.requests == []
    assert json.loads(stderr.getvalue())["error"] == "ValueError"


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


def test_cli_reply_many_dry_run_validates_input_without_requests(tmp_path):
    replies_path = tmp_path / "replies.json"
    replies_path.write_text(
        json.dumps(
            [
                {"comment_id": 15466, "text": "Fixed first comment."},
                {"comment_id": 15467, "text": "Fixed second comment."},
            ]
        )
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


def test_cli_reply_many_posts_replies_sequentially(tmp_path):
    replies_path = tmp_path / "replies.json"
    replies_path.write_text(
        json.dumps(
            [
                {"comment_id": 15466, "text": "Fixed first comment."},
                {"comment_id": 15467, "text": "Fixed second comment."},
            ]
        )
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


def test_cli_reply_many_stops_on_first_api_error_by_default(tmp_path):
    replies_path = tmp_path / "replies.json"
    replies_path.write_text(
        json.dumps(
            [
                {"comment_id": 15466, "text": "Fails first."},
                {"comment_id": 15467, "text": "Should not be attempted."},
            ]
        )
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = FailingReplyTransport()

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
            "BITBUCKET_BASE_URL": "https://url-user:url-password@bitbucket.internal:8443/bitbucket",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "secret-token",
        },
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 1
    assert stderr.getvalue() == ""
    assert len(transport.requests) == 1
    output_text = stdout.getvalue()
    assert "url-user" not in output_text
    assert "url-password" not in output_text
    assert "secret-token" not in output_text
    output = json.loads(output_text)
    assert output["attempted"] == 1
    assert output["failed"] == 1
    assert output["results"][0]["comment_id"] == 15466
    assert output["results"][0]["status"] == "error"
    assert output["results"][0]["error"]["status"] == 404
    assert output["results"][0]["error"]["url"] == (
        "https://bitbucket.internal:8443/bitbucket/rest/api/1.0/projects/ABC/repos/demo/"
        "pull-requests/42/comments"
    )


def test_cli_reply_many_error_redacts_known_secrets(tmp_path):
    class SecretEchoingTransport:
        authorization = ""

        def __call__(self, method, url, headers, body):
            self.authorization = headers["Authorization"]
            error_body = f"missing reply endpoint raw-password raw-token {self.authorization}"
            raise BitbucketAPIError(404, url + "?echo=raw-token", error_body)

    replies_path = tmp_path / "replies.json"
    replies_path.write_text(json.dumps([{"comment_id": 15466, "text": "Fails."}]))
    stdout = io.StringIO()
    transport = SecretEchoingTransport()

    exit_code = main(
        [
            "--project", "ABC", "--repo", "demo", "pr", "reply-many", "42",
            "--input", str(replies_path),
        ],
        env={
            "BITBUCKET_BASE_URL": "https://url-user:url-password@bitbucket.internal/bitbucket",
            "BITBUCKET_USERNAME": "configured-user",
            "BITBUCKET_PASSWORD": "raw-password",
            "BITBUCKET_TOKEN": "raw-token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 1
    output_text = stdout.getvalue()
    for secret in ("url-password", "raw-password", "raw-token", "Basic "):
        assert secret not in output_text
    assert transport.authorization not in output_text
    error = json.loads(output_text)["results"][0]["error"]
    assert error["status"] == 404
    assert "missing reply endpoint" in error["message"]


def test_cli_reply_many_continue_on_error_attempts_remaining_replies(tmp_path):
    replies_path = tmp_path / "replies.json"
    replies_path.write_text(
        json.dumps(
            [
                {"comment_id": 15466, "text": "Fails first."},
                {"comment_id": 15467, "text": "Still attempt this."},
            ]
        )
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = FailingReplyTransport()

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
            "--continue-on-error",
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
    assert stderr.getvalue() == ""
    assert len(transport.requests) == 2
    output = json.loads(stdout.getvalue())
    assert output["attempted"] == 2
    assert output["failed"] == 1
    assert [result["status"] for result in output["results"]] == ["error", "ok"]


def test_cli_comments_requires_path_before_calling_bitbucket():
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "comments", "42"],
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
    assert "--path" in error["message"]
    assert "pr activities" in error["message"]


def test_cli_review_comments_prints_compact_comments_from_activities():
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = ReviewTransport()

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "review-comments", "42"],
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
    assert output == {
        "comments": [
            {
                "id": 100,
                "state": "OPEN",
                "severity": "NORMAL",
                "author": "reviewer",
                "path": "src/app.py",
                "line": 8,
                "line_type": "ADDED",
                "text": "Please add a test.",
                "reply_count": 0,
                "has_replies": False,
            }
        ],
        "count": 1,
        "page": {"start": 0, "limit": 100, "last": True},
    }
    assert [request[1].rsplit("/", 1)[-1] for request in transport.requests] == ["activities?limit=100"]


def test_cli_review_comments_filters_state_locally():
    stdout = io.StringIO()
    transport = StateFilterTransport()

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "review-comments", "42", "--state", "resolved"],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    output = json.loads(stdout.getvalue())
    assert output["count"] == 1
    assert output["comments"][0]["id"] == 101
    assert output["comments"][0]["state"] == "RESOLVED"


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


def test_cli_review_summary_combines_status_comments_and_blockers():
    stdout = io.StringIO()
    transport = ReviewTransport()

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "review-summary", "42"],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    assert stdout.getvalue().lstrip().startswith('{\n  "pull_request"')
    output = json.loads(stdout.getvalue())
    assert output["pull_request"]["id"] == 42
    assert output["pull_request"]["reviewers"] == [
        {"user": "reviewer", "status": "APPROVED", "approved": True}
    ]
    assert output["counts"] == {
        "comments": 1,
        "open_comments": 1,
        "open_comments_with_replies": 0,
        "open_comments_without_replies": 1,
        "blockers": 0,
        "open_blockers": 0,
        "review_events": 0,
        "reviewers": 1,
        "approved_reviewers": 1,
        "needs_work_reviewers": 0,
    }
    assert output["comments"][0]["text"] == "Please add a test."
    assert [request[1].rsplit("/", 1)[-1] for request in transport.requests] == [
        "42",
        "activities?limit=100",
        "blocker-comments?limit=100",
    ]


def test_cli_review_summary_filters_state_locally():
    stdout = io.StringIO()
    transport = StateFilterTransport()

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "review-summary", "42", "--state", "resolved"],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    output = json.loads(stdout.getvalue())
    assert output["counts"]["comments"] == 1
    assert output["counts"]["blockers"] == 1
    assert output["comments"][0]["id"] == 101
    assert output["blockers"][0]["id"] == 201


def test_cli_current_finds_pr_for_source_branch():
    stdout = io.StringIO()
    transport = CurrentPullRequestTransport()

    exit_code = main(
        [
            "--project",
            "ABC",
            "--repo",
            "demo",
            "--source-branch",
            "feature/current",
            "pr",
            "current",
            "--state",
            "ALL",
            "--limit",
            "50",
            "--start",
            "25",
        ],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    output = json.loads(stdout.getvalue())
    assert output["branch"] == "feature/current"
    assert output["count"] == 1
    assert output["pull_requests"][0]["id"] == 42
    assert output["page"] == {"start": 25, "limit": 50, "last": True}
    assert [request[1].rsplit("/", 1)[-1] for request in transport.requests] == [
        "pull-requests?state=ALL&direction=OUTGOING&limit=50&start=25"
    ]


def test_cli_current_requires_source_branch(tmp_path):
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "current"],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        stderr=stderr,
        transport=transport,
        cwd=tmp_path,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert transport.requests == []
    error = json.loads(stderr.getvalue())
    assert "source branch" in error["message"]


def test_cli_current_scans_pages_until_source_branch_matches():
    stdout = io.StringIO()
    transport = PagedCurrentPullRequestTransport()

    exit_code = main(
        [
            "--project",
            "ABC",
            "--repo",
            "demo",
            "--source-branch",
            "feature/current",
            "pr",
            "current",
            "--limit",
            "1",
        ],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    output = json.loads(stdout.getvalue())
    assert output["count"] == 1
    assert output["pull_requests"][0]["id"] == 42
    assert output["scan"] == {"pages": 2, "stopped": "match"}
    assert [request[1].rsplit("/", 1)[-1] for request in transport.requests] == [
        "pull-requests?state=OPEN&direction=OUTGOING&limit=1",
        "pull-requests?state=OPEN&direction=OUTGOING&limit=1&start=1",
    ]


def test_cli_current_scans_until_last_page_when_no_source_branch_matches():
    stdout = io.StringIO()
    transport = NoMatchCurrentPullRequestTransport()

    exit_code = main(
        [
            "--project",
            "ABC",
            "--repo",
            "demo",
            "--source-branch",
            "feature/current",
            "pr",
            "current",
            "--limit",
            "1",
        ],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    output = json.loads(stdout.getvalue())
    assert output["count"] == 0
    assert output["pull_requests"] == []
    assert output["scan"] == {"pages": 2, "stopped": "last"}
    assert [request[1].rsplit("/", 1)[-1] for request in transport.requests] == [
        "pull-requests?state=OPEN&direction=OUTGOING&limit=1",
        "pull-requests?state=OPEN&direction=OUTGOING&limit=1&start=1",
    ]


def test_cli_review_context_prints_unified_diff_by_default():
    stdout = io.StringIO()
    transport = ReviewContextTransport()

    exit_code = main(
        [
            "--project",
            "ABC",
            "--repo",
            "demo",
            "pr",
            "review-context",
            "42",
            "--path",
            "src/app.py",
            "--max-diff-lines",
            "10",
        ],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    output = json.loads(stdout.getvalue())
    assert output["pull_request"]["id"] == 42
    assert output["changed_files"] == [{"path": "src/app.py", "type": "MODIFY", "node_type": "FILE"}]
    assert output["diff_format"] == "unified"
    assert output["diff"] == (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -0 +0 @@\n"
        "+new api"
    )
    assert [request[1].rsplit("/", 1)[-1] for request in transport.requests] == [
        "42",
        "changes?limit=100",
        "diff?path=src%2Fapp.py",
    ]


def test_cli_review_context_can_print_structured_diff():
    stdout = io.StringIO()
    transport = ReviewContextTransport()

    exit_code = main(
        [
            "--project",
            "ABC",
            "--repo",
            "demo",
            "pr",
            "review-context",
            "42",
            "--path",
            "src/app.py",
            "--diff-format",
            "structured",
            "--max-diff-lines",
            "10",
        ],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    output = json.loads(stdout.getvalue())
    assert output["pull_request"]["id"] == 42
    assert output["changed_files"] == [{"path": "src/app.py", "type": "MODIFY", "node_type": "FILE"}]
    assert output["diff_format"] == "structured"
    assert output["counts"]["diff_files"] == 1
    assert output["diff"]["files"][0]["path"] == "src/app.py"
    assert output["diff"]["files"][0]["hunks"][0]["segments"][0]["lines"] == [
        {"destination": 8, "text": "new api"}
    ]
    assert [request[1].rsplit("/", 1)[-1] for request in transport.requests] == [
        "42",
        "changes?limit=100",
        "diff?path=src%2Fapp.py",
    ]


def test_cli_review_context_truncates_unified_diff_at_max_lines():
    stdout = io.StringIO()
    transport = TruncatedUnifiedReviewContextTransport()

    exit_code = main(
        [
            "--project",
            "ABC",
            "--repo",
            "demo",
            "pr",
            "review-context",
            "42",
            "--path",
            "src/app.py",
            "--max-diff-lines",
            "1",
        ],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    output = json.loads(stdout.getvalue())
    assert output["diff_format"] == "unified"
    assert output["counts"]["diff_lines"] == 1
    assert output["counts"]["diff_truncated"] is True
    assert "+first line" in output["diff"]
    assert "second line" not in output["diff"]


def test_cli_review_context_requires_positive_max_diff_lines():
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
            "review-context",
            "42",
            "--max-diff-lines",
            "0",
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
    assert "--max-diff-lines" in error["message"]


def test_cli_project_list_does_not_require_project_or_repo_context():
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        ["project", "list", "--limit", "10"],
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
    assert json.loads(stdout.getvalue())["method"] == "GET"
    assert transport.requests[0][1] == "https://bitbucket.internal/rest/api/1.0/projects?limit=10"


def test_cli_raw_get_does_not_require_project_or_repo_context():
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        ["raw", "GET", "/rest/api/1.0/projects"],
        env=ENV,
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert transport.requests[0][0] == "GET"
    assert transport.requests[0][1] == "https://bitbucket.internal/rest/api/1.0/projects"
    assert transport.requests[0][3] is None


def test_cli_api_error_strips_base_url_userinfo():
    class FailingTransport:
        def __call__(self, method, url, headers, body):
            raise BitbucketAPIError(503, url, "service unavailable")

    stderr = io.StringIO()

    exit_code = main(
        ["raw", "GET", "/rest/api/1.0/projects"],
        env={
            "BITBUCKET_BASE_URL": "https://url-user:url-password@bitbucket.internal:8443/bitbucket",
            "BITBUCKET_USERNAME": "configured-user",
            "BITBUCKET_TOKEN": "secret-token",
        },
        stdout=io.StringIO(),
        stderr=stderr,
        transport=FailingTransport(),
    )

    assert exit_code == 1
    output_text = stderr.getvalue()
    assert "url-user" not in output_text
    assert "url-password" not in output_text
    assert "secret-token" not in output_text
    assert json.loads(output_text) == {
        "error": "BitbucketAPIError",
        "message": "Bitbucket API request failed with HTTP 503: service unavailable",
        "status": 503,
        "url": "https://bitbucket.internal:8443/bitbucket/rest/api/1.0/projects",
    }


def test_cli_api_error_redacts_known_secrets_from_message():
    class SecretEchoingTransport:
        authorization = ""

        def __call__(self, method, url, headers, body):
            self.authorization = headers["Authorization"]
            error_body = (
                f"service unavailable raw-password raw-token {self.authorization} {url} "
                "decoded-user=url-user decoded-password=url-password"
            )
            raise BitbucketAPIError(503, url + "?echo=raw-token", error_body)

    stderr = io.StringIO()
    transport = SecretEchoingTransport()

    exit_code = main(
        ["raw", "GET", "/rest/api/1.0/projects"],
        env={
            "BITBUCKET_BASE_URL": "https://url%2Duser:url%2Dpassword@bitbucket.internal/bitbucket",
            "BITBUCKET_USERNAME": "configured-user",
            "BITBUCKET_PASSWORD": "raw-password",
            "BITBUCKET_TOKEN": "raw-token",
        },
        stdout=io.StringIO(),
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 1
    output_text = stderr.getvalue()
    for secret in (
        "url%2Duser", "url-user", "url%2Dpassword", "url-password",
        "raw-password", "raw-token", "Basic ",
    ):
        assert secret not in output_text
    assert transport.authorization not in output_text
    error = json.loads(output_text)
    assert error["status"] == 503
    assert "service unavailable" in error["message"]


def test_cli_raw_put_strictly_decodes_json_body():
    stdout = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        ["raw", "PUT", "/rest/api/1.0/example", "--json", '{"enabled":true,"count":2}'],
        env=ENV,
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    method, url, headers, body = transport.requests[0]
    assert method == "PUT"
    assert url == "https://bitbucket.internal/rest/api/1.0/example"
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body.decode("utf-8")) == {"enabled": True, "count": 2}


def test_cli_raw_preserves_explicit_json_null_body():
    stdout = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        ["raw", "PATCH", "/rest/api/1.0/example", "--json", "null"],
        env=ENV,
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    _method, _url, headers, body = transport.requests[0]
    assert headers["Content-Type"] == "application/json"
    assert body == b"null"


@pytest.mark.parametrize(
    "target",
    [
        "https://attacker.example/rest/api/1.0/projects",
        "/plugins/servlet/example",
        "/rest/../plugins/servlet/example",
        "/rest/%2e%2e/plugins/servlet/example",
        "/rest/..;/plugins/servlet/example",
        "/rest/%2e%2e%3b/plugins/servlet/example",
        "/rest/%252e%252e%253b/plugins/servlet/example",
    ],
)
def test_cli_raw_rejects_targets_outside_rest_without_transport(target):
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        ["raw", "GET", target],
        env=ENV,
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert transport.requests == []
    error = json.loads(stderr.getvalue())
    assert error["error"] == "ValueError"
    assert "/rest/" in error["message"]


def test_cli_raw_rejects_malformed_json_without_transport():
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        ["raw", "POST", "/rest/api/1.0/example", "--json", '{"broken":'],
        env=ENV,
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert transport.requests == []
    error = json.loads(stderr.getvalue())
    assert error["error"] == "ValueError"
    assert "valid JSON" in error["message"]


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_cli_raw_rejects_nonstandard_json_constants_without_transport(constant):
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        ["raw", "POST", "/rest/api/1.0/example", "--json", '{"value":' + constant + "}"],
        env=ENV,
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert transport.requests == []
    error = json.loads(stderr.getvalue())
    assert error["error"] == "ValueError"
    assert constant in error["message"]


def test_cli_raw_help_lists_controlled_interface(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["raw", "--help"], env={})

    assert exc_info.value.code == 0
    help_output = capsys.readouterr().out
    assert "{GET,POST,PUT,PATCH,DELETE}" in help_output
    assert "/rest/" in help_output
    assert "--json" in help_output


def test_cli_project_get_uses_cli_project_key():
    stdout = io.StringIO()
    transport = CapturingTransport()

    exit_code = main(
        ["--project", "ABC", "project", "get"],
        env={
            "BITBUCKET_BASE_URL": "https://bitbucket.internal",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token",
        },
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    assert transport.requests[0][1] == "https://bitbucket.internal/rest/api/1.0/projects/ABC"


def test_cli_repo_list_and_get_use_project_and_repo_context():
    stdout = io.StringIO()
    transport = CapturingTransport()
    env = {
        "BITBUCKET_BASE_URL": "https://bitbucket.internal",
        "BITBUCKET_USERNAME": "alice",
        "BITBUCKET_TOKEN": "token",
    }

    list_exit = main(["--project", "ABC", "repo", "list"], env=env, stdout=stdout, transport=transport)
    get_exit = main(["--project", "ABC", "--repo", "demo", "repo", "get"], env=env, stdout=stdout, transport=transport)

    assert list_exit == 0
    assert get_exit == 0
    assert transport.requests[0][1] == "https://bitbucket.internal/rest/api/1.0/projects/ABC/repos?limit=25"
    assert transport.requests[1][1] == "https://bitbucket.internal/rest/api/1.0/projects/ABC/repos/demo"


def test_cli_reports_configuration_errors_as_json():
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["pr", "list"], env={}, stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    error = json.loads(stderr.getvalue())
    assert error["error"] == "configuration_error"
    assert "BITBUCKET_BASE_URL" in error["message"]


def test_cli_empty_env_does_not_fall_back_to_process_environment(monkeypatch):
    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = CapturingTransport()
    monkeypatch.setenv("BITBUCKET_BASE_URL", "https://bitbucket.internal")
    monkeypatch.setenv("BITBUCKET_USERNAME", "alice")
    monkeypatch.setenv("BITBUCKET_TOKEN", "token")

    exit_code = main(
        ["--project", "ABC", "--repo", "demo", "pr", "list"],
        env={},
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert transport.requests == []
    error = json.loads(stderr.getvalue())
    assert error["error"] == "configuration_error"
    assert "BITBUCKET_BASE_URL" in error["message"]
