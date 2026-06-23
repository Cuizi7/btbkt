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


def test_review_with_approval_uses_official_review_endpoint():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    result = client.review_pull_request(12, comment="Looks good.", approve=True)

    assert result == {"review": {"ok": True, "method": "PUT"}}
    review_request = transport.requests[0]
    assert review_request[0] == "PUT"
    assert review_request[1].endswith("/pull-requests/12/review")
    assert json.loads(review_request[3].decode("utf-8")) == {
        "commentText": "Looks good.",
        "participantStatus": "APPROVED",
    }


def test_review_comment_without_status_posts_a_normal_comment():
    transport = CapturingTransport()
    client = BitbucketClient(
        base_url="https://bitbucket.internal",
        auth_header=("Authorization", "Basic token"),
        project="ABC",
        repo="demo",
        transport=transport,
    )

    result = client.review_pull_request(12, comment="Please check this.")

    assert result == {"comment": {"ok": True, "method": "POST"}}
    comment_request = transport.requests[0]
    assert comment_request[0] == "POST"
    assert comment_request[1].endswith("/pull-requests/12/comments")
    assert json.loads(comment_request[3].decode("utf-8")) == {"text": "Please check this."}
