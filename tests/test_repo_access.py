import subprocess
from pathlib import Path

import pytest

from btbkt.repo_access import (
    GitAuth,
    GitOperationError,
    GitRunner,
    RefRequest,
    RepositoryAccess,
)


class FakeClient:
    def __init__(
        self,
        clone_links,
        *,
        default_branch="master",
        pr_commit="c" * 40,
        pr_source_links=None,
        pr_source_identity=None,
        source_lookup_links=None,
    ):
        self.clone_links = clone_links
        self.default_branch = default_branch
        self.pr_commit = pr_commit
        self.pr_source_links = pr_source_links
        self.pr_source_identity = pr_source_identity
        self.source_lookup_links = source_lookup_links
        self.default_branch_calls = 0
        self.pr_calls = []

    def get_repository(self, *, project=None, repo=None):
        if project is not None or repo is not None:
            return {"links": {"clone": self.source_lookup_links or []}}
        return {"links": {"clone": self.clone_links}}

    def get_default_branch(self):
        self.default_branch_calls += 1
        return {"id": f"refs/heads/{self.default_branch}", "displayId": self.default_branch}

    def get_pull_request(self, pr_id):
        self.pr_calls.append(pr_id)
        from_ref = {
                "id": "refs/heads/feature",
                "displayId": "feature",
                "latestCommit": self.pr_commit,
        }
        if self.pr_source_links is not None:
            from_ref["repository"] = {"links": {"clone": self.pr_source_links}}
        elif self.pr_source_identity is not None:
            project, slug = self.pr_source_identity
            from_ref["repository"] = {"project": {"key": project}, "slug": slug}
        return {"fromRef": from_ref}


class StatefulGitRunner:
    def __init__(
        self,
        *,
        remote_url=None,
        branch="master",
        head="a" * 40,
        tracking_commit=None,
        fetch_commit=None,
        dirty=False,
        detached=False,
        can_fast_forward=True,
        local_ahead=False,
        ls_remote_refs=None,
        missing_ref_before_fetch=False,
        checkout_root=None,
        effective_remote_urls=None,
    ):
        self.remote_url = remote_url
        self.branch = branch
        self.head = head
        self.tracking_commit = tracking_commit or head
        self.fetch_commit = fetch_commit or self.tracking_commit
        self.dirty = dirty
        self.detached = detached
        self.can_fast_forward = can_fast_forward
        self.local_ahead = local_ahead
        self.ls_remote_refs = None if ls_remote_refs is None else tuple(ls_remote_refs)
        self.local_branches = {branch}
        self.missing_ref_before_fetch = missing_ref_before_fetch
        self.fetched = False
        self.checkout_root = checkout_root
        self.effective_remote_urls = effective_remote_urls
        self.calls = []

    def run(
        self,
        args,
        *,
        cwd=None,
        auth=None,
        acceptable_returncodes=(0,),
        safe_url=None,
    ):
        args = list(args)
        self.calls.append({"args": args, "cwd": cwd, "auth": auth, "safe_url": safe_url})
        returncode = 0
        stdout = ""

        if args[0] == "clone":
            self.remote_url = args[-2]
            Path(args[-1]).mkdir(parents=True, exist_ok=True)
        elif args[:3] == ["show-ref", "--verify", "--quiet"]:
            branch_name = args[3].removeprefix("refs/heads/")
            returncode = 0 if branch_name in self.local_branches else 1
        elif args[:3] == ["rev-parse", "--is-inside-work-tree"]:
            stdout = "true\n"
        elif args[:2] == ["rev-parse", "--show-toplevel"]:
            stdout = str(self.checkout_root or cwd) + "\n"
        elif args[:3] == ["config", "--get", "remote.origin.url"]:
            if self.remote_url is None:
                returncode = 1
            else:
                stdout = self.remote_url + "\n"
        elif args[:4] == ["remote", "get-url", "--all", "origin"]:
            urls = self.effective_remote_urls
            if urls is None:
                urls = (self.remote_url,) if self.remote_url else ()
            stdout = "".join(f"{url}\n" for url in urls)
            returncode = 0 if urls else 2
        elif args[:3] == ["remote", "set-url", "origin"]:
            self.remote_url = args[3]
        elif args[0] == "check-ref-format":
            pass
        elif args[0] == "fetch":
            self.tracking_commit = self.fetch_commit
            self.fetched = True
        elif args[:2] == ["rev-parse", "--verify"]:
            ref = args[-1]
            if self.missing_ref_before_fetch and not self.fetched:
                returncode = 1
            elif ref.startswith("refs/remotes/origin/") or ref.startswith("refs/tags/"):
                stdout = self.tracking_commit + "\n"
            elif ref.startswith("FETCH_HEAD"):
                stdout = self.fetch_commit + "\n"
            elif ref.startswith("HEAD"):
                stdout = self.head + "\n"
            else:
                stdout = self.fetch_commit + "\n"
        elif args[:3] == ["symbolic-ref", "--quiet", "--short"]:
            if self.detached:
                returncode = 1
            else:
                stdout = self.branch + "\n"
        elif args[:2] == ["status", "--porcelain"]:
            stdout = " M local.txt\n" if self.dirty else ""
        elif args[:2] == ["merge-base", "--is-ancestor"]:
            if args[2].startswith("HEAD"):
                returncode = 0 if self.can_fast_forward else 1
            else:
                returncode = 0 if self.local_ahead else 1
        elif args[:2] == ["merge", "--ff-only"]:
            self.head = self.tracking_commit
        elif args[:2] == ["checkout", "--detach"]:
            self.detached = True
            self.head = args[2]
        elif args[:2] == ["checkout", "-b"]:
            if args[2] in self.local_branches:
                raise GitOperationError(f"branch {args[2]} already exists")
            self.local_branches.add(args[2])
            self.branch = args[2]
            self.detached = False
            self.head = self.tracking_commit
        elif args[0] == "checkout":
            self.branch = args[1]
            self.detached = False
            self.head = self.tracking_commit
        elif args[0] == "ls-remote":
            refs = self.ls_remote_refs
            if refs is None:
                refs = tuple(arg for arg in args[2:] if arg.startswith("refs/"))
            stdout = "".join(f"{'e' * 40}\t{ref}\n" for ref in refs)
        else:
            raise AssertionError(f"Unexpected Git command: {args}")

        if returncode not in acceptable_returncodes:
            raise GitOperationError(f"fake Git failure: {args[0]}")
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


HTTPS_LINK = {"name": "http", "href": "https://bitbucket.example/scm/TRAD/trading.git"}
HTTP_LINK = {"name": "http", "href": "http://bitbucket.example/scm/TRAD/trading.git"}
SSH_LINK = {"name": "ssh", "href": "ssh://git@bitbucket.example:7999/TRAD/trading.git"}


def make_access(client, runner, secret="access-token"):
    return RepositoryAccess(
        client=client,
        project="TRAD",
        repo="trading",
        auth=GitAuth(username="alice", secret=secret),
        runner=runner,
    )


def test_git_runner_keeps_secret_out_of_argv_script_and_persistent_config(tmp_path):
    secret = "token-with-sensitive-value"
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["env"] = dict(kwargs["env"])
        askpass = Path(kwargs["env"]["GIT_ASKPASS"])
        captured["askpass"] = askpass
        captured["script"] = askpass.read_text(encoding="utf-8")
        assert askpass.exists()
        return subprocess.CompletedProcess(argv, 0, stdout="abc123\n", stderr="")

    runner = GitRunner(subprocess_run=fake_run, environ={}, temporary_parent=tmp_path)

    result = runner.run(
        ["ls-remote", "https://bitbucket.example/scm/TRAD/trading.git"],
        auth=GitAuth(username="alice", secret=secret),
    )

    assert result.stdout == "abc123\n"
    assert secret not in "\0".join(captured["argv"])
    assert secret not in captured["script"]
    assert captured["env"]["BTBKT_GIT_SECRET"] == secret
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert "credential.helper=" in captured["argv"]
    assert not captured["askpass"].exists()


def test_git_runner_redacts_secret_from_process_failure_and_cleans_askpass(tmp_path):
    secret = "failure-secret"
    captured = {}

    def fake_run(argv, **kwargs):
        captured["askpass"] = Path(kwargs["env"]["GIT_ASKPASS"])
        return subprocess.CompletedProcess(
            argv,
            128,
            stdout=f"stdout {secret}",
            stderr=f"fatal: authentication failed for https://alice:{secret}@bitbucket.example/repo.git",
        )

    runner = GitRunner(subprocess_run=fake_run, environ={}, temporary_parent=tmp_path)

    with pytest.raises(GitOperationError) as error_info:
        runner.run(
            ["fetch", "https://bitbucket.example/scm/TRAD/trading.git"],
            auth=GitAuth(username="alice", secret=secret),
        )

    message = str(error_info.value)
    assert secret not in message
    assert "[REDACTED]" in message
    assert not captured["askpass"].exists()


def test_git_runner_cleans_askpass_after_interruption(tmp_path):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["askpass"] = Path(kwargs["env"]["GIT_ASKPASS"])
        raise KeyboardInterrupt

    runner = GitRunner(subprocess_run=fake_run, environ={}, temporary_parent=tmp_path)

    with pytest.raises(KeyboardInterrupt):
        runner.run(
            ["fetch", "https://bitbucket.example/scm/TRAD/trading.git"],
            auth=GitAuth(username="alice", secret="interrupt-secret"),
        )

    assert not captured["askpass"].exists()


def test_git_runner_redacts_unexpected_process_exception_and_cleans_askpass(tmp_path):
    secret = "unexpected-exception-secret"
    captured = {}

    def fake_run(argv, **kwargs):
        captured["askpass"] = Path(kwargs["env"]["GIT_ASKPASS"])
        raise RuntimeError(f"runner failed with {secret}")

    runner = GitRunner(subprocess_run=fake_run, environ={}, temporary_parent=tmp_path)

    with pytest.raises(GitOperationError) as error_info:
        runner.run(
            ["fetch", HTTPS_LINK["href"]],
            auth=GitAuth(username="alice", secret=secret),
        )

    assert secret not in str(error_info.value)
    assert "[REDACTED]" in str(error_info.value)
    assert not captured["askpass"].exists()


def test_git_runner_removes_bitbucket_secrets_from_non_http_child_environment():
    captured = {}

    def fake_run(argv, **kwargs):
        captured["env"] = dict(kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runner = GitRunner(
        subprocess_run=fake_run,
        environ={
            "PATH": "/usr/bin",
            "BITBUCKET_BASE_URL": "https://alice:base-url-secret@bitbucket.example",
            "BITBUCKET_USERNAME": "alice",
            "BITBUCKET_TOKEN": "token-must-not-reach-ssh",
            "BITBUCKET_PASSWORD": "password-must-not-reach-hooks",
            "BTBKT_GIT_SECRET": "stale-secret",
        },
    )

    runner.run(["status", "--porcelain"])

    assert captured["env"]["PATH"] == "/usr/bin"
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert "BITBUCKET_BASE_URL" not in captured["env"]
    assert "BITBUCKET_USERNAME" not in captured["env"]
    assert "BITBUCKET_TOKEN" not in captured["env"]
    assert "BITBUCKET_PASSWORD" not in captured["env"]
    assert "BTBKT_GIT_SECRET" not in captured["env"]


def test_git_runner_binds_network_command_to_validated_url_and_disables_hooks():
    captured = {}
    safe_url = HTTPS_LINK["href"]

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    GitRunner(subprocess_run=fake_run, environ={}).run(
        ["fetch", safe_url, "refs/heads/master"],
        safe_url=safe_url,
    )

    joined = "\0".join(captured["argv"])
    assert "core.hooksPath=/dev/null" in captured["argv"]
    mapping = next(value for value in captured["argv"] if value.startswith(f"url.{safe_url}.insteadOf="))
    placeholder = mapping.split("insteadOf=", 1)[1]
    assert placeholder.startswith("btbkt-safe-")
    assert placeholder in captured["argv"]
    assert joined.count(safe_url) == 1


def test_clone_uses_server_https_link_without_putting_token_in_remote_or_argv(tmp_path):
    token = "clone-secret"
    runner = StatefulGitRunner(fetch_commit="b" * 40)
    access = make_access(FakeClient([SSH_LINK, HTTPS_LINK]), runner, secret=token)
    destination = tmp_path / "checkout"

    result = access.clone(destination, RefRequest("branch", "master"))

    payload = result.to_dict()
    assert payload["action"] == "cloned"
    assert payload["resolved_commit"] == "b" * 40
    assert payload["changed"] is True
    assert payload["warning"] is None
    assert token not in runner.remote_url
    assert all(token not in "\0".join(call["args"]) for call in runner.calls)
    clone_call = next(call for call in runner.calls if call["args"][0] == "clone")
    assert clone_call["auth"] == GitAuth(username="alice", secret=token)


def test_clone_supports_http_with_plaintext_warning_and_ssh_without_http_auth(tmp_path):
    http_runner = StatefulGitRunner(fetch_commit="b" * 40)
    http_result = make_access(FakeClient([HTTP_LINK]), http_runner).clone(
        tmp_path / "http", RefRequest("branch", "master")
    )
    ssh_runner = StatefulGitRunner(fetch_commit="b" * 40)
    ssh_result = make_access(FakeClient([SSH_LINK]), ssh_runner).clone(
        tmp_path / "ssh", RefRequest("branch", "master")
    )

    assert "plain HTTP" in http_result.warning
    assert next(call for call in http_runner.calls if call["args"][0] == "clone")["auth"] is not None
    assert ssh_result.warning is None
    assert next(call for call in ssh_runner.calls if call["args"][0] == "clone")["auth"] is None


def test_clone_fast_forwards_if_remote_advances_between_clone_and_fetch(tmp_path):
    class AdvancingRemoteRunner(StatefulGitRunner):
        def run(self, args, **kwargs):
            result = super().run(args, **kwargs)
            if args[0] == "checkout":
                self.head = "a" * 40
            return result

    runner = AdvancingRemoteRunner(head="a" * 40, fetch_commit="b" * 40)

    result = make_access(FakeClient([HTTPS_LINK]), runner).clone(
        tmp_path / "checkout", RefRequest("branch", "master")
    )

    assert result.resolved_commit == "b" * 40
    assert result.state["head"] == "b" * 40
    assert any(call["args"][:2] == ["merge", "--ff-only"] for call in runner.calls)


def test_fetch_without_explicit_ref_uses_bitbucket_default_branch(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    client = FakeClient([HTTPS_LINK], default_branch="develop")
    runner = StatefulGitRunner(remote_url=HTTPS_LINK["href"], branch="develop")

    result = make_access(client, runner).fetch(destination)

    assert result.requested_ref == "develop"
    assert result.ref_kind == "branch"
    assert client.default_branch_calls == 1


def test_first_fetch_allows_target_ref_to_be_absent_before_fetch(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    runner = StatefulGitRunner(
        remote_url=HTTPS_LINK["href"],
        fetch_commit="b" * 40,
        missing_ref_before_fetch=True,
    )

    result = make_access(FakeClient([HTTPS_LINK]), runner).fetch(
        destination, RefRequest("branch", "new-branch")
    )

    assert result.action == "fetched"
    assert result.resolved_commit == "b" * 40
    assert result.changed is True
    first_probe = next(
        call for call in runner.calls
        if call["args"][:2] == ["rev-parse", "--verify"]
    )
    assert "--quiet" in first_probe["args"]


def test_untyped_ref_resolves_an_unambiguous_branch(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    runner = StatefulGitRunner(
        remote_url=HTTPS_LINK["href"],
        ls_remote_refs=("refs/heads/master",),
    )

    result = make_access(FakeClient([HTTPS_LINK]), runner).fetch(
        destination, RefRequest("ref", "master")
    )

    assert result.ref_kind == "branch"
    assert result.requested_ref == "master"


def test_untyped_ref_rejects_ambiguous_branch_and_tag(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    runner = StatefulGitRunner(
        remote_url=HTTPS_LINK["href"],
        ls_remote_refs=("refs/heads/release", "refs/tags/release"),
    )

    with pytest.raises(GitOperationError, match="both a branch and tag"):
        make_access(FakeClient([HTTPS_LINK]), runner).fetch(
            destination, RefRequest("ref", "release")
        )


@pytest.mark.parametrize(
    ("ref_request", "expected_kind", "expected_ref_fragment"),
    [
        (RefRequest("branch", "release"), "branch", "refs/heads/release"),
        (RefRequest("tag", "v1.2.3"), "tag", "refs/tags/v1.2.3"),
        (RefRequest("commit", "d" * 40), "commit", "d" * 40),
        (RefRequest("pr", "27"), "pr", "refs/heads/feature"),
    ],
)
def test_fetch_resolves_explicit_branch_tag_commit_and_pr(
    tmp_path, ref_request, expected_kind, expected_ref_fragment
):
    destination = tmp_path / ref_request.kind
    destination.mkdir()
    runner = StatefulGitRunner(remote_url=HTTPS_LINK["href"], fetch_commit="c" * 40)
    client = FakeClient([HTTPS_LINK])

    result = make_access(client, runner).fetch(destination, ref_request)

    assert result.ref_kind == expected_kind
    assert result.resolved_commit == "c" * 40
    fetch_call = next(call for call in runner.calls if call["args"][0] == "fetch")
    assert expected_ref_fragment in "\0".join(fetch_call["args"])
    if ref_request.kind == "pr":
        assert client.pr_calls == [27]


def test_fetch_pr_uses_fork_source_repository_and_verifies_latest_commit(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    fork_link = {
        "name": "http",
        "href": "https://bitbucket.example/scm/FORK/trading-fork.git",
    }
    expected_commit = "c" * 40
    runner = StatefulGitRunner(
        remote_url=HTTPS_LINK["href"],
        fetch_commit=expected_commit,
    )
    client = FakeClient(
        [HTTPS_LINK],
        pr_commit=expected_commit,
        pr_source_links=[fork_link],
    )

    result = make_access(client, runner).fetch(destination, RefRequest("pr", "27"))

    assert result.resolved_commit == expected_commit
    fetch_call = next(call for call in runner.calls if call["args"][0] == "fetch")
    assert fetch_call["args"][2] == fork_link["href"]
    assert fetch_call["args"][3] == "refs/heads/feature"
    assert fetch_call["safe_url"] == fork_link["href"]


def test_fetch_pr_rejects_source_ref_commit_mismatch(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    runner = StatefulGitRunner(
        remote_url=HTTPS_LINK["href"],
        fetch_commit="d" * 40,
    )

    with pytest.raises(GitOperationError, match="did not resolve"):
        make_access(FakeClient([HTTPS_LINK], pr_commit="c" * 40), runner).fetch(
            destination, RefRequest("pr", "27")
        )


def test_fetch_pr_loads_fork_clone_links_when_pr_embeds_only_identity(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    fork_link = {
        "name": "http",
        "href": "https://bitbucket.example/scm/FORK/trading-fork.git",
    }
    runner = StatefulGitRunner(
        remote_url=HTTPS_LINK["href"],
        fetch_commit="c" * 40,
    )
    client = FakeClient(
        [HTTPS_LINK],
        pr_source_identity=("FORK", "trading-fork"),
        source_lookup_links=[fork_link],
    )

    make_access(client, runner).fetch(destination, RefRequest("pr", "27"))

    fetch_call = next(call for call in runner.calls if call["args"][0] == "fetch")
    assert fetch_call["args"][2] == fork_link["href"]


@pytest.mark.parametrize("commit", ["abc1234", "f" * 39, "f" * 41])
def test_commit_selector_requires_full_object_id(tmp_path, commit):
    destination = tmp_path / "checkout"
    destination.mkdir()
    runner = StatefulGitRunner(remote_url=HTTPS_LINK["href"])

    with pytest.raises(GitOperationError, match="full 40- or 64-character"):
        make_access(FakeClient([HTTPS_LINK]), runner).fetch(
            destination, RefRequest("commit", commit)
        )


def test_pr_selector_requires_positive_id(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    runner = StatefulGitRunner(remote_url=HTTPS_LINK["href"])

    with pytest.raises(GitOperationError, match="positive"):
        make_access(FakeClient([HTTPS_LINK]), runner).fetch(
            destination, RefRequest("pr", "0")
        )


def test_ref_selector_rejects_unsupported_fully_qualified_ref(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    runner = StatefulGitRunner(remote_url=HTTPS_LINK["href"])

    with pytest.raises(GitOperationError, match="branch and tag refs only"):
        make_access(FakeClient([HTTPS_LINK]), runner).fetch(
            destination, RefRequest("ref", "refs/pull-requests/27/from")
        )


def test_full_ref_failure_recovery_preserves_ref_selector(tmp_path):
    class FailingFetchRunner(StatefulGitRunner):
        def run(self, args, **kwargs):
            if args[0] == "fetch":
                raise GitOperationError("network failed")
            return super().run(args, **kwargs)

    destination = tmp_path / "checkout"
    destination.mkdir()
    runner = FailingFetchRunner(remote_url=HTTPS_LINK["href"])

    with pytest.raises(GitOperationError) as error_info:
        make_access(FakeClient([HTTPS_LINK]), runner).fetch(
            destination, RefRequest("ref", "refs/heads/master")
        )

    retry = next(item for item in error_info.value.recovery if "repo fetch" in item)
    assert "--ref refs/heads/master" in retry
    assert "--branch refs/heads/master" not in retry


def test_ensure_is_unchanged_when_checkout_already_matches(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    (destination / ".git").mkdir()
    runner = StatefulGitRunner(remote_url=HTTPS_LINK["href"], head="a" * 40)

    result = make_access(FakeClient([HTTPS_LINK]), runner).ensure(
        destination, RefRequest("branch", "master")
    )

    assert result.status == "ok"
    assert result.action == "unchanged"
    assert result.changed is False
    assert not any(call["args"][0] == "merge" for call in runner.calls)


def test_ensure_fast_forwards_clean_matching_branch(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    (destination / ".git").mkdir()
    runner = StatefulGitRunner(
        remote_url=HTTPS_LINK["href"],
        head="a" * 40,
        tracking_commit="a" * 40,
        fetch_commit="b" * 40,
    )

    result = make_access(FakeClient([HTTPS_LINK]), runner).ensure(
        destination, RefRequest("branch", "master")
    )

    assert result.action == "fast_forwarded"
    assert result.resolved_commit == "b" * 40
    assert result.changed is True
    merge_call = next(call for call in runner.calls if call["args"][0] == "merge")
    assert merge_call["args"][1] == "--ff-only"


@pytest.mark.parametrize(
    ("runner_kwargs", "warning_fragment"),
    [
        ({"dirty": True}, "dirty"),
        ({"branch": "feature"}, "current branch"),
        ({"detached": True}, "detached"),
    ],
)
def test_ensure_fetches_but_returns_partial_without_updating_unsafe_checkout(
    tmp_path, runner_kwargs, warning_fragment
):
    destination = tmp_path / warning_fragment.replace(" ", "-")
    destination.mkdir()
    (destination / ".git").mkdir()
    runner = StatefulGitRunner(
        remote_url=HTTPS_LINK["href"],
        head="a" * 40,
        tracking_commit="a" * 40,
        fetch_commit="b" * 40,
        **runner_kwargs,
    )

    result = make_access(FakeClient([HTTPS_LINK]), runner).ensure(
        destination, RefRequest("branch", "master")
    )

    assert result.status == "partial"
    assert result.exit_code == 1
    assert result.action == "fetched"
    assert warning_fragment in result.warning
    assert result.resolved_commit == "b" * 40
    assert not any(call["args"][0] == "merge" for call in runner.calls)


def test_existing_checkout_rejects_wrong_remote_before_fetch(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    runner = StatefulGitRunner(
        remote_url="https://other.example/scm/TRAD/trading.git"
    )

    with pytest.raises(GitOperationError, match="expected Bitbucket repository"):
        make_access(FakeClient([HTTPS_LINK, SSH_LINK]), runner).fetch(
            destination, RefRequest("branch", "master")
        )

    assert not any(call["args"][0] == "fetch" for call in runner.calls)


def test_existing_checkout_rejects_effective_url_rewrite_before_fetch(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    runner = StatefulGitRunner(
        remote_url=HTTPS_LINK["href"],
        effective_remote_urls=("https://attacker.example/TRAD/trading.git",),
    )

    with pytest.raises(GitOperationError, match="expected Bitbucket repository"):
        make_access(FakeClient([HTTPS_LINK]), runner).fetch(
            destination, RefRequest("branch", "master")
        )

    assert not any(call["args"][0] == "fetch" for call in runner.calls)


def test_existing_checkout_rejects_multiple_origin_fetch_urls(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    runner = StatefulGitRunner(
        remote_url=HTTPS_LINK["href"],
        effective_remote_urls=(
            HTTPS_LINK["href"],
            "https://attacker.example/TRAD/trading.git",
        ),
    )

    with pytest.raises(GitOperationError, match="multiple fetch URLs"):
        make_access(FakeClient([HTTPS_LINK]), runner).fetch(
            destination, RefRequest("branch", "master")
        )


def test_existing_checkout_rejects_query_data_without_echoing_it(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    persisted_secret = "persisted-query-secret"
    runner = StatefulGitRunner(
        remote_url=f"{HTTPS_LINK['href']}?access_token={persisted_secret}"
    )

    with pytest.raises(GitOperationError) as error_info:
        make_access(FakeClient([HTTPS_LINK]), runner).fetch(
            destination, RefRequest("branch", "master")
        )

    assert persisted_secret not in str(error_info.value)
    assert not any(call["args"][0] == "fetch" for call in runner.calls)


def test_server_clone_link_with_query_data_is_rejected_without_echoing_it(tmp_path):
    secret = "server-query-secret"
    link = {"name": "http", "href": f"{HTTPS_LINK['href']}?token={secret}"}

    with pytest.raises(GitOperationError) as error_info:
        make_access(FakeClient([link]), StatefulGitRunner()).clone(
            tmp_path / "checkout", RefRequest("branch", "master")
        )

    assert secret not in str(error_info.value)


def test_server_clone_link_with_malformed_port_is_a_git_operation_error(tmp_path):
    link = {"name": "http", "href": "https://bitbucket.example:not-a-port/repo.git"}

    with pytest.raises(GitOperationError, match="malformed"):
        make_access(FakeClient([link]), StatefulGitRunner()).clone(
            tmp_path / "checkout", RefRequest("branch", "master")
        )


def test_existing_checkout_rejects_subdirectory_instead_of_updating_parent(tmp_path):
    checkout = tmp_path / "checkout"
    subdirectory = checkout / "src"
    subdirectory.mkdir(parents=True)
    runner = StatefulGitRunner(
        remote_url=HTTPS_LINK["href"], checkout_root=checkout
    )

    with pytest.raises(GitOperationError, match="repository root"):
        make_access(FakeClient([HTTPS_LINK]), runner).ensure(
            subdirectory, RefRequest("branch", "master")
        )

    assert not any(call["args"][0] == "fetch" for call in runner.calls)


def test_existing_checkout_rejects_credentialed_http_remote_without_echoing_it(tmp_path):
    destination = tmp_path / "checkout"
    destination.mkdir()
    secret = "already-persisted-secret"
    runner = StatefulGitRunner(
        remote_url=f"https://alice:{secret}@bitbucket.example/scm/TRAD/trading.git"
    )

    with pytest.raises(GitOperationError) as error_info:
        make_access(FakeClient([HTTPS_LINK]), runner).fetch(
            destination, RefRequest("branch", "master")
        )

    assert secret not in str(error_info.value)
    assert not any(call["args"][0] == "fetch" for call in runner.calls)


def test_fetch_failure_reports_checkout_state_and_recovery(tmp_path):
    class FailingFetchRunner(StatefulGitRunner):
        def run(self, args, **kwargs):
            if args[0] == "fetch":
                raise GitOperationError("network failed")
            return super().run(args, **kwargs)

    destination = tmp_path / "checkout"
    destination.mkdir()
    runner = FailingFetchRunner(remote_url=HTTPS_LINK["href"])

    with pytest.raises(GitOperationError) as error_info:
        make_access(FakeClient([HTTPS_LINK]), runner).fetch(
            destination, RefRequest("branch", "master")
        )

    assert error_info.value.state == {
        "branch": "master",
        "head": "a" * 40,
        "dirty": False,
        "detached": False,
    }
    assert error_info.value.recovery
    assert any("repo fetch" in command for command in error_info.value.recovery)


def test_clone_fetch_failure_recovery_preserves_partial_checkout(tmp_path):
    class FailingFetchRunner(StatefulGitRunner):
        def run(self, args, **kwargs):
            if args[0] == "fetch":
                raise GitOperationError("network failed")
            return super().run(args, **kwargs)

    destination = tmp_path / "checkout"

    with pytest.raises(GitOperationError) as error_info:
        make_access(FakeClient([HTTPS_LINK]), FailingFetchRunner()).clone(
            destination, RefRequest("branch", "master")
        )

    assert destination.exists()
    assert any(command.startswith("Move the partial checkout with `mv --") for command in error_info.value.recovery)
    assert any("repo clone" in command for command in error_info.value.recovery)


def test_ensure_fetch_failure_recovery_retries_ensure(tmp_path):
    class FailingFetchRunner(StatefulGitRunner):
        def run(self, args, **kwargs):
            if args[0] == "fetch":
                raise GitOperationError("network failed")
            return super().run(args, **kwargs)

    destination = tmp_path / "checkout"
    destination.mkdir()
    (destination / ".git").mkdir()

    with pytest.raises(GitOperationError) as error_info:
        make_access(
            FakeClient([HTTPS_LINK]),
            FailingFetchRunner(remote_url=HTTPS_LINK["href"]),
        ).ensure(destination, RefRequest("branch", "master"))

    assert any("repo ensure" in command for command in error_info.value.recovery)


def test_post_clone_checkout_failure_reports_state_and_recovery(tmp_path):
    class FailingCheckoutRunner(StatefulGitRunner):
        def run(self, args, **kwargs):
            if args[0] == "checkout":
                raise GitOperationError("checkout failed")
            return super().run(args, **kwargs)

    runner = FailingCheckoutRunner(fetch_commit="b" * 40)

    with pytest.raises(GitOperationError) as error_info:
        make_access(FakeClient([HTTPS_LINK]), runner).clone(
            tmp_path / "checkout", RefRequest("branch", "master")
        )

    assert error_info.value.state
    assert error_info.value.recovery


def test_fast_forward_failure_reports_state_and_recovery(tmp_path):
    class FailingMergeRunner(StatefulGitRunner):
        def run(self, args, **kwargs):
            if args[0] == "merge":
                raise GitOperationError("merge failed")
            return super().run(args, **kwargs)

    destination = tmp_path / "checkout"
    destination.mkdir()
    (destination / ".git").mkdir()
    runner = FailingMergeRunner(
        remote_url=HTTPS_LINK["href"],
        head="a" * 40,
        tracking_commit="a" * 40,
        fetch_commit="b" * 40,
    )

    with pytest.raises(GitOperationError) as error_info:
        make_access(FakeClient([HTTPS_LINK]), runner).ensure(
            destination, RefRequest("branch", "master")
        )

    assert error_info.value.state["head"] == "a" * 40
    assert error_info.value.recovery


def test_existing_non_repository_directory_is_rejected(tmp_path):
    destination = tmp_path / "not-a-repository"
    destination.mkdir()
    (destination / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(GitOperationError, match="not a Git checkout"):
        make_access(FakeClient([HTTPS_LINK]), GitRunner(environ={})).fetch(
            destination, RefRequest("branch", "master")
        )


def test_clone_rejects_nonempty_destination_without_running_git(tmp_path):
    destination = tmp_path / "occupied"
    destination.mkdir()
    (destination / "keep.txt").write_text("keep", encoding="utf-8")
    runner = StatefulGitRunner()

    with pytest.raises(GitOperationError, match="not empty"):
        make_access(FakeClient([HTTPS_LINK]), runner).clone(
            destination, RefRequest("branch", "master")
        )

    assert runner.calls == []


def test_clone_preflights_missing_branch_before_creating_destination(tmp_path):
    destination = tmp_path / "checkout"
    runner = StatefulGitRunner(ls_remote_refs=())

    with pytest.raises(GitOperationError, match="was not found"):
        make_access(FakeClient([HTTPS_LINK]), runner).clone(
            destination, RefRequest("branch", "missing")
        )

    assert not destination.exists()
    assert not any(call["args"][0] == "clone" for call in runner.calls)


def test_clone_supports_a_destination_with_missing_parent_directories(tmp_path):
    destination = tmp_path / "missing" / "nested" / "checkout"
    runner = StatefulGitRunner(fetch_commit="b" * 40)

    result = make_access(FakeClient([HTTPS_LINK]), runner).clone(
        destination, RefRequest("branch", "master")
    )

    assert result.action == "cloned"
    preflight = next(call for call in runner.calls if call["args"][0] == "ls-remote")
    assert preflight["cwd"] is None
    assert destination.exists()


def test_real_git_clone_noop_fast_forward_and_dirty_partial(tmp_path):
    seed = tmp_path / "seed"
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    ssh_wrapper = tmp_path / "local-ssh"
    ssh_wrapper.write_text(
        "#!/bin/sh\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    -o|-p|-i|-F) shift 2 ;;\n"
        "    -*) shift ;;\n"
        "    *) shift; break ;;\n"
        "  esac\n"
        "done\n"
        "exec sh -c \"$1\"\n",
        encoding="utf-8",
    )
    ssh_wrapper.chmod(0o700)

    def git(*args, cwd=None):
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "--bare", str(remote))
    git("init", "-b", "master", str(seed))
    git("config", "user.name", "btbkt test", cwd=seed)
    git("config", "user.email", "btbkt@example.invalid", cwd=seed)
    tracked = seed / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git("add", "tracked.txt", cwd=seed)
    git("commit", "-m", "one", cwd=seed)
    git("remote", "add", "origin", str(remote), cwd=seed)
    git("push", "-u", "origin", "master", cwd=seed)

    remote_url = f"ssh://git@localhost{remote}"
    client = FakeClient([{"name": "ssh", "href": remote_url}])
    runner = GitRunner(environ={"GIT_SSH_COMMAND": str(ssh_wrapper), "PATH": "/usr/bin:/bin"})
    access = make_access(client, runner)

    cloned = access.clone(checkout, RefRequest("branch", "master"))
    assert cloned.action == "cloned"
    assert cloned.resolved_commit == git("rev-parse", "HEAD", cwd=seed)

    unchanged = access.ensure(checkout, RefRequest("branch", "master"))
    assert unchanged.action == "unchanged"
    assert unchanged.changed is False

    tracked.write_text("two\n", encoding="utf-8")
    git("commit", "-am", "two", cwd=seed)
    git("push", "origin", "master", cwd=seed)
    fast_forwarded = access.ensure(checkout, RefRequest("branch", "master"))
    assert fast_forwarded.action == "fast_forwarded"
    assert git("rev-parse", "HEAD", cwd=checkout) == git("rev-parse", "HEAD", cwd=seed)

    (checkout / "tracked.txt").write_text("local dirty change\n", encoding="utf-8")
    tracked.write_text("three\n", encoding="utf-8")
    git("commit", "-am", "three", cwd=seed)
    git("push", "origin", "master", cwd=seed)
    partial = access.ensure(checkout, RefRequest("branch", "master"))
    assert partial.status == "partial"
    assert partial.action == "fetched"
    assert partial.state["dirty"] is True
    assert git("rev-parse", "HEAD", cwd=checkout) != git("rev-parse", "HEAD", cwd=seed)
    assert (checkout / "tracked.txt").read_text(encoding="utf-8") == "local dirty change\n"
