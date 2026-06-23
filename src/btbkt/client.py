from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Optional
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

Transport = Callable[[str, str, Mapping[str, str], Optional[bytes]], Any]


class BitbucketAPIError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"Bitbucket API request failed with HTTP {status}: {body}")
        self.status = status
        self.url = url
        self.body = body


class BitbucketClient:
    def __init__(
        self,
        *,
        base_url: str,
        auth_header: tuple[str, str],
        project: Optional[str] = None,
        repo: Optional[str] = None,
        transport: Optional[Transport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_header = auth_header
        self.project = project
        self.repo = repo
        self.transport = transport or default_transport

    def list_projects(
        self,
        *,
        name: Optional[str] = None,
        limit: Optional[int] = None,
        start: Optional[int] = None,
    ) -> Any:
        return self._request("GET", self._project_path(), query={"name": name, "limit": limit, "start": start})

    def get_project(self, project: Optional[str] = None) -> Any:
        return self._request("GET", self._project_path(self._require_project(project)))

    def list_repositories(
        self,
        *,
        project: Optional[str] = None,
        limit: Optional[int] = None,
        start: Optional[int] = None,
    ) -> Any:
        return self._request(
            "GET",
            self._project_path(self._require_project(project), "repos"),
            query={"limit": limit, "start": start},
        )

    def get_repository(self, *, project: Optional[str] = None, repo: Optional[str] = None) -> Any:
        return self._request(
            "GET",
            self._project_path(self._require_project(project), "repos", self._require_repo(repo)),
        )

    def list_pull_requests(
        self,
        *,
        state: str = "OPEN",
        direction: Optional[str] = None,
        at: Optional[str] = None,
        limit: Optional[int] = None,
        start: Optional[int] = None,
    ) -> Any:
        return self._request(
            "GET",
            self._repo_path("pull-requests"),
            query={"state": state, "direction": direction, "at": at, "limit": limit, "start": start},
        )

    def get_pull_request(self, pr_id: int) -> Any:
        return self._request("GET", self._repo_path("pull-requests", str(pr_id)))

    def create_pull_request(
        self,
        *,
        title: str,
        description: Optional[str],
        source_branch: str,
        target_branch: str,
        reviewers: Optional[list[str]] = None,
    ) -> Any:
        payload = {
            "title": title,
            "description": description or "",
            "state": "OPEN",
            "open": True,
            "closed": False,
            "fromRef": self._ref_payload(source_branch),
            "toRef": self._ref_payload(target_branch),
            "reviewers": [{"user": {"name": reviewer}} for reviewer in reviewers or []],
        }
        return self._request("POST", self._repo_path("pull-requests"), json_body=payload)

    def get_activities(self, pr_id: int, *, limit: Optional[int] = None, start: Optional[int] = None) -> Any:
        return self._request(
            "GET",
            self._repo_path("pull-requests", str(pr_id), "activities"),
            query={"limit": limit, "start": start},
        )

    def list_comments(
        self,
        pr_id: int,
        *,
        path: Optional[str] = None,
        state: Optional[str] = None,
        limit: Optional[int] = None,
        start: Optional[int] = None,
    ) -> Any:
        return self._request(
            "GET",
            self._repo_path("pull-requests", str(pr_id), "comments"),
            query={"path": path, "state": state, "limit": limit, "start": start},
        )

    def list_tasks(self, pr_id: int, *, limit: Optional[int] = None, start: Optional[int] = None) -> Any:
        return self._request(
            "GET",
            self._repo_path("pull-requests", str(pr_id), "blocker-comments"),
            query={"limit": limit, "start": start},
        )

    def get_changes(self, pr_id: int, *, limit: Optional[int] = None, start: Optional[int] = None) -> Any:
        return self._request(
            "GET",
            self._repo_path("pull-requests", str(pr_id), "changes"),
            query={"limit": limit, "start": start},
        )

    def get_diff(self, pr_id: int, *, path: Optional[str] = None) -> Any:
        query = {"path": path} if path else None
        return self._request("GET", self._repo_path("pull-requests", str(pr_id), "diff"), query=query)

    def comment_pull_request(self, pr_id: int, *, text: str, anchor: Optional[dict[str, Any]] = None) -> Any:
        payload: dict[str, Any] = {"text": text}
        if anchor:
            payload["anchor"] = anchor
        return self._request("POST", self._repo_path("pull-requests", str(pr_id), "comments"), json_body=payload)

    def create_task(self, pr_id: int, *, text: str, anchor: Optional[dict[str, Any]] = None) -> Any:
        payload: dict[str, Any] = {"text": text, "severity": "BLOCKER"}
        if anchor:
            payload["anchor"] = anchor
        return self._request(
            "POST",
            self._repo_path("pull-requests", str(pr_id), "blocker-comments"),
            json_body=payload,
        )

    def reply_to_comment(self, pr_id: int, comment_id: int, *, text: str) -> Any:
        return self._request(
            "POST",
            self._repo_path("pull-requests", str(pr_id), "comments"),
            json_body={"text": text, "parent": {"id": comment_id}},
        )

    def approve_pull_request(self, pr_id: int) -> Any:
        return self.submit_review(pr_id, participant_status="APPROVED")

    def unapprove_pull_request(self, pr_id: int) -> Any:
        return self.submit_review(pr_id, participant_status="UNAPPROVED")

    def request_changes(self, pr_id: int, *, comment: Optional[str] = None) -> Any:
        return self.submit_review(pr_id, comment=comment, participant_status="NEEDS_WORK")

    def submit_review(
        self,
        pr_id: int,
        *,
        comment: Optional[str] = None,
        participant_status: Optional[str] = None,
        last_reviewed_commit: Optional[str] = None,
    ) -> Any:
        payload: dict[str, Any] = {}
        if comment:
            payload["commentText"] = comment
        if participant_status:
            payload["participantStatus"] = participant_status
        if last_reviewed_commit:
            payload["lastReviewedCommit"] = last_reviewed_commit
        if not payload:
            raise ValueError("Review submit requires comment text or participant status.")
        return self._request("PUT", self._repo_path("pull-requests", str(pr_id), "review"), json_body=payload)

    def merge_pull_request(self, pr_id: int, *, version: Optional[int] = None, message: Optional[str] = None) -> Any:
        return self._request(
            "POST",
            self._repo_path("pull-requests", str(pr_id), "merge"),
            query={"version": version},
            json_body={"message": message} if message else None,
        )

    def decline_pull_request(self, pr_id: int, *, version: Optional[int] = None) -> Any:
        return self._request(
            "POST",
            self._repo_path("pull-requests", str(pr_id), "decline"),
            query={"version": version},
        )

    def reopen_pull_request(self, pr_id: int, *, version: Optional[int] = None) -> Any:
        return self._request(
            "POST",
            self._repo_path("pull-requests", str(pr_id), "reopen"),
            query={"version": version},
        )

    def review_pull_request(
        self,
        pr_id: int,
        *,
        comment: Optional[str] = None,
        approve: bool = False,
        unapprove: bool = False,
        needs_work: bool = False,
    ) -> dict[str, Any]:
        statuses = [approve, unapprove, needs_work]
        if sum(bool(status) for status in statuses) > 1:
            raise ValueError("Choose only one of approve, unapprove, or needs_work.")
        if approve or unapprove or needs_work:
            status = "APPROVED" if approve else "UNAPPROVED" if unapprove else "NEEDS_WORK"
            return {"review": self.submit_review(pr_id, comment=comment, participant_status=status)}
        if comment:
            return {"comment": self.comment_pull_request(pr_id, text=comment)}
        raise ValueError("Review requires --comment, --approve, --unapprove, or --needs-work.")

    def raw(self, method: str, path: str, *, query: Optional[dict[str, Any]] = None, json_body: Any = None) -> Any:
        if not path.startswith("/"):
            path = "/" + path
        return self._request(method.upper(), path, query=query, json_body=json_body)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[dict[str, Any]] = None,
        json_body: Any = None,
    ) -> Any:
        body = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        headers = {
            self.auth_header[0]: self.auth_header[1],
            "Accept": "application/json",
            "User-Agent": "btbkt/0.1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        url = self.base_url + path + self._query_string(query)
        return self.transport(method, url, headers, body)

    def _project_path(self, *parts: str) -> str:
        encoded = ["rest", "api", "1.0", "projects", *[quote(part, safe="") for part in parts]]
        return "/" + "/".join(encoded)

    def _repo_path(self, *parts: str) -> str:
        encoded = [
            "rest",
            "api",
            "1.0",
            "projects",
            quote(self._require_project(), safe=""),
            "repos",
            quote(self._require_repo(), safe=""),
            *[quote(part, safe="") for part in parts],
        ]
        return "/" + "/".join(encoded)

    def _ref_payload(self, branch: str) -> dict[str, Any]:
        return {
            "id": _branch_ref(branch),
            "repository": {
                "slug": self._require_repo(),
                "project": {"key": self._require_project()},
            },
        }

    @staticmethod
    def _query_string(query: Optional[dict[str, Any]]) -> str:
        if not query:
            return ""
        clean = {key: value for key, value in query.items() if value is not None}
        if not clean:
            return ""
        return "?" + urlencode(clean)

    def _require_project(self, project: Optional[str] = None) -> str:
        value = project or self.project
        if not value:
            raise ValueError("Missing project. Set --project or run in a Bitbucket git checkout.")
        return value

    def _require_repo(self, repo: Optional[str] = None) -> str:
        value = repo or self.repo
        if not value:
            raise ValueError("Missing repo. Set --repo or run in a Bitbucket git checkout.")
        return value


def default_transport(method: str, url: str, headers: Mapping[str, str], body: Optional[bytes]) -> Any:
    request = Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urlopen(request) as response:
            response_body = response.read()
            content_type = response.headers.get("Content-Type", "")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise BitbucketAPIError(exc.code, url, error_body) from exc

    if not response_body:
        return {}
    text = response_body.decode("utf-8", errors="replace")
    if "json" not in content_type.lower():
        return {"body": text}
    return json.loads(text)


def _branch_ref(branch: str) -> str:
    if branch.startswith("refs/"):
        return branch
    return f"refs/heads/{branch}"
