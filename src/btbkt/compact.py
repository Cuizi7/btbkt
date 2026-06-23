from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

_REVIEW_ACTIONS = {
    "APPROVED",
    "UNAPPROVED",
    "NEEDS_WORK",
    "REVIEWED",
    "REVIEWER_APPROVED",
    "REVIEWER_NEEDS_WORK",
}


def compact_current_pull_requests(page: Mapping[str, Any], branch: str) -> dict[str, Any]:
    pull_requests = []
    for pull_request in _as_list(page.get("values")):
        if not isinstance(pull_request, Mapping):
            continue
        if _ref_matches_branch(pull_request.get("fromRef"), branch):
            pull_requests.append(compact_review_status(pull_request))
    return {
        "branch": _branch_display(branch),
        "count": len(pull_requests),
        "pull_requests": pull_requests,
        "page": _page_info(page),
    }


def compact_review_status(pull_request: Mapping[str, Any]) -> dict[str, Any]:
    reviewers = [_compact_participant(reviewer) for reviewer in _as_list(pull_request.get("reviewers"))]
    participants = [_compact_participant(participant) for participant in _as_list(pull_request.get("participants"))]
    return _clean_dict(
        {
            "id": pull_request.get("id"),
            "version": pull_request.get("version"),
            "state": pull_request.get("state"),
            "open": pull_request.get("open"),
            "title": pull_request.get("title"),
            "author": _compact_participant(pull_request.get("author")),
            "from": _compact_ref(pull_request.get("fromRef")),
            "to": _compact_ref(pull_request.get("toRef")),
            "reviewers": reviewers,
            "participants": participants,
        }
    )


def compact_review_context(
    pull_request: Mapping[str, Any],
    changes: Mapping[str, Any],
    diff: Mapping[str, Any],
    *,
    max_diff_lines: int = 200,
    path: Optional[str] = None,
    diff_format: str = "unified",
) -> dict[str, Any]:
    if max_diff_lines < 1:
        raise ValueError("max_diff_lines must be positive.")
    changed_files = [_compact_change(change) for change in _as_list(changes.get("values")) if isinstance(change, Mapping)]
    if diff_format == "structured":
        if "body" in diff:
            raise ValueError("Structured diff format requires a JSON diff response.")
        diff_summary = _compact_diff(diff, max_diff_lines=max_diff_lines, path=path)
        diff_value: Any = diff_summary
    elif diff_format == "unified":
        diff_summary = _compact_unified_diff(diff, max_diff_lines=max_diff_lines, path=path)
        diff_value = diff_summary.get("text", "")
    else:
        raise ValueError("diff_format must be unified or structured.")
    return {
        "pull_request": compact_review_status(pull_request),
        "counts": {
            "changed_files": len(changed_files),
            "diff_files": len(diff_summary.get("files", [])),
            "diff_lines": diff_summary.get("line_count", 0),
            "diff_truncated": diff_summary.get("truncated", False),
        },
        "changed_files": changed_files,
        "diff_format": diff_format,
        "diff": diff_value,
        "page": {"changes": _page_info(changes)},
    }


def compact_review_comments(activities: Mapping[str, Any], *, state: Optional[str] = None) -> dict[str, Any]:
    comments = []
    wanted_state = state.upper() if state else None
    for activity in _as_list(activities.get("values")):
        if not isinstance(activity, Mapping):
            continue
        comment = activity.get("comment")
        if not isinstance(comment, Mapping):
            continue
        compact = _compact_comment(comment, anchor=activity.get("commentAnchor"))
        if wanted_state and compact.get("state") != wanted_state:
            continue
        comments.append(compact)
    return {"comments": comments, "count": len(comments), "page": _page_info(activities)}


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


def compact_review_summary(
    pull_request: Mapping[str, Any],
    activities: Mapping[str, Any],
    blocker_comments: Mapping[str, Any],
    *,
    state: Optional[str] = None,
) -> dict[str, Any]:
    status = compact_review_status(pull_request)
    comments = compact_review_comments(activities, state=state)["comments"]
    blockers = _compact_blockers(blocker_comments, state=state)
    review_events = _compact_review_events(activities)
    reviewers = status.get("reviewers", [])
    return {
        "pull_request": status,
        "counts": {
            "comments": len(comments),
            "open_comments": _count_state(comments, "OPEN"),
            "open_comments_with_replies": _count_open_with_replies(comments),
            "open_comments_without_replies": _count_open_without_replies(comments),
            "blockers": len(blockers),
            "open_blockers": _count_state(blockers, "OPEN"),
            "review_events": len(review_events),
            "reviewers": len(reviewers),
            "approved_reviewers": _count_approved(reviewers),
            "needs_work_reviewers": _count_status(reviewers, "NEEDS_WORK"),
        },
        "comments": comments,
        "blockers": blockers,
        "review_events": review_events,
        "page": {
            "activities": _page_info(activities),
            "blockers": _page_info(blocker_comments),
        },
    }


def _compact_blockers(page: Mapping[str, Any], *, state: Optional[str] = None) -> list[dict[str, Any]]:
    blockers = []
    wanted_state = state.upper() if state else None
    for blocker in _as_list(page.get("values")):
        if not isinstance(blocker, Mapping):
            continue
        compact = _compact_comment(blocker, anchor=blocker.get("anchor") or blocker.get("commentAnchor"))
        if wanted_state and compact.get("state") != wanted_state:
            continue
        blockers.append(compact)
    return blockers


def _compact_change(change: Mapping[str, Any]) -> dict[str, Any]:
    return _clean_dict(
        {
            "path": _path_text(change.get("path")),
            "src_path": _path_text(change.get("srcPath")),
            "type": change.get("type"),
            "node_type": change.get("nodeType"),
        }
    )


def _compact_diff(diff: Mapping[str, Any], *, max_diff_lines: int, path: Optional[str] = None) -> dict[str, Any]:
    if "body" in diff:
        return _compact_text_diff(diff.get("body"), max_diff_lines=max_diff_lines)

    files = []
    line_count = 0
    truncated = False
    for file_diff in _as_list(diff.get("diffs")):
        if not isinstance(file_diff, Mapping):
            continue
        file_entry: dict[str, Any] = _clean_dict(
            {
                "path": _path_text(file_diff.get("destination")) or _path_text(file_diff.get("source")),
                "src_path": _path_text(file_diff.get("source")),
            }
        )
        if path and file_entry.get("path") != path and file_entry.get("src_path") != path:
            continue
        hunks = []
        for hunk in _as_list(file_diff.get("hunks")):
            if not isinstance(hunk, Mapping):
                continue
            hunk_entry: dict[str, Any] = _clean_dict(
                {
                    "source_line": hunk.get("sourceLine"),
                    "destination_line": hunk.get("destinationLine"),
                }
            )
            segments = []
            for segment in _as_list(hunk.get("segments")):
                if not isinstance(segment, Mapping):
                    continue
                lines = []
                for line in _as_list(segment.get("lines")):
                    if not isinstance(line, Mapping):
                        continue
                    if line_count >= max_diff_lines:
                        truncated = True
                        break
                    lines.append(_compact_diff_line(line))
                    line_count += 1
                if lines:
                    segments.append(_clean_dict({"type": segment.get("type"), "lines": lines}))
                if truncated:
                    break
            if segments:
                hunk_entry["segments"] = segments
                hunks.append(hunk_entry)
            if truncated:
                break
        if hunks:
            file_entry["hunks"] = hunks
            files.append(file_entry)
        if truncated:
            break
    return {"files": files, "line_count": line_count, "truncated": truncated}


def _compact_unified_diff(diff: Mapping[str, Any], *, max_diff_lines: int, path: Optional[str] = None) -> dict[str, Any]:
    if "body" in diff:
        return _compact_unified_text_diff(diff.get("body"), max_diff_lines=max_diff_lines, path=path)

    output_lines = []
    files = []
    line_count = 0
    truncated = False

    for file_diff in _as_list(diff.get("diffs")):
        if not isinstance(file_diff, Mapping):
            continue
        destination = _path_text(file_diff.get("destination")) or _path_text(file_diff.get("source"))
        source = _path_text(file_diff.get("source")) or destination
        if path and destination != path and source != path:
            continue

        file_lines: list[str] = []
        for hunk in _as_list(file_diff.get("hunks")):
            if not isinstance(hunk, Mapping):
                continue
            hunk_lines: list[str] = []
            source_count = 0
            destination_count = 0
            for segment in _as_list(hunk.get("segments")):
                if not isinstance(segment, Mapping):
                    continue
                prefix = _unified_prefix(segment.get("type"))
                for line in _as_list(segment.get("lines")):
                    if not isinstance(line, Mapping):
                        continue
                    if line_count >= max_diff_lines:
                        truncated = True
                        break
                    text = _string_or_none(line.get("line") if "line" in line else line.get("text")) or ""
                    hunk_lines.append(prefix + text)
                    line_count += 1
                    if prefix != "+":
                        source_count += 1
                    if prefix != "-":
                        destination_count += 1
                if truncated:
                    break
            if hunk_lines:
                source_line = _line_number(hunk.get("sourceLine"))
                destination_line = _line_number(hunk.get("destinationLine"))
                file_lines.append(
                    f"@@ -{_unified_range(source_line, source_count)} +{_unified_range(destination_line, destination_count)} @@"
                )
                file_lines.extend(hunk_lines)
            if truncated:
                break

        if file_lines:
            files.append(_clean_dict({"path": destination, "src_path": source}))
            output_lines.append(f"diff --git a/{source} b/{destination}")
            output_lines.append(f"--- a/{source}")
            output_lines.append(f"+++ b/{destination}")
            output_lines.extend(file_lines)
        if truncated:
            break

    return {
        "files": files,
        "text": "\n".join(output_lines),
        "line_count": line_count,
        "truncated": truncated,
    }


def _compact_text_diff(body: Any, *, max_diff_lines: int) -> dict[str, Any]:
    text = _string_or_none(body) or ""
    lines = text.splitlines()
    selected = lines[:max_diff_lines]
    return {
        "files": [],
        "text": "\n".join(selected),
        "line_count": len(selected),
        "truncated": len(lines) > len(selected),
    }


def _compact_unified_text_diff(body: Any, *, max_diff_lines: int, path: Optional[str] = None) -> dict[str, Any]:
    text = _string_or_none(body) or ""
    lines = text.splitlines()
    filtered = _filter_unified_text_lines(lines, path)
    selected, line_count, truncated = _cap_unified_text_lines(filtered, max_diff_lines)
    return {
        "files": _files_from_unified_lines(selected),
        "text": "\n".join(selected),
        "line_count": line_count,
        "truncated": truncated,
    }


def _compact_diff_line(line: Mapping[str, Any]) -> dict[str, Any]:
    return _clean_dict(
        {
            "source": line.get("source"),
            "destination": line.get("destination"),
            "text": line.get("line") if "line" in line else line.get("text"),
        }
    )


def _unified_prefix(segment_type: Any) -> str:
    value = _string_or_none(segment_type) or ""
    normalized = value.upper()
    if normalized in {"ADDED", "ADD"}:
        return "+"
    if normalized in {"REMOVED", "DELETED", "DELETE"}:
        return "-"
    return " "


def _line_number(value: Any) -> int:
    if isinstance(value, int):
        return value
    return 0


def _unified_range(start: int, count: int) -> str:
    if count <= 1:
        return str(start)
    return f"{start},{count}"


def _files_from_unified_lines(lines: list[str]) -> list[dict[str, Any]]:
    files = []
    for line in lines:
        if not line.startswith("diff --git "):
            continue
        paths = _parse_diff_git_paths(line)
        if paths:
            source, destination = paths
            files.append(_clean_dict({"path": destination, "src_path": source}))
    return files


def _parse_diff_git_paths(line: str) -> Optional[tuple[str, str]]:
    prefix = "diff --git a/"
    separator = " b/"
    if not line.startswith(prefix):
        return None
    rest = line[len(prefix) :]
    index = rest.find(separator)
    if index < 0:
        return None
    return rest[:index], rest[index + len(separator) :]


def _filter_unified_text_lines(lines: list[str], path: Optional[str]) -> list[str]:
    if not path:
        return lines
    sections = _unified_text_sections(lines)
    if not sections:
        return []
    selected = []
    for section in sections:
        files = _files_from_unified_lines(section)
        if any(file.get("path") == path or file.get("src_path") == path for file in files):
            selected.extend(section)
    return selected


def _cap_unified_text_lines(lines: list[str], max_diff_lines: int) -> tuple[list[str], int, bool]:
    selected = []
    content_count = 0
    truncated = False
    for line in lines:
        if _is_unified_content_line(line):
            if content_count >= max_diff_lines:
                truncated = True
                break
            content_count += 1
        selected.append(line)
    return selected, content_count, truncated


def _is_unified_content_line(line: str) -> bool:
    if line.startswith("+++") or line.startswith("---"):
        return False
    return line.startswith("+") or line.startswith("-") or line.startswith(" ")


def _unified_text_sections(lines: list[str]) -> list[list[str]]:
    sections = []
    current: list[str] = []
    for line in lines:
        if line.startswith("diff --git "):
            if current:
                sections.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        sections.append(current)
    return sections


def _compact_review_events(activities: Mapping[str, Any]) -> list[dict[str, Any]]:
    events = []
    for activity in _as_list(activities.get("values")):
        if not isinstance(activity, Mapping):
            continue
        action = activity.get("action")
        if action not in _REVIEW_ACTIONS:
            continue
        comment = activity.get("comment")
        event = _clean_dict(
            {
                "action": action,
                "author": _identity(activity.get("user")),
                "created": _time(activity.get("createdDate")),
                "comment_id": comment.get("id") if isinstance(comment, Mapping) else None,
                "text": comment.get("text") if isinstance(comment, Mapping) else None,
            }
        )
        events.append(event)
    return events


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


def _latest_reply(replies: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not replies:
        return None
    return max(replies, key=lambda reply: reply.get("created") or "")


def _compact_reply(reply: Mapping[str, Any]) -> dict[str, Any]:
    return _clean_dict(
        {
            "id": reply.get("id"),
            "version": reply.get("version"),
            "state": reply.get("state"),
            "author": _identity(reply.get("author")),
            "created": _time(reply.get("createdDate")),
            "updated": _time(reply.get("updatedDate")),
            "text": reply.get("text"),
        }
    )


def _compact_participant(participant: Any) -> dict[str, Any]:
    if not isinstance(participant, Mapping):
        return {}
    return _clean_dict(
        {
            "user": _identity(participant.get("user")),
            "role": participant.get("role"),
            "status": participant.get("status"),
            "approved": participant.get("approved"),
            "last_reviewed_commit": participant.get("lastReviewedCommit"),
        }
    )


def _compact_ref(ref: Any) -> dict[str, Any]:
    if not isinstance(ref, Mapping):
        return {}
    return _clean_dict(
        {
            "id": ref.get("id"),
            "display": ref.get("displayId"),
            "commit": ref.get("latestCommit"),
        }
    )


def _ref_matches_branch(ref: Any, branch: str) -> bool:
    if not isinstance(ref, Mapping):
        return False
    display = _branch_display(branch)
    ref_id = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
    return ref.get("displayId") == display or ref.get("id") == ref_id


def _branch_display(branch: str) -> str:
    if branch.startswith("refs/heads/"):
        return branch[len("refs/heads/") :]
    return branch


def _path_text(value: Any) -> Optional[str]:
    if isinstance(value, Mapping):
        for key in ("toString", "displayId", "path", "name"):
            text = _string_or_none(value.get(key))
            if text:
                return text
        components = value.get("components")
        if isinstance(components, list) and components:
            return "/".join(str(component) for component in components)
        return None
    return _string_or_none(value)


def _identity(user: Any) -> Optional[str]:
    if not isinstance(user, Mapping):
        return None
    display = _string_or_none(user.get("displayName"))
    name = _string_or_none(user.get("name") or user.get("slug"))
    if display and name and display != name:
        return f"{display}<{name}>"
    return display or name or _string_or_none(user.get("emailAddress"))


def _time(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        return _string_or_none(value)
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _page_info(page: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "start": page.get("start"),
        "limit": page.get("limit"),
        "next": page.get("nextPageStart"),
    }
    if "isLastPage" in page:
        result["last"] = page.get("isLastPage")
    return _clean_dict(result)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _count_state(items: list[dict[str, Any]], state: str) -> int:
    return sum(1 for item in items if item.get("state") == state)


def _count_open_with_replies(comments: list[dict[str, Any]]) -> int:
    return sum(1 for comment in comments if comment.get("state") == "OPEN" and comment.get("has_replies") is True)


def _count_open_without_replies(comments: list[dict[str, Any]]) -> int:
    return sum(1 for comment in comments if comment.get("state") == "OPEN" and comment.get("has_replies") is not True)


def _count_status(items: list[dict[str, Any]], status: str) -> int:
    return sum(1 for item in items if item.get("status") == status)


def _count_approved(items: list[dict[str, Any]]) -> int:
    return sum(1 for item in items if item.get("approved") is True or item.get("status") == "APPROVED")


def _string_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _clean_dict(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if value is not None and value != [] and value != {}
    }
