import json
from urllib.parse import parse_qs, urlparse

from btbkt.client import BitbucketClient


class CapturingTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        return {"ok": True, "method": method}


def test_create_pull_request_posts_data_center_payload():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    result = client.create_pull_request(
        title="feat: add cli",
        description="Agent review workflow",
        source_branch="feature/cli",
        target_branch="main",
        reviewers=["alice", "bob"],
    )

    assert result == {"ok": True, "method": "POST"}
    method, url, headers, body = transport.requests[0]
    assert method == "POST"
    assert url == "https://bitbucket.internal/rest/api/1.0/projects/ABC/repos/demo/pull-requests"
    assert headers["Authorization"] == "Basic token"
    assert headers["Content-Type"] == "application/json"
    payload = json.loads(body.decode("utf-8"))
    assert payload["title"] == "feat: add cli"
    assert payload["fromRef"]["id"] == "refs/heads/feature/cli"
    assert payload["toRef"]["id"] == "refs/heads/main"
    assert payload["reviewers"] == [{"user": {"name": "alice"}}, {"user": {"name": "bob"}}]


def test_list_pull_requests_encodes_query_parameters():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="repo with spaces",
        transport=transport,
    )

    client.list_pull_requests(state="OPEN", direction="INCOMING", limit=25)

    method, url, _headers, body = transport.requests[0]
    assert method == "GET"
    assert body is None
    parsed = urlparse(url)
    assert parsed.path == "/rest/api/1.0/projects/ABC/repos/repo%20with%20spaces/pull-requests"
    assert parse_qs(parsed.query) == {
        "state": ["OPEN"],
        "direction": ["INCOMING"],
        "limit": ["25"],
    }


def test_list_and_get_projects_use_project_endpoints():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    client.list_projects(limit=50)
    client.get_project("ABC")

    list_request, get_request = transport.requests
    assert list_request[0] == "GET"
    assert list_request[1] == "https://bitbucket.internal/rest/api/1.0/projects?limit=50"
    assert get_request[0] == "GET"
    assert get_request[1] == "https://bitbucket.internal/rest/api/1.0/projects/ABC"


def test_list_and_get_repositories_use_project_repo_endpoints():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    client.list_repositories(project="ABC", limit=100)
    client.get_repository(project="ABC", repo="repo with spaces")

    list_request, get_request = transport.requests
    parsed = urlparse(list_request[1])
    assert list_request[0] == "GET"
    assert parsed.path == "/rest/api/1.0/projects/ABC/repos"
    assert parse_qs(parsed.query) == {"limit": ["100"]}
    assert get_request[0] == "GET"
    assert get_request[1] == "https://bitbucket.internal/rest/api/1.0/projects/ABC/repos/repo%20with%20spaces"


def test_get_default_branch_uses_repository_default_branch_endpoint():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    client.get_default_branch()

    assert transport.requests == [
        (
            "GET",
            "https://bitbucket.internal/rest/api/1.0/projects/ABC/repos/demo/default-branch",
            {
                "Authorization": "Basic token",
                "Accept": "application/json",
                "User-Agent": "btbkt/0.1",
            },
            None,
        )
    ]


def test_list_comments_can_filter_by_path_and_state():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    client.list_comments(12, path="src/app.py", state="OPEN", limit=100)

    method, url, _headers, body = transport.requests[0]
    assert method == "GET"
    assert body is None
    parsed = urlparse(url)
    assert parsed.path == "/rest/api/1.0/projects/ABC/repos/demo/pull-requests/12/comments"
    assert parse_qs(parsed.query) == {
        "path": ["src/app.py"],
        "state": ["OPEN"],
        "limit": ["100"],
    }


def test_create_task_posts_blocker_comment():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    client.create_task(12, text="Please add a test.")

    method, url, _headers, body = transport.requests[0]
    assert method == "POST"
    assert url.endswith("/pull-requests/12/blocker-comments")
    assert json.loads(body.decode("utf-8")) == {"text": "Please add a test.", "severity": "BLOCKER"}


def test_update_participant_status_uses_participant_endpoint_and_commit():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    client.update_participant_status(12, "alice-slug", "NEEDS_WORK", "abc123")

    method, url, _headers, body = transport.requests[0]
    assert method == "PUT"
    assert url.endswith("/pull-requests/12/participants/alice-slug")
    assert json.loads(body.decode("utf-8")) == {
        "status": "NEEDS_WORK",
        "lastReviewedCommit": "abc123",
    }


def test_pending_review_methods_use_review_lifecycle_endpoints():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    client.get_pending_review(12, limit=25, start=10)
    client.create_pending_comment(12, text="Needs tests.")
    client.complete_pending_review(
        12,
        participant_status="NEEDS_WORK",
        last_reviewed_commit="abc123",
    )
    client.discard_pending_review(12)

    assert [(request[0], urlparse(request[1]).path) for request in transport.requests] == [
        ("GET", "/rest/api/1.0/projects/ABC/repos/demo/pull-requests/12/review"),
        ("POST", "/rest/api/1.0/projects/ABC/repos/demo/pull-requests/12/comments"),
        ("PUT", "/rest/api/1.0/projects/ABC/repos/demo/pull-requests/12/review"),
        ("DELETE", "/rest/api/1.0/projects/ABC/repos/demo/pull-requests/12/review"),
    ]
    assert parse_qs(urlparse(transport.requests[0][1]).query) == {
        "limit": ["25"],
        "start": ["10"],
    }
    assert json.loads(transport.requests[1][3].decode("utf-8")) == {
        "text": "Needs tests.",
        "state": "PENDING",
    }
    assert json.loads(transport.requests[2][3].decode("utf-8")) == {
        "participantStatus": "NEEDS_WORK",
        "lastReviewedCommit": "abc123",
    }
    assert transport.requests[3][3] is None


def test_create_pending_comment_conditionally_includes_anchor():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )
    anchor = {"path": "src/app.py", "line": 12, "lineType": "ADDED"}

    client.create_pending_comment(12, text="General note.")
    client.create_pending_comment(12, text="Inline note.", anchor=anchor)

    assert json.loads(transport.requests[0][3].decode("utf-8")) == {
        "text": "General note.",
        "state": "PENDING",
    }
    assert json.loads(transport.requests[1][3].decode("utf-8")) == {
        "text": "Inline note.",
        "state": "PENDING",
        "anchor": anchor,
    }


def test_complete_pending_review_allows_commit_without_status():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    client.complete_pending_review(12, last_reviewed_commit="abc123")

    method, url, _headers, body = transport.requests[0]
    assert method == "PUT"
    assert url.endswith("/pull-requests/12/review")
    assert json.loads(body.decode("utf-8")) == {"lastReviewedCommit": "abc123"}


def test_participant_status_methods_reject_unknown_status():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    for call in (
        lambda: client.update_participant_status(12, "alice", "DECLINED"),
        lambda: client.complete_pending_review(12, participant_status="DECLINED"),
    ):
        try:
            call()
        except ValueError as exc:
            assert "APPROVED, UNAPPROVED, or NEEDS_WORK" in str(exc)
        else:
            raise AssertionError("Unknown participant status was accepted")

    assert transport.requests == []


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


def test_raw_omitted_json_body_sends_no_body_or_content_type():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        transport=transport,
    )

    client.raw("GET", "/rest/api/1.0/projects")

    method, _url, headers, body = transport.requests[0]
    assert method == "GET"
    assert body is None
    assert "Content-Type" not in headers


def test_raw_explicit_json_null_sends_json_body_and_content_type():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        transport=transport,
    )

    client.raw("PATCH", "/rest/api/1.0/example", json_body=None)

    method, _url, headers, body = transport.requests[0]
    assert method == "PATCH"
    assert body == b"null"
    assert headers["Content-Type"] == "application/json"


def test_merge_without_message_sends_no_body_or_content_type():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    client.merge_pull_request(42)

    method, _url, headers, body = transport.requests[0]
    assert method == "POST"
    assert body is None
    assert "Content-Type" not in headers


def test_merge_with_message_sends_json_body_and_content_type():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    client.merge_pull_request(42, message="Merge after review")

    method, _url, headers, body = transport.requests[0]
    assert method == "POST"
    assert json.loads(body.decode("utf-8")) == {"message": "Merge after review"}
    assert headers["Content-Type"] == "application/json"
