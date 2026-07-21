from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, TextIO, Union
from urllib.parse import unquote, urlsplit, urlunsplit

from .client import BitbucketAPIError, BitbucketClient, Transport
from .compact import (
    add_diff_context_to_comments,
    compact_current_pull_requests,
    compact_review_comments,
    compact_review_context,
    compact_review_status,
    compact_review_summary,
)
from .context import ConfigError, read_git_info, resolve_context
from .repo_access import GitAuth, GitOperationError, GitRunner, RefRequest, RepositoryAccess


@dataclass(frozen=True)
class CommandResult:
    payload: Any
    exit_code: int = 0


def console() -> None:
    raise SystemExit(main())


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    transport: Optional[Transport] = None,
    git_runner: Optional[GitRunner] = None,
    cwd: Optional[Union[str, Path]] = None,
) -> int:
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    parser = build_parser()
    args = parser.parse_args(argv)
    effective_env = os.environ if env is None else env
    error_redactions: tuple[str, ...] = ()

    try:
        git = read_git_info(cwd)
        require_project, require_repo = _context_requirements(args)
        context = resolve_context(
            env=effective_env,
            git=git,
            base_url=args.base_url,
            username=args.username,
            password=args.password,
            token=args.token,
            project=getattr(args, "project_key", None) or args.project,
            repo=getattr(args, "repo_slug", None) or args.repo,
            source_branch=args.source_branch,
            target_branch=args.target_branch,
            require_project=require_project,
            require_repo=require_repo,
        )
        error_redactions = _error_redaction_values(args, effective_env, context)
        client = BitbucketClient(
            base_url=context.base_url,
            auth_header=context.auth_header,
            project=context.project,
            repo=context.repo,
            transport=transport,
        )
        result = _dispatch(
            args,
            context,
            client,
            error_redactions,
            git_runner=git_runner,
            git_secret=_git_secret(args, effective_env),
        )
        exit_code = 0
        if isinstance(result, CommandResult):
            exit_code = result.exit_code
            result = result.payload
    except ConfigError as exc:
        _write_json(stderr, {"error": "configuration_error", "message": str(exc)})
        return 2
    except (BitbucketAPIError, ValueError) as exc:
        message = _redact_text(str(exc), error_redactions)
        payload: dict[str, Any] = {"error": exc.__class__.__name__, "message": message}
        if isinstance(exc, BitbucketAPIError):
            payload["status"] = exc.status
            payload["url"] = _redact_text(_strip_url_userinfo(exc.url), error_redactions)
        _write_json(stderr, payload)
        return 1
    except GitOperationError as exc:
        payload = {
            "error": "git_operation_error",
            "message": _redact_text(str(exc), error_redactions),
        }
        if exc.state:
            payload["state"] = exc.state
        if exc.recovery:
            payload["recovery"] = exc.recovery
        _write_json(stderr, _redact_value(payload, error_redactions))
        return 1

    _write_json(stdout, _redact_value(result, error_redactions))
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btbkt",
        description="Thin agent-friendly Bitbucket Data Center REST API CLI.",
    )
    parser.add_argument("--base-url", help="Bitbucket base URL. Env: BITBUCKET_BASE_URL.")
    parser.add_argument("--token", help="Basic auth token used as the password. Env: BITBUCKET_TOKEN.")
    parser.add_argument("--username", help="Basic auth username. Env: BITBUCKET_USERNAME.")
    parser.add_argument("--password", help="Basic auth password. Env: BITBUCKET_PASSWORD.")
    parser.add_argument("--project", help="Bitbucket project key. Defaults from git remote.")
    parser.add_argument("--repo", help="Bitbucket repository slug. Defaults from git remote.")
    parser.add_argument("--source-branch", help="Source branch. Defaults from current git branch.")
    parser.add_argument("--target-branch", help="Target branch. Defaults from origin/HEAD, init.defaultBranch, or main.")

    resources = parser.add_subparsers(dest="resource", required=True)

    project = resources.add_parser("project", help="Project workflows.")
    project_actions = project.add_subparsers(dest="action", required=True)

    project_list = project_actions.add_parser("list", help="List projects.")
    project_list.add_argument("--name", help="Filter projects by name.")
    project_list.add_argument("--limit", type=int, default=25)
    project_list.add_argument("--start", type=int)

    project_get = project_actions.add_parser("get", help="Get one project.")
    project_get.add_argument("project_key", nargs="?")

    repo = resources.add_parser("repo", help="Repository workflows.")
    repo_actions = repo.add_subparsers(dest="action", required=True)

    repo_list = repo_actions.add_parser("list", help="List repositories in a project.")
    repo_list.add_argument("project_key", nargs="?")
    repo_list.add_argument("--limit", type=int, default=25)
    repo_list.add_argument("--start", type=int)

    repo_get = repo_actions.add_parser("get", help="Get one repository.")
    repo_get.add_argument("repo_slug", nargs="?")

    for action, help_text in (
        ("clone", "Clone a repository with btbkt-managed authentication."),
        ("fetch", "Fetch one repository ref without changing the worktree."),
        ("ensure", "Clone or safely fast-forward an existing repository checkout."),
    ):
        access_parser = repo_actions.add_parser(action, help=help_text)
        ref_group = access_parser.add_mutually_exclusive_group()
        ref_group.add_argument("--branch", help="Fetch a branch by name.")
        ref_group.add_argument("--tag", help="Fetch a tag by name.")
        ref_group.add_argument("--commit", help="Fetch a full 40- or 64-character hexadecimal commit ID.")
        ref_group.add_argument("--pr", type=int, help="Fetch the source commit of a pull request.")
        ref_group.add_argument("--ref", help="Fetch a branch/tag ref, full commit ID, or unambiguous name.")
        access_parser.add_argument("path", type=Path, help="Destination or existing checkout path.")

    pr = resources.add_parser("pr", help="Pull request workflows.")
    pr_actions = pr.add_subparsers(dest="action", required=True)

    list_parser = pr_actions.add_parser("list", help="List pull requests.")
    list_parser.add_argument("--state", default="OPEN")
    list_parser.add_argument("--direction", choices=["INCOMING", "OUTGOING"])
    list_parser.add_argument("--at", help="Branch or commit to filter by.")
    list_parser.add_argument("--limit", type=int, default=25)
    list_parser.add_argument("--start", type=int)

    current_parser = pr_actions.add_parser("current", help="Find open outgoing PRs for the current source branch.")
    current_parser.add_argument("--state", default="OPEN")
    current_parser.add_argument("--limit", type=int, default=100)
    current_parser.add_argument("--start", type=int)

    get_parser = pr_actions.add_parser("get", help="Get one pull request.")
    get_parser.add_argument("pr_id", type=int)

    create_parser = pr_actions.add_parser("create", help="Create a pull request.")
    create_parser.add_argument("--title", required=True)
    create_parser.add_argument("--description", default="")
    create_parser.add_argument("--source")
    create_parser.add_argument("--target")
    create_parser.add_argument("--reviewer", action="append", default=[])

    activities_parser = pr_actions.add_parser("activities", help="Show PR activity, including comments.")
    activities_parser.add_argument("pr_id", type=int)
    activities_parser.add_argument("--limit", type=int, default=100)
    activities_parser.add_argument("--start", type=int)

    review_comments_parser = pr_actions.add_parser(
        "review-comments",
        help="Show compact PR review comments from the activity stream.",
    )
    review_comments_parser.add_argument("pr_id", type=int)
    review_comments_parser.add_argument("--state", help="Locally filter compact comments by state.")
    review_comments_parser.add_argument("--limit", type=int, default=100)
    review_comments_parser.add_argument("--start", type=int)
    review_comments_parser.add_argument(
        "--with-diff-context",
        type=int,
        help="Attach bounded diff context around each anchored comment using this line radius.",
    )

    review_status_parser = pr_actions.add_parser("review-status", help="Show compact PR reviewer status.")
    review_status_parser.add_argument("pr_id", type=int)

    review_summary_parser = pr_actions.add_parser(
        "review-summary",
        help="Show compact PR status, comments, review events, and blockers.",
    )
    review_summary_parser.add_argument("pr_id", type=int)
    review_summary_parser.add_argument("--state", help="Locally filter compact comments and blockers by state.")
    review_summary_parser.add_argument("--limit", type=int, default=100)
    review_summary_parser.add_argument("--start", type=int)

    review_context_parser = pr_actions.add_parser(
        "review-context",
        help="Show compact PR metadata, changed files, and capped diff context.",
    )
    review_context_parser.add_argument("pr_id", type=int)
    review_context_parser.add_argument("--path", help="Limit diff context to one file path.")
    review_context_parser.add_argument("--limit", type=int, default=100)
    review_context_parser.add_argument("--start", type=int)
    review_context_parser.add_argument("--max-diff-lines", type=int, default=200)
    review_context_parser.add_argument("--diff-format", choices=["unified", "structured"], default="unified")

    comments_parser = pr_actions.add_parser("comments", help="List PR comments.")
    comments_parser.add_argument("pr_id", type=int)
    comments_parser.add_argument("--path", help="Filter comments by file path.")
    comments_parser.add_argument("--state", help="Filter comments by state.")
    comments_parser.add_argument("--limit", type=int, default=100)
    comments_parser.add_argument("--start", type=int)

    tasks_parser = pr_actions.add_parser("tasks", help="List PR blocker tasks.")
    tasks_parser.add_argument("pr_id", type=int)
    tasks_parser.add_argument("--limit", type=int, default=100)
    tasks_parser.add_argument("--start", type=int)

    changes_parser = pr_actions.add_parser("changes", help="Show changed files for a PR.")
    changes_parser.add_argument("pr_id", type=int)
    changes_parser.add_argument("--limit", type=int, default=500)
    changes_parser.add_argument("--start", type=int)

    diff_parser = pr_actions.add_parser("diff", help="Show PR diff.")
    diff_parser.add_argument("pr_id", type=int)
    diff_parser.add_argument("--path", help="Limit diff to a file path.")

    comment_parser = pr_actions.add_parser("comment", help="Add a PR comment.")
    comment_parser.add_argument("pr_id", type=int)
    comment_parser.add_argument("--text", required=True)
    comment_parser.add_argument("--anchor-json", help="Raw Bitbucket comment anchor JSON for inline comments.")

    task_parser = pr_actions.add_parser("task", help="Create a PR blocker task.")
    task_parser.add_argument("pr_id", type=int)
    task_parser.add_argument("--text", required=True)
    task_parser.add_argument("--anchor-json", help="Raw Bitbucket comment anchor JSON for inline tasks.")

    reply_parser = pr_actions.add_parser("reply", help="Reply to a PR comment.")
    reply_parser.add_argument("pr_id", type=int)
    reply_parser.add_argument("comment_id", type=int)
    reply_parser.add_argument("--text", required=True)

    reply_many_parser = pr_actions.add_parser("reply-many", help="Reply to multiple PR comments from a JSON file.")
    reply_many_parser.add_argument("pr_id", type=int)
    reply_many_parser.add_argument("--input", required=True, help="JSON file containing reply objects.")
    reply_many_parser.add_argument("--dry-run", action="store_true", help="Validate and print planned replies without posting.")
    reply_many_parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Attempt remaining replies after a failed reply.",
    )

    review_parser = pr_actions.add_parser("review", help="Submit a compact agent review action.")
    review_parser.add_argument("pr_id", type=int)
    review_parser.add_argument("--comment")
    review_parser.add_argument("--approve", action="store_true")
    review_parser.add_argument("--unapprove", action="store_true")
    review_parser.add_argument("--needs-work", action="store_true")

    review_pending_parser = pr_actions.add_parser("review-pending", help="Show the current user's pending review.")
    review_pending_parser.add_argument("pr_id", type=int)
    review_pending_parser.add_argument("--limit", type=int)
    review_pending_parser.add_argument("--start", type=int)

    review_submit_parser = pr_actions.add_parser("review-submit", help="Complete the current user's pending review.")
    review_submit_parser.add_argument("pr_id", type=int)
    review_submit_parser.add_argument("--approve", action="store_true")
    review_submit_parser.add_argument("--unapprove", action="store_true")
    review_submit_parser.add_argument("--needs-work", action="store_true")

    review_discard_parser = pr_actions.add_parser("review-discard", help="Discard the current user's pending review.")
    review_discard_parser.add_argument("pr_id", type=int)

    approve_parser = pr_actions.add_parser("approve", help="Approve a PR.")
    approve_parser.add_argument("pr_id", type=int)

    unapprove_parser = pr_actions.add_parser("unapprove", help="Remove PR approval.")
    unapprove_parser.add_argument("pr_id", type=int)

    needs_work_parser = pr_actions.add_parser("needs-work", help="Request changes on a PR.")
    needs_work_parser.add_argument("pr_id", type=int)

    merge_parser = pr_actions.add_parser("merge", help="Merge a PR.")
    merge_parser.add_argument("pr_id", type=int)
    merge_parser.add_argument("--version", type=int)
    merge_parser.add_argument("--message")

    decline_parser = pr_actions.add_parser("decline", help="Decline a PR.")
    decline_parser.add_argument("pr_id", type=int)
    decline_parser.add_argument("--version", type=int)

    reopen_parser = pr_actions.add_parser("reopen", help="Reopen a declined PR.")
    reopen_parser.add_argument("pr_id", type=int)
    reopen_parser.add_argument("--version", type=int)

    raw_parser = resources.add_parser("raw", help="Call a controlled Bitbucket REST endpoint.")
    raw_parser.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    raw_parser.add_argument("path", help="Absolute Bitbucket REST path beginning with /rest/.")
    raw_parser.add_argument("--json", help="Strict JSON request body.")

    return parser


def _context_requirements(args: argparse.Namespace) -> tuple[bool, bool]:
    if args.resource == "raw":
        return False, False
    if args.resource == "project" and args.action == "list":
        return False, False
    if args.resource == "project" and args.action == "get":
        return True, False
    if args.resource == "repo" and args.action == "list":
        return True, False
    return True, True


def _dispatch(
    args: argparse.Namespace,
    context,
    client: BitbucketClient,
    error_redactions: Sequence[str],
    *,
    git_runner: Optional[GitRunner],
    git_secret: str,
) -> Any:
    if args.resource == "project":
        return _dispatch_project(args, client)
    if args.resource == "repo":
        return _dispatch_repo(
            args,
            context,
            client,
            git_runner=git_runner,
            git_secret=git_secret,
        )
    if args.resource == "pr":
        return _dispatch_pr(args, context, client, error_redactions)
    if args.resource == "raw":
        decoded_path = urlsplit(args.path).path
        while True:
            next_decoded_path = unquote(decoded_path)
            if next_decoded_path == decoded_path:
                break
            decoded_path = next_decoded_path
        path_segments = decoded_path.replace("\\", "/").split("/")
        if (
            "://" in args.path
            or not args.path.startswith("/rest/")
            or any(segment in {".", ".."} or ";" in segment for segment in path_segments)
        ):
            raise ValueError("Raw target must be a path beginning with /rest/; absolute URLs are not allowed.")
        if args.json is None:
            return client.raw(args.method, args.path)
        try:
            json_body = json.loads(args.json, parse_constant=_reject_json_constant)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--json must be valid JSON: {exc}") from exc
        return client.raw(args.method, args.path, json_body=json_body)
    raise ValueError(f"Unsupported resource: {args.resource}")


def _dispatch_project(args: argparse.Namespace, client: BitbucketClient) -> Any:
    if args.action == "list":
        return client.list_projects(name=args.name, limit=args.limit, start=args.start)
    if args.action == "get":
        return client.get_project(args.project_key)
    raise ValueError(f"Unsupported project action: {args.action}")


def _dispatch_repo(
    args: argparse.Namespace,
    context,
    client: BitbucketClient,
    *,
    git_runner: Optional[GitRunner],
    git_secret: str,
) -> Any:
    if args.action == "list":
        return client.list_repositories(project=args.project_key, limit=args.limit, start=args.start)
    if args.action == "get":
        return client.get_repository(repo=args.repo_slug)
    if args.action in {"clone", "fetch", "ensure"}:
        if not context.username or not git_secret:
            raise ConfigError("Missing Bitbucket Git credentials.")
        access = RepositoryAccess(
            client=client,
            project=context.project,
            repo=context.repo,
            auth=GitAuth(username=context.username, secret=git_secret),
            runner=git_runner,
        )
        request = _repository_ref_request(args)
        result = getattr(access, args.action)(args.path, request)
        return CommandResult(payload=result.to_dict(), exit_code=result.exit_code)
    raise ValueError(f"Unsupported repo action: {args.action}")


def _repository_ref_request(args: argparse.Namespace) -> Optional[RefRequest]:
    for kind in ("branch", "tag", "commit", "pr", "ref"):
        value = getattr(args, kind, None)
        if value is not None:
            return RefRequest(kind, str(value))
    return None


def _dispatch_pr(
    args: argparse.Namespace,
    context,
    client: BitbucketClient,
    error_redactions: Sequence[str],
) -> Any:
    if args.action == "list":
        return client.list_pull_requests(
            state=args.state,
            direction=args.direction,
            at=args.at,
            limit=args.limit,
            start=args.start,
        )
    if args.action == "current":
        if not context.source_branch:
            raise ValueError("Missing source branch. Set --source-branch or run in a git branch.")
        return _current_pull_requests(args, context, client)
    if args.action == "get":
        return client.get_pull_request(args.pr_id)
    if args.action == "create":
        source = args.source or context.source_branch
        target = args.target or context.target_branch or "main"
        if not source:
            raise ValueError("Missing source branch. Set --source, --source-branch, or run in a git branch.")
        return client.create_pull_request(
            title=args.title,
            description=args.description,
            source_branch=source,
            target_branch=target,
            reviewers=args.reviewer,
        )
    if args.action == "activities":
        return client.get_activities(args.pr_id, limit=args.limit, start=args.start)
    if args.action == "review-comments":
        activities = client.get_activities(args.pr_id, limit=args.limit, start=args.start)
        result = compact_review_comments(activities, state=args.state)
        if args.with_diff_context is not None:
            if args.with_diff_context < 0:
                raise ValueError("--with-diff-context must be zero or positive.")
            diff_by_path = _diffs_for_comment_paths(client, args.pr_id, result["comments"])
            result = add_diff_context_to_comments(result, diff_by_path, radius=args.with_diff_context)
        return result
    if args.action == "review-status":
        return compact_review_status(client.get_pull_request(args.pr_id))
    if args.action == "review-summary":
        pull_request = client.get_pull_request(args.pr_id)
        activities = client.get_activities(args.pr_id, limit=args.limit, start=args.start)
        blocker_comments = client.list_tasks(args.pr_id, limit=args.limit, start=args.start)
        return compact_review_summary(pull_request, activities, blocker_comments, state=args.state)
    if args.action == "review-context":
        if args.max_diff_lines < 1:
            raise ValueError("--max-diff-lines must be positive.")
        pull_request = client.get_pull_request(args.pr_id)
        changes = client.get_changes(args.pr_id, limit=args.limit, start=args.start)
        diff = client.get_diff(args.pr_id, path=args.path)
        return compact_review_context(
            pull_request,
            changes,
            diff,
            max_diff_lines=args.max_diff_lines,
            path=args.path,
            diff_format=args.diff_format,
        )
    if args.action == "comments":
        if not args.path:
            raise ValueError("Bitbucket requires --path for pr comments. Use pr activities to inspect all PR comments.")
        return client.list_comments(
            args.pr_id,
            path=args.path,
            state=args.state,
            limit=args.limit,
            start=args.start,
        )
    if args.action == "tasks":
        return client.list_tasks(args.pr_id, limit=args.limit, start=args.start)
    if args.action == "changes":
        return client.get_changes(args.pr_id, limit=args.limit, start=args.start)
    if args.action == "diff":
        return client.get_diff(args.pr_id, path=args.path)
    if args.action == "comment":
        anchor = json.loads(args.anchor_json) if args.anchor_json else None
        return client.comment_pull_request(args.pr_id, text=args.text, anchor=anchor)
    if args.action == "task":
        anchor = json.loads(args.anchor_json) if args.anchor_json else None
        return client.create_task(args.pr_id, text=args.text, anchor=anchor)
    if args.action == "reply":
        return client.reply_to_comment(args.pr_id, args.comment_id, text=args.text)
    if args.action == "reply-many":
        replies = _load_reply_many_input(Path(args.input))
        if args.dry_run:
            return {"dry_run": True, "count": len(replies), "replies": replies}
        return _reply_many(
            client,
            args.pr_id,
            replies,
            continue_on_error=args.continue_on_error,
            error_redactions=error_redactions,
        )
    if args.action == "review":
        status = _decision_status(args)
        if args.comment is not None and not args.comment.strip():
            raise ValueError("Review comment must be a non-empty string.")
        if args.comment is not None and status:
            return _combined_review(
                client,
                context,
                args.pr_id,
                args.comment,
                status,
                error_redactions=error_redactions,
            )
        if args.comment is not None:
            return client.comment_pull_request(args.pr_id, text=args.comment)
        if status:
            return _update_review_status(client, context, args.pr_id, status)
        raise ValueError("Review requires --comment, --approve, --unapprove, or --needs-work.")
    if args.action == "review-pending":
        return client.get_pending_review(args.pr_id, limit=args.limit, start=args.start)
    if args.action == "review-submit":
        status = _decision_status(args)
        if status is None:
            raise ValueError("Review submit requires exactly one decision.")
        pull_request = client.get_pull_request(args.pr_id)
        return client.complete_pending_review(
            args.pr_id,
            participant_status=status,
            last_reviewed_commit=_pull_request_commit(pull_request),
        )
    if args.action == "review-discard":
        return client.discard_pending_review(args.pr_id)
    if args.action == "approve":
        return _update_review_status(client, context, args.pr_id, "APPROVED")
    if args.action == "unapprove":
        return _update_review_status(client, context, args.pr_id, "UNAPPROVED")
    if args.action == "needs-work":
        return _update_review_status(client, context, args.pr_id, "NEEDS_WORK")
    if args.action == "merge":
        return client.merge_pull_request(args.pr_id, version=args.version, message=args.message)
    if args.action == "decline":
        return client.decline_pull_request(args.pr_id, version=args.version)
    if args.action == "reopen":
        return client.reopen_pull_request(args.pr_id, version=args.version)
    raise ValueError(f"Unsupported PR action: {args.action}")


def _pull_request_commit(pull_request: Mapping[str, Any]) -> Optional[str]:
    from_ref = pull_request.get("fromRef")
    if not isinstance(from_ref, Mapping):
        return None
    commit = from_ref.get("latestCommit")
    return commit if isinstance(commit, str) and commit else None


def _current_user_slug(pull_request: Mapping[str, Any], username: str) -> str:
    participants = []
    for key in ("reviewers", "participants"):
        values = pull_request.get(key)
        if isinstance(values, list):
            participants.extend(values)

    for participant in participants:
        if not isinstance(participant, Mapping):
            continue
        user = participant.get("user")
        if not isinstance(user, Mapping):
            continue
        slug = user.get("slug")
        name = user.get("name")
        if username in (slug, name):
            return slug if isinstance(slug, str) and slug else username
    return username


def _decision_status(args: argparse.Namespace) -> Optional[str]:
    decisions = [
        (getattr(args, "approve", False), "APPROVED"),
        (getattr(args, "unapprove", False), "UNAPPROVED"),
        (getattr(args, "needs_work", False), "NEEDS_WORK"),
    ]
    selected = [status for enabled, status in decisions if enabled]
    if len(selected) > 1:
        raise ValueError("Choose exactly one of --approve, --unapprove, or --needs-work.")
    return selected[0] if selected else None


def _update_review_status(client, context, pr_id: int, status: str) -> Any:
    pull_request = client.get_pull_request(pr_id)
    username = context.username
    if not username:
        raise ValueError("Missing Bitbucket username for participant status update.")
    return client.update_participant_status(
        pr_id,
        _current_user_slug(pull_request, username),
        status,
        _pull_request_commit(pull_request),
    )


def _combined_review(
    client,
    context,
    pr_id: int,
    comment: str,
    status: str,
    *,
    error_redactions: Sequence[str],
) -> CommandResult:
    pull_request = client.get_pull_request(pr_id)
    pending_review = client.get_pending_review(pr_id)
    decision_flag = {
        "APPROVED": "--approve",
        "UNAPPROVED": "--unapprove",
        "NEEDS_WORK": "--needs-work",
    }[status]
    command_parts = ["btbkt"]
    for flag, value in (
        ("--base-url", _strip_url_userinfo(context.base_url)),
        ("--username", context.username),
        ("--project", context.project),
        ("--repo", context.repo),
    ):
        if value:
            command_parts.extend((flag, shlex.quote(value)))
    command_prefix = " ".join(command_parts)
    recovery = [
        f"{command_prefix} pr review-pending {pr_id}",
        f"{command_prefix} pr review-submit {pr_id} {decision_flag}",
        f"{command_prefix} pr review-discard {pr_id}",
    ]
    pending_values = pending_review.get("values") if isinstance(pending_review, Mapping) else None
    if isinstance(pending_values, list) and pending_values:
        return CommandResult(
            payload={
                "status": "blocked",
                "steps": [{"operation": "preflight_pending_review", "response": pending_review}],
                "recovery": recovery,
            },
            exit_code=1,
        )

    comment_response = client.create_pending_comment(pr_id, text=comment)
    steps = [{"operation": "create_pending_comment", "status": "ok", "response": comment_response}]
    try:
        completion_response = client.complete_pending_review(
            pr_id,
            participant_status=status,
            last_reviewed_commit=_pull_request_commit(pull_request),
        )
    except BitbucketAPIError as exc:
        steps.append(
            {
                "operation": "complete_pending_review",
                "status": "error",
                "error": {
                    "status": exc.status,
                    "url": _redact_text(_strip_url_userinfo(exc.url), error_redactions),
                    "message": _redact_text(str(exc), error_redactions),
                },
            }
        )
        return CommandResult(
            payload={"status": "partial", "steps": steps, "recovery": recovery},
            exit_code=1,
        )

    steps.append(
        {
            "operation": "complete_pending_review",
            "status": "ok",
            "response": completion_response,
        }
    )
    return CommandResult(payload={"status": "completed", "steps": steps})


def _strip_url_userinfo(url: str) -> str:
    parsed = urlsplit(url)
    if "@" not in parsed.netloc:
        return url
    sanitized_netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parsed.scheme, sanitized_netloc, parsed.path, parsed.query, parsed.fragment))


def _error_redaction_values(args, env: Mapping[str, str], context) -> tuple[str, ...]:
    parsed_base_url = urlsplit(context.base_url)
    url_username = parsed_base_url.username
    url_password = parsed_base_url.password
    candidates = (
        args.password or env.get("BITBUCKET_PASSWORD"),
        args.token or env.get("BITBUCKET_TOKEN"),
        context.auth_header[1],
        url_username,
        unquote(url_username) if url_username else None,
        url_password,
        unquote(url_password) if url_password else None,
    )
    return tuple(sorted({value for value in candidates if value}, key=len, reverse=True))


def _git_secret(args, env: Mapping[str, str]) -> str:
    password = args.password or env.get("BITBUCKET_PASSWORD")
    token = args.token or env.get("BITBUCKET_TOKEN")
    return password or token or ""


def _redact_text(text: str, redactions: Sequence[str]) -> str:
    for value in redactions:
        text = text.replace(value, "[REDACTED]")
    return text


def _redact_value(value: Any, redactions: Sequence[str]) -> Any:
    if isinstance(value, str):
        return _redact_text(value, redactions)
    if isinstance(value, list):
        return [_redact_value(item, redactions) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item, redactions) for item in value]
    if isinstance(value, Mapping):
        return {
            _redact_text(str(key), redactions): _redact_value(item, redactions)
            for key, item in value.items()
        }
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"--json must use standard JSON values; {value} is not allowed.")


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


def _reply_many(
    client: BitbucketClient,
    pr_id: int,
    replies: list[dict[str, Any]],
    *,
    continue_on_error: bool,
    error_redactions: Sequence[str],
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
                        "url": _redact_text(_strip_url_userinfo(exc.url), error_redactions),
                        "message": _redact_text(str(exc), error_redactions),
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


def _current_pull_requests(args: argparse.Namespace, context, client: BitbucketClient) -> dict[str, Any]:
    start = args.start
    pages = 0
    values = []
    last_page: dict[str, Any] = {"values": []}
    stopped = "last"

    while True:
        page = client.list_pull_requests(
            state=args.state,
            direction="OUTGOING",
            limit=args.limit,
            start=start,
        )
        pages += 1
        if isinstance(page, dict):
            last_page = page
            page_values = page.get("values")
            if isinstance(page_values, list):
                values.extend(page_values)
            current_page = compact_current_pull_requests(page, context.source_branch)
            if current_page["count"]:
                stopped = "match"
                break
            if page.get("isLastPage"):
                stopped = "last"
                break
            start = page.get("nextPageStart")
            if start is None:
                stopped = "no_next_page"
                break
            continue
        last_page = {"values": values}
        stopped = "unexpected_page"
        break

    merged_page = {
        "start": args.start if args.start is not None else 0,
        "limit": args.limit,
        "nextPageStart": last_page.get("nextPageStart"),
        "isLastPage": last_page.get("isLastPage"),
        "values": values,
    }
    result = compact_current_pull_requests(merged_page, context.source_branch)
    result["scan"] = {"pages": pages, "stopped": stopped}
    return result


def _write_json(stream: TextIO, payload: Any) -> None:
    json.dump(payload, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
