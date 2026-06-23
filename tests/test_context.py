import pytest

from btbkt.context import ConfigError, GitInfo, parse_remote_url, resolve_context


def test_parse_remote_url_supports_common_bitbucket_data_center_urls():
    assert parse_remote_url("https://bb.example.com/scm/ABC/demo-repo.git") == (
        "https://bb.example.com",
        "ABC",
        "demo-repo",
    )
    assert parse_remote_url("https://bb.example.com/bitbucket/scm/ABC/demo-repo.git") == (
        "https://bb.example.com/bitbucket",
        "ABC",
        "demo-repo",
    )
    assert parse_remote_url("https://bb.example.com/bitbucket/projects/ABC/repos/demo-repo") == (
        "https://bb.example.com/bitbucket",
        "ABC",
        "demo-repo",
    )
    assert parse_remote_url("https://alice:secret@bb.example.com/scm/ABC/demo-repo.git") == (
        "https://bb.example.com",
        "ABC",
        "demo-repo",
    )
    assert parse_remote_url("ssh://git@bb.example.com:7999/ABC/demo-repo.git") == (
        None,
        "ABC",
        "demo-repo",
    )
    assert parse_remote_url("git@bb.example.com:ABC/demo-repo.git") == (
        None,
        "ABC",
        "demo-repo",
    )


def test_resolve_context_uses_basic_auth_env_and_git_repo_context():
    env = {
        "BITBUCKET_BASE_URL": "https://bitbucket.internal",
        "BITBUCKET_USERNAME": "alice",
        "BITBUCKET_TOKEN": "token",
    }
    git = GitInfo(
        remote_url="https://ignored.example.com/scm/GIT/repo-from-git.git",
        current_branch="feature/pr-flow",
        default_branch="develop",
    )

    context = resolve_context(env=env, git=git)

    assert context.base_url == "https://bitbucket.internal"
    name, value = context.auth_header
    assert name == "Authorization"
    assert value.startswith("Basic ")
    assert context.project == "GIT"
    assert context.repo == "repo-from-git"
    assert context.source_branch == "feature/pr-flow"
    assert context.target_branch == "develop"


def test_resolve_context_requires_base_url_auth_project_and_repo():
    with pytest.raises(ConfigError) as exc:
        resolve_context(env={}, git=None)

    message = str(exc.value)
    assert "BITBUCKET_BASE_URL" in message
    assert "BITBUCKET_USERNAME" in message
    assert "BITBUCKET_PASSWORD" in message
    assert "project" in message
    assert "repo" in message


def test_resolve_context_accepts_password_instead_of_token():
    env = {
        "BITBUCKET_BASE_URL": "https://bitbucket.internal/",
        "BITBUCKET_USERNAME": "alice",
        "BITBUCKET_PASSWORD": "secret",
    }
    git = GitInfo(remote_url="https://bb.example.com/scm/ABC/demo.git")

    context = resolve_context(env=env, git=git)

    assert context.base_url == "https://bitbucket.internal"
    name, value = context.auth_header
    assert name == "Authorization"
    assert value.startswith("Basic ")
    assert context.project == "ABC"
    assert context.repo == "demo"


def test_resolve_context_does_not_read_project_repo_or_legacy_btbkt_env():
    env = {
        "BTBKT_BASE_URL": "https://legacy.example.com",
        "BTBKT_USERNAME": "legacy",
        "BTBKT_TOKEN": "legacy-token",
        "BITBUCKET_PROJECT": "ENV",
        "BITBUCKET_REPO": "env-repo",
    }

    with pytest.raises(ConfigError) as exc:
        resolve_context(env=env, git=None)

    message = str(exc.value)
    assert "BITBUCKET_BASE_URL" in message
    assert "project" in message
    assert "repo" in message
