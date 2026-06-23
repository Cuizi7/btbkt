import pytest

from btbkt.compact import (
    add_diff_context_to_comments,
    compact_current_pull_requests,
    compact_review_comments,
    compact_review_context,
    compact_review_summary,
)


def test_compact_current_pull_requests_filters_by_source_branch():
    page = {
        "start": 0,
        "limit": 100,
        "isLastPage": True,
        "values": [
            {
                "id": 12,
                "title": "Feature current",
                "state": "OPEN",
                "fromRef": {"id": "refs/heads/feature/current", "displayId": "feature/current"},
                "toRef": {"id": "refs/heads/main", "displayId": "main"},
            },
            {
                "id": 13,
                "title": "Other branch",
                "state": "OPEN",
                "fromRef": {"id": "refs/heads/feature/other", "displayId": "feature/other"},
                "toRef": {"id": "refs/heads/main", "displayId": "main"},
            },
        ],
    }

    assert compact_current_pull_requests(page, "feature/current") == {
        "branch": "feature/current",
        "count": 1,
        "pull_requests": [
            {
                "id": 12,
                "state": "OPEN",
                "title": "Feature current",
                "from": {"id": "refs/heads/feature/current", "display": "feature/current"},
                "to": {"id": "refs/heads/main", "display": "main"},
            }
        ],
        "page": {"start": 0, "limit": 100, "last": True},
    }


def test_compact_review_context_summarizes_changes_and_caps_diff_lines():
    pull_request = {
        "id": 42,
        "title": "Add API",
        "state": "OPEN",
        "fromRef": {"id": "refs/heads/feature/api", "displayId": "feature/api", "latestCommit": "abc123"},
        "toRef": {"id": "refs/heads/main", "displayId": "main", "latestCommit": "def456"},
    }
    changes = {
        "start": 0,
        "limit": 100,
        "isLastPage": True,
        "values": [
            {"path": {"toString": "src/app.py"}, "srcPath": {"toString": "src/old_app.py"}, "type": "MOVE", "nodeType": "FILE"},
            {"path": {"toString": "tests/test_app.py"}, "type": "ADD", "nodeType": "FILE"},
        ],
    }
    diff = {
        "diffs": [
            {
                "destination": {"toString": "src/app.py"},
                "hunks": [
                    {
                        "sourceLine": 10,
                        "destinationLine": 10,
                        "segments": [
                            {
                                "type": "CONTEXT",
                                "lines": [{"source": 10, "destination": 10, "line": "old line"}],
                            },
                            {
                                "type": "ADDED",
                                "lines": [
                                    {"destination": 11, "line": "new line"},
                                    {"destination": 12, "line": "another line"},
                                ],
                            },
                        ],
                    }
                ],
            }
        ]
    }

    assert compact_review_context(pull_request, changes, diff, max_diff_lines=2, diff_format="structured") == {
        "pull_request": {
            "id": 42,
            "state": "OPEN",
            "title": "Add API",
            "from": {"id": "refs/heads/feature/api", "display": "feature/api", "commit": "abc123"},
            "to": {"id": "refs/heads/main", "display": "main", "commit": "def456"},
        },
        "counts": {"changed_files": 2, "diff_files": 1, "diff_lines": 2, "diff_truncated": True},
        "changed_files": [
            {"path": "src/app.py", "src_path": "src/old_app.py", "type": "MOVE", "node_type": "FILE"},
            {"path": "tests/test_app.py", "type": "ADD", "node_type": "FILE"},
        ],
        "diff_format": "structured",
        "diff": {
            "files": [
                {
                    "path": "src/app.py",
                    "hunks": [
                        {
                            "source_line": 10,
                            "destination_line": 10,
                            "segments": [
                                {
                                    "type": "CONTEXT",
                                    "lines": [{"source": 10, "destination": 10, "text": "old line"}],
                                },
                                {
                                    "type": "ADDED",
                                    "lines": [{"destination": 11, "text": "new line"}],
                                },
                            ],
                        }
                    ],
                }
            ],
            "line_count": 2,
            "truncated": True,
        },
        "page": {"changes": {"start": 0, "limit": 100, "last": True}},
    }


def test_compact_review_context_outputs_unified_diff_by_default():
    pull_request = {
        "id": 42,
        "title": "Add API",
        "state": "OPEN",
        "fromRef": {"id": "refs/heads/feature/api", "displayId": "feature/api"},
        "toRef": {"id": "refs/heads/main", "displayId": "main"},
    }
    changes = {"values": [{"path": {"toString": "src/app.py"}, "srcPath": {"toString": "src/old_app.py"}}]}
    diff = {
        "diffs": [
            {
                "source": {"toString": "src/old_app.py"},
                "destination": {"toString": "src/app.py"},
                "hunks": [
                    {
                        "sourceLine": 10,
                        "destinationLine": 10,
                        "segments": [
                            {"type": "CONTEXT", "lines": [{"source": 10, "destination": 10, "line": "old line"}]},
                            {
                                "type": "ADDED",
                                "lines": [
                                    {"destination": 11, "line": "new line"},
                                    {"destination": 12, "line": "another line"},
                                ],
                            },
                        ],
                    }
                ],
            }
        ]
    }

    result = compact_review_context(pull_request, changes, diff)

    assert result["diff_format"] == "unified"
    assert result["counts"] == {
        "changed_files": 1,
        "diff_files": 1,
        "diff_lines": 3,
        "diff_truncated": False,
    }
    assert result["diff"] == (
        "diff --git a/src/old_app.py b/src/app.py\n"
        "--- a/src/old_app.py\n"
        "+++ b/src/app.py\n"
        "@@ -10 +10,3 @@\n"
        " old line\n"
        "+new line\n"
        "+another line"
    )


def test_compact_review_context_filters_diff_path_locally():
    pull_request = {"id": 42, "state": "OPEN"}
    changes = {"values": []}
    diff = {
        "diffs": [
            {
                "destination": {"toString": "README.md"},
                "hunks": [{"segments": [{"type": "ADDED", "lines": [{"destination": 1, "line": "readme"}]}]}],
            },
            {
                "destination": {"toString": "src/app.py"},
                "hunks": [{"segments": [{"type": "ADDED", "lines": [{"destination": 8, "line": "app"}]}]}],
            },
        ]
    }

    result = compact_review_context(
        pull_request,
        changes,
        diff,
        max_diff_lines=10,
        path="src/app.py",
        diff_format="structured",
    )

    assert result["counts"]["diff_files"] == 1
    assert result["diff_format"] == "structured"
    assert result["diff"]["files"][0]["path"] == "src/app.py"
    assert result["diff"]["files"][0]["hunks"][0]["segments"][0]["lines"] == [
        {"destination": 8, "text": "app"}
    ]


def test_compact_review_context_filters_unified_text_body_by_path():
    pull_request = {"id": 42, "state": "OPEN"}
    changes = {"values": []}
    diff = {
        "body": (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1 @@\n"
            "+readme\n"
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -8 +8 @@\n"
            "+app\n"
        )
    }

    result = compact_review_context(pull_request, changes, diff, path="src/app.py")

    assert result["diff_format"] == "unified"
    assert result["counts"]["diff_files"] == 1
    assert "diff --git a/src/app.py b/src/app.py" in result["diff"]
    assert "+app" in result["diff"]
    assert "README.md" not in result["diff"]


def test_compact_review_context_caps_unified_text_body_by_content_lines():
    diff = {
        "body": (
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -8 +8,2 @@\n"
            "+first line\n"
            "+second line\n"
        )
    }

    result = compact_review_context({"id": 42}, {"values": []}, diff, max_diff_lines=1)

    assert result["counts"]["diff_lines"] == 1
    assert result["counts"]["diff_truncated"] is True
    assert "diff --git a/src/app.py b/src/app.py" in result["diff"]
    assert "@@ -8 +8,2 @@" in result["diff"]
    assert "+first line" in result["diff"]
    assert "+second line" not in result["diff"]


def test_compact_review_context_filters_unified_text_body_path_with_spaces():
    diff = {
        "body": (
            "diff --git a/src/file with spaces.py b/src/file with spaces.py\n"
            "--- a/src/file with spaces.py\n"
            "+++ b/src/file with spaces.py\n"
            "@@ -1 +1 @@\n"
            "+app\n"
        )
    }

    result = compact_review_context({"id": 42}, {"values": []}, diff, path="src/file with spaces.py")

    assert result["counts"]["diff_files"] == 1
    assert "diff --git a/src/file with spaces.py b/src/file with spaces.py" in result["diff"]
    assert "+app" in result["diff"]


def test_compact_review_context_rejects_structured_format_for_raw_text_diff():
    with pytest.raises(ValueError, match="Structured diff format"):
        compact_review_context(
            {"id": 42},
            {"values": []},
            {"body": "diff --git a/src/app.py b/src/app.py\n+app\n"},
            diff_format="structured",
        )


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


def test_add_diff_context_to_comments_marks_missing_anchor():
    comments = {"comments": [{"id": 15466, "text": "提高优先级"}], "count": 1}

    result = add_diff_context_to_comments(comments, {}, radius=1)

    assert result["comments"][0]["diff_context_unavailable"] == "missing_anchor"


def test_add_diff_context_to_comments_supports_unified_text_diff_body():
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
            "body": (
                "diff --git a/src/app.py b/src/app.py\n"
                "--- a/src/app.py\n"
                "+++ b/src/app.py\n"
                "@@ -7,2 +7,3 @@\n"
                " before\n"
                "+target\n"
                " after\n"
            )
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
            {"type": "CONTEXT", "source": 8, "destination": 9, "text": "after"},
        ],
    }


def test_add_diff_context_to_comments_uses_file_type_from_for_context_anchor():
    activities = {
        "values": [
            {
                "comment": {
                    "id": 15466,
                    "text": "source-side context",
                    "state": "OPEN",
                    "author": {"name": "reviewer"},
                },
                "commentAnchor": {
                    "path": "src/app.py",
                    "line": 20,
                    "lineType": "CONTEXT",
                    "fileType": "FROM",
                },
            }
        ]
    }
    comments = compact_review_comments(activities)
    diff_by_path = {
        "src/app.py": {
            "diffs": [
                {
                    "source": {"toString": "src/app.py"},
                    "destination": {"toString": "src/app.py"},
                    "hunks": [
                        {
                            "segments": [
                                {
                                    "type": "CONTEXT",
                                    "lines": [
                                        {"source": 20, "destination": 30, "line": "source-side target"},
                                        {"source": 21, "destination": 20, "line": "destination collision"},
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    }

    result = add_diff_context_to_comments(comments, diff_by_path, radius=0)

    comment = result["comments"][0]
    assert comment["file_type"] == "FROM"
    assert comment["diff_context"] == {
        "path": "src/app.py",
        "line": 20,
        "line_type": "CONTEXT",
        "file_type": "FROM",
        "radius": 0,
        "truncated_before": False,
        "truncated_after": True,
        "lines": [{"type": "CONTEXT", "source": 20, "destination": 30, "text": "source-side target"}],
    }


def test_add_diff_context_to_comments_does_not_cross_hunk_boundaries():
    comments = {
        "comments": [
            {
                "id": 15466,
                "path": "src/app.py",
                "line": 100,
                "line_type": "ADDED",
                "text": "target hunk",
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
                                {"type": "ADDED", "lines": [{"destination": 10, "line": "unrelated hunk"}]}
                            ]
                        },
                        {
                            "segments": [
                                {
                                    "type": "ADDED",
                                    "lines": [
                                        {"destination": 100, "line": "target"},
                                        {"destination": 101, "line": "same hunk after"},
                                    ],
                                }
                            ]
                        },
                    ],
                }
            ]
        }
    }

    result = add_diff_context_to_comments(comments, diff_by_path, radius=1)

    assert result["comments"][0]["diff_context"] == {
        "path": "src/app.py",
        "line": 100,
        "line_type": "ADDED",
        "radius": 1,
        "truncated_before": True,
        "truncated_after": False,
        "lines": [
            {"type": "ADDED", "destination": 100, "text": "target"},
            {"type": "ADDED", "destination": 101, "text": "same hunk after"},
        ],
    }


def test_compact_review_comments_extracts_comment_activities_without_diff_noise():
    activities = {
        "start": 0,
        "limit": 2,
        "isLastPage": False,
        "nextPageStart": 2,
        "values": [
            {
                "action": "COMMENTED",
                "createdDate": 0,
                "user": {"name": "alice", "displayName": "Alice A"},
                "comment": {
                    "id": 15450,
                    "version": 0,
                    "text": "资金账号",
                    "state": "OPEN",
                    "severity": "NORMAL",
                    "author": {"name": "alice", "displayName": "Alice A"},
                    "createdDate": 0,
                    "comments": [
                        {
                            "id": 15460,
                            "text": "已补充说明",
                            "state": "OPEN",
                            "author": {"name": "bob"},
                            "createdDate": 1000,
                        }
                    ],
                },
                "commentAnchor": {
                    "path": "trading_script_collection/real_trading/real_trading.py",
                    "line": 56,
                    "lineType": "ADDED",
                    "fileType": "TO",
                },
                "diff": {"this": "must not be copied"},
            },
            {
                "action": "RESCOPED",
                "createdDate": 1000,
                "diff": {"too": "large"},
            },
        ],
    }

    assert compact_review_comments(activities) == {
        "comments": [
            {
                "id": 15450,
                "version": 0,
                "state": "OPEN",
                "severity": "NORMAL",
                "author": "Alice A<alice>",
                "created": "1970-01-01T00:00:00Z",
                "path": "trading_script_collection/real_trading/real_trading.py",
                "line": 56,
                "line_type": "ADDED",
                "file_type": "TO",
                "text": "资金账号",
                "replies": [
                    {
                        "id": 15460,
                        "state": "OPEN",
                        "author": "bob",
                        "created": "1970-01-01T00:00:01Z",
                        "text": "已补充说明",
                    }
                ],
                "reply_count": 1,
                "has_replies": True,
                "latest_reply_author": "bob",
                "latest_reply_created": "1970-01-01T00:00:01Z",
            }
        ],
        "count": 1,
        "page": {"start": 0, "limit": 2, "next": 2, "last": False},
    }


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


def test_compact_review_summary_combines_pr_status_comments_and_blockers():
    pull_request = {
        "id": 390,
        "version": 12,
        "title": "Codex/account config schema",
        "state": "OPEN",
        "open": True,
        "fromRef": {
            "id": "refs/heads/codex/account-config-schema",
            "displayId": "codex/account-config-schema",
            "latestCommit": "abc123",
        },
        "toRef": {
            "id": "refs/heads/master",
            "displayId": "master",
            "latestCommit": "def456",
        },
        "author": {
            "user": {"name": "cziqi", "displayName": "崔子琦"},
            "status": "UNAPPROVED",
            "approved": False,
        },
        "reviewers": [
            {
                "user": {"name": "reviewer", "displayName": "Reviewer"},
                "role": "REVIEWER",
                "status": "NEEDS_WORK",
                "approved": False,
                "lastReviewedCommit": "abc123",
            }
        ],
        "participants": [
            {
                "user": {"name": "ci"},
                "role": "PARTICIPANT",
                "status": "APPROVED",
                "approved": True,
            }
        ],
    }
    activities = {
        "start": 0,
        "limit": 100,
        "isLastPage": True,
        "values": [
            {
                "action": "COMMENTED",
                "createdDate": 0,
                "comment": {
                    "id": 15450,
                    "text": "资金账号",
                    "state": "OPEN",
                    "severity": "NORMAL",
                    "author": {"name": "reviewer"},
                },
                "commentAnchor": {"path": "src/config.py", "line": 12, "lineType": "ADDED"},
            },
            {
                "action": "APPROVED",
                "createdDate": 1000,
                "user": {"name": "ci"},
            },
        ],
    }
    blocker_comments = {
        "start": 0,
        "limit": 100,
        "isLastPage": True,
        "values": [
            {
                "id": 99,
                "text": "Add a regression test.",
                "state": "OPEN",
                "severity": "BLOCKER",
                "author": {"name": "reviewer"},
                "anchor": {"path": "tests/test_config.py", "line": 3, "lineType": "ADDED"},
            }
        ],
    }

    assert compact_review_summary(pull_request, activities, blocker_comments) == {
        "pull_request": {
            "id": 390,
            "version": 12,
            "state": "OPEN",
            "open": True,
            "title": "Codex/account config schema",
            "author": {"user": "崔子琦<cziqi>", "status": "UNAPPROVED", "approved": False},
            "from": {"id": "refs/heads/codex/account-config-schema", "display": "codex/account-config-schema", "commit": "abc123"},
            "to": {"id": "refs/heads/master", "display": "master", "commit": "def456"},
            "reviewers": [
                {
                    "user": "Reviewer<reviewer>",
                    "role": "REVIEWER",
                    "status": "NEEDS_WORK",
                    "approved": False,
                    "last_reviewed_commit": "abc123",
                }
            ],
            "participants": [
                {"user": "ci", "role": "PARTICIPANT", "status": "APPROVED", "approved": True}
            ],
        },
        "counts": {
            "comments": 1,
            "open_comments": 1,
            "open_comments_with_replies": 0,
            "open_comments_without_replies": 1,
            "blockers": 1,
            "open_blockers": 1,
            "review_events": 1,
            "reviewers": 1,
            "approved_reviewers": 0,
            "needs_work_reviewers": 1,
        },
        "comments": [
            {
                "id": 15450,
                "state": "OPEN",
                "severity": "NORMAL",
                "author": "reviewer",
                "path": "src/config.py",
                "line": 12,
                "line_type": "ADDED",
                "text": "资金账号",
                "reply_count": 0,
                "has_replies": False,
            }
        ],
        "blockers": [
            {
                "id": 99,
                "state": "OPEN",
                "severity": "BLOCKER",
                "author": "reviewer",
                "path": "tests/test_config.py",
                "line": 3,
                "line_type": "ADDED",
                "text": "Add a regression test.",
                "reply_count": 0,
                "has_replies": False,
            }
        ],
        "review_events": [
            {"action": "APPROVED", "author": "ci", "created": "1970-01-01T00:00:01Z"}
        ],
        "page": {
            "activities": {"start": 0, "limit": 100, "last": True},
            "blockers": {"start": 0, "limit": 100, "last": True},
        },
    }


def test_compact_review_summary_filters_comments_and_blockers_by_state():
    pull_request = {"id": 390, "state": "OPEN", "reviewers": []}
    activities = {
        "values": [
            {
                "comment": {
                    "id": 1,
                    "text": "Open comment.",
                    "state": "OPEN",
                    "author": {"name": "reviewer"},
                }
            },
            {
                "comment": {
                    "id": 2,
                    "text": "Resolved comment.",
                    "state": "RESOLVED",
                    "author": {"name": "reviewer"},
                }
            },
        ],
    }
    blocker_comments = {
        "values": [
            {
                "id": 3,
                "text": "Open blocker.",
                "state": "OPEN",
                "severity": "BLOCKER",
                "author": {"name": "reviewer"},
            },
            {
                "id": 4,
                "text": "Resolved blocker.",
                "state": "RESOLVED",
                "severity": "BLOCKER",
                "author": {"name": "reviewer"},
            },
        ],
    }

    result = compact_review_summary(pull_request, activities, blocker_comments, state="resolved")

    assert result["counts"]["comments"] == 1
    assert result["counts"]["blockers"] == 1
    assert result["comments"][0]["id"] == 2
    assert result["blockers"][0]["id"] == 4


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
