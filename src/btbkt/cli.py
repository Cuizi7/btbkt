from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, TextIO, Union

from .client import BitbucketAPIError, BitbucketClient, Transport
from .compact import (
    compact_current_pull_requests,
    compact_review_comments,
    compact_review_context,
    compact_review_status,
    compact_review_summary,
)
from .context import ConfigError, read_git_info, resolve_context


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
    cwd: Optional[Union[str, Path]] = None,
) -> int:
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        git = read_git_info(cwd)
        require_project, require_repo = _context_requirements(args)
        context = resolve_context(
            env=os.environ if env is None else env,
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
        client = BitbucketClient(
            base_url=context.base_url,
            auth_header=context.auth_header,
            project=context.project,
            repo=context.repo,
            transport=transport,
        )
        result = _dispatch(args, context, client)
        exit_code = 0
        if isinstance(result, CommandResult):
            exit_code = result.exit_code
            result = result.payload
    except ConfigError as exc:
        _write_json(stderr, {"error": "configuration_error", "message": str(exc)})
        return 2
    except (BitbucketAPIError, ValueError) as exc:
        payload: dict[str, Any] = {"error": exc.__class__.__name__, "message": str(exc)}
        if isinstance(exc, BitbucketAPIError):
            payload["status"] = exc.status
            payload["url"] = exc.url
        _write_json(stderr, payload)
        return 1

    _write_json(stdout, result)
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

    return parser


def _context_requirements(args: argparse.Namespace) -> tuple[bool, bool]:
    if args.resource == "project" and args.action == "list":
        return False, False
    if args.resource == "project" and args.action == "get":
        return True, False
    if args.resource == "repo" and args.action == "list":
        return True, False
    return True, True


def _dispatch(args: argparse.Namespace, context, client: BitbucketClient) -> Any:
    if args.resource == "project":
        return _dispatch_project(args, client)
    if args.resource == "repo":
        return _dispatch_repo(args, client)
    if args.resource == "pr":
        return _dispatch_pr(args, context, client)
    raise ValueError(f"Unsupported resource: {args.resource}")


def _dispatch_project(args: argparse.Namespace, client: BitbucketClient) -> Any:
    if args.action == "list":
        return client.list_projects(name=args.name, limit=args.limit, start=args.start)
    if args.action == "get":
        return client.get_project(args.project_key)
    raise ValueError(f"Unsupported project action: {args.action}")


def _dispatch_repo(args: argparse.Namespace, client: BitbucketClient) -> Any:
    if args.action == "list":
        return client.list_repositories(project=args.project_key, limit=args.limit, start=args.start)
    if args.action == "get":
        return client.get_repository(repo=args.repo_slug)
    raise ValueError(f"Unsupported repo action: {args.action}")


def _dispatch_pr(args: argparse.Namespace, context, client: BitbucketClient) -> Any:
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
        return compact_review_comments(activities, state=args.state)
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
        )
    if args.action == "review":
        return client.review_pull_request(
            args.pr_id,
            comment=args.comment,
            approve=args.approve,
            unapprove=args.unapprove,
            needs_work=args.needs_work,
        )
    if args.action == "approve":
        return client.approve_pull_request(args.pr_id)
    if args.action == "unapprove":
        return client.unapprove_pull_request(args.pr_id)
    if args.action == "needs-work":
        return client.request_changes(args.pr_id)
    if args.action == "merge":
        return client.merge_pull_request(args.pr_id, version=args.version, message=args.message)
    if args.action == "decline":
        return client.decline_pull_request(args.pr_id, version=args.version)
    if args.action == "reopen":
        return client.reopen_pull_request(args.pr_id, version=args.version)
    raise ValueError(f"Unsupported PR action: {args.action}")


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
