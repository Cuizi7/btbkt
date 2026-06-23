from __future__ import annotations

import base64
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence, Union
from urllib.parse import unquote, urlparse


class ConfigError(RuntimeError):
    """Raised when required CLI context cannot be resolved."""


@dataclass(frozen=True)
class GitInfo:
    remote_url: Optional[str] = None
    current_branch: Optional[str] = None
    default_branch: Optional[str] = None


@dataclass(frozen=True)
class BitbucketContext:
    base_url: str
    auth_header: tuple[str, str]
    project: Optional[str] = None
    repo: Optional[str] = None
    source_branch: Optional[str] = None
    target_branch: Optional[str] = None


def parse_remote_url(remote_url: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    if not remote_url:
        return None, None, None

    parsed = urlparse(remote_url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        parts = _path_parts(parsed.path)
        scm_index = _index_of(parts, "scm")
        if scm_index is not None and len(parts) > scm_index + 2:
            return _base_url_before(parsed, parts, scm_index), parts[scm_index + 1], _clean_repo_slug(parts[scm_index + 2])
        projects_index = _index_of(parts, "projects")
        if (
            projects_index is not None
            and len(parts) > projects_index + 3
            and parts[projects_index + 2].lower() == "repos"
        ):
            return (
                _base_url_before(parsed, parts, projects_index),
                parts[projects_index + 1],
                _clean_repo_slug(parts[projects_index + 3]),
            )

    if parsed.scheme == "ssh" and parsed.path:
        parts = _path_parts(parsed.path)
        if len(parts) >= 2:
            return None, parts[-2], _clean_repo_slug(parts[-1])

    scp_match = re.match(r"(?:[^@]+@)?[^:]+:(?P<project>[^/]+)/(?P<repo>.+)$", remote_url)
    if scp_match:
        return None, scp_match.group("project"), _clean_repo_slug(scp_match.group("repo"))

    return None, None, None


def read_git_info(cwd: Optional[Union[str, Path]] = None) -> Optional[GitInfo]:
    root = _run_git(["rev-parse", "--show-toplevel"], cwd)
    if not root:
        return None

    repo_root = Path(root)
    remote_url = _run_git(["config", "--get", "remote.origin.url"], repo_root)
    current_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    default_branch = _remote_default_branch(repo_root)
    return GitInfo(remote_url=remote_url, current_branch=current_branch, default_branch=default_branch)


def resolve_context(
    env: Optional[Mapping[str, str]] = None,
    git: Optional[GitInfo] = None,
    *,
    base_url: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    token: Optional[str] = None,
    project: Optional[str] = None,
    repo: Optional[str] = None,
    source_branch: Optional[str] = None,
    target_branch: Optional[str] = None,
    require_project: bool = True,
    require_repo: bool = True,
) -> BitbucketContext:
    env = os.environ if env is None else env
    remote_base_url, git_project, git_repo = parse_remote_url(git.remote_url if git else None)

    base_url = base_url or env.get("BITBUCKET_BASE_URL") or remote_base_url
    username = username or env.get("BITBUCKET_USERNAME")
    password = password or env.get("BITBUCKET_PASSWORD")
    token = token or env.get("BITBUCKET_TOKEN")
    project = project or git_project
    repo = repo or git_repo
    source_branch = source_branch or (git.current_branch if git else None)
    target_branch = target_branch or (git.default_branch if git else None)

    auth_header = _resolve_auth_header(username=username, password=password, token=token)
    missing = _missing_context_fields(
        base_url,
        auth_header,
        project,
        repo,
        require_project=require_project,
        require_repo=require_repo,
    )
    if missing:
        raise ConfigError(
            "Missing Bitbucket context: "
            + ", ".join(missing)
            + ". Set env vars or run inside a Bitbucket git checkout."
        )

    return BitbucketContext(
        base_url=_strip_trailing_slash(base_url),
        auth_header=auth_header,
        project=project,
        repo=repo,
        source_branch=source_branch,
        target_branch=target_branch,
    )


def _resolve_auth_header(
    *,
    username: Optional[str],
    password: Optional[str],
    token: Optional[str],
) -> Optional[tuple[str, str]]:
    if username and (password or token):
        secret = password or token
        raw = f"{username}:{secret}".encode("utf-8")
        return "Authorization", "Basic " + base64.b64encode(raw).decode("ascii")
    return None


def _missing_context_fields(
    base_url: Optional[str],
    auth_header: Optional[tuple[str, str]],
    project: Optional[str],
    repo: Optional[str],
    *,
    require_project: bool,
    require_repo: bool,
) -> list[str]:
    missing: list[str] = []
    if not base_url:
        missing.append("BITBUCKET_BASE_URL")
    if not auth_header:
        missing.append("BITBUCKET_USERNAME plus BITBUCKET_PASSWORD or BITBUCKET_TOKEN")
    if require_project and not project:
        missing.append("project")
    if require_repo and not repo:
        missing.append("repo")
    return missing


def _remote_default_branch(repo_root: Path) -> Optional[str]:
    value = _run_git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], repo_root)
    if value and "/" in value:
        return value.split("/", 1)[1]
    return _run_git(["config", "--get", "init.defaultBranch"], repo_root) or "main"


def _run_git(args: Sequence[str], cwd: Optional[Union[str, Path]]) -> Optional[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _url_origin(parsed) -> str:
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return f"{parsed.scheme}://{netloc}"


def _base_url_before(parsed, parts: list[str], marker_index: int) -> str:
    prefix = "/".join(parts[:marker_index])
    if not prefix:
        return _url_origin(parsed)
    return f"{_url_origin(parsed)}/{prefix}"


def _index_of(parts: list[str], value: str) -> Optional[int]:
    for index, part in enumerate(parts):
        if part.lower() == value:
            return index
    return None


def _path_parts(path: str) -> list[str]:
    return [unquote(part) for part in path.split("/") if part]


def _clean_repo_slug(repo: str) -> str:
    if repo.endswith(".git"):
        return repo[:-4]
    return repo


def _strip_trailing_slash(value: str) -> str:
    return value.rstrip("/")
