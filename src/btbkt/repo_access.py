from __future__ import annotations

import os
import re
import secrets
import shlex
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence
from urllib.parse import unquote, urlsplit, urlunsplit


@dataclass(frozen=True)
class GitAuth:
    username: str
    secret: str


class GitOperationError(RuntimeError):
    """A Git failure whose message is safe to serialize."""

    def __init__(
        self,
        message: str,
        *,
        state: Optional[Mapping[str, Any]] = None,
        recovery: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.state = dict(state or {})
        self.recovery = list(recovery)


@dataclass(frozen=True)
class RefRequest:
    kind: str
    value: str

    def __post_init__(self) -> None:
        if self.kind not in {"branch", "tag", "commit", "pr", "ref"}:
            raise ValueError(f"Unsupported ref kind: {self.kind}")
        if not self.value:
            raise ValueError("Requested ref must not be empty.")


@dataclass(frozen=True)
class ResolvedRef:
    kind: str
    value: str
    fetch_source: str
    requested: str
    remote_url: Optional[str] = None
    expected_commit: Optional[str] = None
    selector_kind: Optional[str] = None


@dataclass(frozen=True)
class RepositoryResult:
    status: str
    action: str
    project: str
    repo: str
    path: str
    requested_ref: str
    ref_kind: str
    resolved_commit: str
    changed: bool
    warning: Optional[str] = None
    recovery: Sequence[str] = ()
    state: Optional[Mapping[str, Any]] = None
    exit_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "action": self.action,
            "project": self.project,
            "repo": self.repo,
            "path": self.path,
            "requested_ref": self.requested_ref,
            "ref_kind": self.ref_kind,
            "resolved_commit": self.resolved_commit,
            "changed": self.changed,
            "warning": self.warning,
        }
        if self.recovery:
            payload["recovery"] = list(self.recovery)
        if self.state is not None:
            payload["state"] = dict(self.state)
        return payload


class GitRunner:
    """Run Git with an isolated, non-persistent credential boundary."""

    def __init__(
        self,
        *,
        subprocess_run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        environ: Optional[Mapping[str, str]] = None,
        temporary_parent: Optional[Path] = None,
    ) -> None:
        self._subprocess_run = subprocess_run
        self._environ = dict(os.environ if environ is None else environ)
        for name in (
            "BITBUCKET_BASE_URL",
            "BITBUCKET_USERNAME",
            "BITBUCKET_TOKEN",
            "BITBUCKET_PASSWORD",
            "BTBKT_GIT_USERNAME",
            "BTBKT_GIT_SECRET",
        ):
            self._environ.pop(name, None)
        self._environ["GIT_TERMINAL_PROMPT"] = "0"
        self._temporary_parent = temporary_parent

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Optional[Path] = None,
        auth: Optional[GitAuth] = None,
        acceptable_returncodes: Sequence[int] = (0,),
        safe_url: Optional[str] = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "git",
            "-c",
            "credential.helper=",
            "-c",
            "core.hooksPath=/dev/null",
        ]
        effective_args = list(args)
        if safe_url is not None:
            _validate_safe_git_url(safe_url)
            placeholder = f"btbkt-safe-{secrets.token_hex(16)}:"
            if safe_url not in effective_args:
                raise ValueError("safe_url must be an exact Git command argument.")
            effective_args = [placeholder if value == safe_url else value for value in effective_args]
            # Git applies one longest-prefix insteadOf rewrite. A random, exact
            # placeholder prevents broader or equal user/system rewrites from
            # changing the validated endpoint after btbkt enables askpass.
            command.extend(["-c", f"url.{safe_url}.insteadOf={placeholder}"])
        command.extend(effective_args)
        try:
            if auth is None:
                return self._run(
                    command,
                    cwd=cwd,
                    env=self._environ,
                    redactions=(),
                    acceptable_returncodes=acceptable_returncodes,
                )
            with self._askpass_environment(auth) as env:
                return self._run(
                    command,
                    cwd=cwd,
                    env=env,
                    redactions=(auth.secret,),
                    acceptable_returncodes=acceptable_returncodes,
                )
        except GitOperationError:
            raise
        except Exception as exc:
            message = _redact_text(str(exc), (auth.secret,) if auth else ())
            raise GitOperationError(f"Unable to execute Git: {message}") from exc

    def _run(
        self,
        command: Sequence[str],
        *,
        cwd: Optional[Path],
        env: Mapping[str, str],
        redactions: Sequence[str],
        acceptable_returncodes: Sequence[int],
    ) -> subprocess.CompletedProcess[str]:
        completed = self._subprocess_run(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode not in acceptable_returncodes:
            details = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
            details = _redact_text(details, redactions)
            raise GitOperationError(f"Git command failed with exit {completed.returncode}: {details}")
        return completed

    @contextmanager
    def _askpass_environment(self, auth: GitAuth) -> Iterator[dict[str, str]]:
        parent = str(self._temporary_parent) if self._temporary_parent is not None else None
        with tempfile.TemporaryDirectory(prefix="btbkt-git-askpass-", dir=parent) as directory:
            askpass = Path(directory) / "askpass.sh"
            askpass.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  *sername*) printf '%s\\n' \"$BTBKT_GIT_USERNAME\" ;;\n"
                "  *) printf '%s\\n' \"$BTBKT_GIT_SECRET\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            askpass.chmod(0o700)
            env = dict(self._environ)
            env.update(
                {
                    "GIT_ASKPASS": str(askpass),
                    "GIT_TERMINAL_PROMPT": "0",
                    "BTBKT_GIT_USERNAME": auth.username,
                    "BTBKT_GIT_SECRET": auth.secret,
                }
            )
            yield env


class RepositoryAccess:
    """Credential-safe Git operations for one Bitbucket repository."""

    _HTTP_WARNING = "Credentials are being sent over plain HTTP; use HTTPS when available."

    def __init__(
        self,
        *,
        client: Any,
        project: str,
        repo: str,
        auth: GitAuth,
        runner: Optional[GitRunner] = None,
    ) -> None:
        self.client = client
        self.project = project
        self.repo = repo
        self.auth = auth
        self.runner = runner or GitRunner()

    def clone(self, path: Path, request: Optional[RefRequest] = None) -> RepositoryResult:
        destination = Path(path).expanduser().resolve()
        self._require_clone_destination(destination)
        resolved: Optional[ResolvedRef] = None
        fetch_completed = False
        try:
            repository = self.client.get_repository()
            clone_urls = _clone_urls(repository)
            clone_url = _select_clone_url(clone_urls)
            probe_path = destination.parent if destination.parent.is_dir() else None
            resolved = self._resolve_request(probe_path, request, clone_url)
            self._preflight_clone_ref(probe_path, resolved, clone_url)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.runner.run(
                ["clone", "--no-checkout", clone_url, str(destination)],
                auth=self._auth_for_url(clone_url),
                safe_url=clone_url,
            )
            self.runner.run(["remote", "set-url", "origin", clone_url], cwd=destination)
            origin_url = self._validated_origin(destination, clone_urls)
            _, commit = self._fetch_ref(destination, resolved, origin_url)
            fetch_completed = True
            if resolved.kind == "branch":
                local_branch = f"refs/heads/{resolved.value}"
                branch_exists = self.runner.run(
                    ["show-ref", "--verify", "--quiet", local_branch],
                    cwd=destination,
                    acceptable_returncodes=(0, 1),
                ).returncode == 0
                if branch_exists:
                    self.runner.run(["checkout", resolved.value], cwd=destination)
                else:
                    self.runner.run(
                        [
                            "checkout",
                            "-b",
                            resolved.value,
                            "--track",
                            f"refs/remotes/origin/{resolved.value}",
                        ],
                        cwd=destination,
                    )
                if self._commit(destination, "HEAD") != commit:
                    self.runner.run(
                        ["merge", "--ff-only", f"refs/remotes/origin/{resolved.value}"],
                        cwd=destination,
                    )
            else:
                self.runner.run(["checkout", "--detach", commit], cwd=destination)
            state = self._checkout_state(destination)
            return self._result(
                destination,
                resolved,
                commit,
                action="cloned",
                changed=True,
                warning=self._transport_warning(origin_url),
                state=state,
            )
        except GitOperationError as exc:
            exc = self._with_fetch_phase(exc, fetch_completed)
            raise self._failure_with_context(exc, destination, "clone", resolved) from exc

    def fetch(self, path: Path, request: Optional[RefRequest] = None) -> RepositoryResult:
        destination = Path(path).expanduser().resolve()
        resolved: Optional[ResolvedRef] = None
        fetch_completed = False
        try:
            clone_urls = _clone_urls(self.client.get_repository())
            origin_url = self._validated_origin(destination, clone_urls)
            resolved = self._resolve_request(destination, request, origin_url)
            before, commit = self._fetch_ref(destination, resolved, origin_url)
            fetch_completed = True
            changed = before != commit
            return self._result(
                destination,
                resolved,
                commit,
                action="fetched" if changed else "unchanged",
                changed=changed,
                warning=self._transport_warning(resolved.remote_url or origin_url),
                state=self._checkout_state(destination),
            )
        except GitOperationError as exc:
            exc = self._with_fetch_phase(exc, fetch_completed)
            raise self._failure_with_context(exc, destination, "fetch", resolved) from exc

    def ensure(self, path: Path, request: Optional[RefRequest] = None) -> RepositoryResult:
        destination = Path(path).expanduser().resolve()
        if not destination.exists() or (destination.is_dir() and not any(destination.iterdir())):
            return self.clone(destination, request)
        resolved: Optional[ResolvedRef] = None
        fetch_completed = False
        try:
            clone_urls = _clone_urls(self.client.get_repository())
            origin_url = self._validated_origin(destination, clone_urls)
            resolved = self._resolve_request(destination, request, origin_url)
            before, commit = self._fetch_ref(destination, resolved, origin_url)
            fetch_completed = True
            fetched_changed = before != commit
            state = self._checkout_state(destination)
            transport_warning = self._transport_warning(resolved.remote_url or origin_url)

            if resolved.kind != "branch":
                warning = _join_warnings(
                    transport_warning,
                    f"Fetched {resolved.kind} {resolved.requested}; existing worktree was not changed.",
                )
                return self._result(
                    destination,
                    resolved,
                    commit,
                    action="fetched" if fetched_changed else "unchanged",
                    changed=fetched_changed,
                    warning=warning,
                    state=state,
                )

            blocked = self._unsafe_update_reason(state, resolved.value)
            if blocked:
                return self._partial_result(
                    destination,
                    resolved,
                    commit,
                    fetched_changed,
                    _join_warnings(transport_warning, blocked),
                    state,
                )

            if state.get("head") == commit:
                return self._result(
                    destination,
                    resolved,
                    commit,
                    action="fetched" if fetched_changed else "unchanged",
                    changed=fetched_changed,
                    warning=transport_warning,
                    state=state,
                )

            target_ref = f"refs/remotes/origin/{resolved.value}"
            can_fast_forward = self.runner.run(
                ["merge-base", "--is-ancestor", "HEAD", target_ref],
                cwd=destination,
                acceptable_returncodes=(0, 1),
            ).returncode == 0
            if not can_fast_forward:
                local_ahead = self.runner.run(
                    ["merge-base", "--is-ancestor", target_ref, "HEAD"],
                    cwd=destination,
                    acceptable_returncodes=(0, 1),
                ).returncode == 0
                reason = (
                    "The current branch is ahead of the requested remote commit; no update was performed."
                    if local_ahead
                    else "The current branch has diverged from the requested remote commit; no update was performed."
                )
                return self._partial_result(
                    destination,
                    resolved,
                    commit,
                    fetched_changed,
                    _join_warnings(transport_warning, reason),
                    state,
                )

            self.runner.run(["merge", "--ff-only", target_ref], cwd=destination)
            updated_state = self._checkout_state(destination)
            return self._result(
                destination,
                resolved,
                commit,
                action="fast_forwarded",
                changed=True,
                warning=transport_warning,
                state=updated_state,
            )
        except GitOperationError as exc:
            exc = self._with_fetch_phase(exc, fetch_completed)
            raise self._failure_with_context(exc, destination, "ensure", resolved) from exc

    def _resolve_request(
        self,
        path: Optional[Path],
        request: Optional[RefRequest],
        origin_url: str,
    ) -> ResolvedRef:
        if request is None:
            default = self.client.get_default_branch()
            ref_id = str(default.get("id") or "") if isinstance(default, Mapping) else ""
            display = str(default.get("displayId") or "") if isinstance(default, Mapping) else ""
            branch = display or _strip_ref_prefix(ref_id, "refs/heads/")
            if not branch:
                raise GitOperationError("Bitbucket did not return a configured default branch.")
            request = RefRequest("branch", branch)

        if request.kind == "branch":
            self._validate_branch(request.value)
            return ResolvedRef("branch", request.value, f"refs/heads/{request.value}", request.value)
        if request.kind == "tag":
            self._validate_tag(request.value)
            return ResolvedRef("tag", request.value, f"refs/tags/{request.value}", request.value)
        if request.kind == "commit":
            if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", request.value):
                raise GitOperationError("--commit must be a full 40- or 64-character hexadecimal Git object ID.")
            return ResolvedRef("commit", request.value, request.value, request.value)
        if request.kind == "pr":
            if not request.value.isdigit() or int(request.value) <= 0:
                raise GitOperationError("--pr must be a positive pull request ID.")
            pull_request = self.client.get_pull_request(int(request.value))
            from_ref = pull_request.get("fromRef") if isinstance(pull_request, Mapping) else None
            commit = from_ref.get("latestCommit") if isinstance(from_ref, Mapping) else None
            source_ref = from_ref.get("id") if isinstance(from_ref, Mapping) else None
            if not isinstance(commit, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit):
                raise GitOperationError(f"Pull request {request.value} did not include a source commit.")
            if not isinstance(source_ref, str) or not source_ref.startswith("refs/"):
                raise GitOperationError(f"Pull request {request.value} did not include a source ref.")
            source_repository = from_ref.get("repository") if isinstance(from_ref, Mapping) else None
            source_urls = _clone_urls(source_repository, required=False)
            source_project, source_slug = _repository_identity(source_repository)
            is_target_repository = (
                source_project is not None
                and source_slug is not None
                and source_project.casefold() == self.project.casefold()
                and source_slug.casefold() == self.repo.casefold()
            )
            if not source_urls and source_project and source_slug and not is_target_repository:
                source_urls = _clone_urls(
                    self.client.get_repository(project=source_project, repo=source_slug),
                    required=False,
                )
                if not source_urls:
                    raise GitOperationError(
                        f"Pull request {request.value} source repository did not include a supported clone URL."
                    )
            source_url = _select_clone_url(source_urls) if source_urls else origin_url
            return ResolvedRef(
                "pr",
                request.value,
                source_ref,
                request.value,
                remote_url=source_url,
                expected_commit=commit.lower(),
            )
        return self._resolve_untyped_ref(path, request.value, origin_url)

    def _resolve_untyped_ref(self, path: Optional[Path], value: str, origin_url: str) -> ResolvedRef:
        if value.startswith("refs/heads/"):
            branch = _strip_ref_prefix(value, "refs/heads/")
            self._validate_branch(branch)
            return ResolvedRef("branch", branch, value, value, selector_kind="ref")
        if value.startswith("refs/tags/"):
            tag = _strip_ref_prefix(value, "refs/tags/")
            self._validate_tag(tag)
            return ResolvedRef("tag", tag, value, value, selector_kind="ref")
        if value.startswith("refs/"):
            raise GitOperationError("--ref supports branch and tag refs only; use --pr for pull requests.")
        if re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", value):
            return ResolvedRef("commit", value, value, value, selector_kind="ref")
        self._validate_branch(value)
        response = self.runner.run(
            ["ls-remote", origin_url, f"refs/heads/{value}", f"refs/tags/{value}"],
            cwd=path,
            auth=self._auth_for_url(origin_url),
            safe_url=origin_url,
        )
        refs = {line.split("\t", 1)[1] for line in response.stdout.splitlines() if "\t" in line}
        branch_ref = f"refs/heads/{value}"
        tag_ref = f"refs/tags/{value}"
        if branch_ref in refs and tag_ref in refs:
            raise GitOperationError(
                f"Ref {value!r} is both a branch and tag; use --branch or --tag explicitly."
            )
        if branch_ref in refs:
            return ResolvedRef("branch", value, branch_ref, value, selector_kind="ref")
        if tag_ref in refs:
            return ResolvedRef("tag", value, tag_ref, value, selector_kind="ref")
        raise GitOperationError(f"Ref {value!r} was not found as a branch or tag.")

    def _preflight_clone_ref(
        self,
        path: Optional[Path],
        resolved: ResolvedRef,
        origin_url: str,
    ) -> None:
        if resolved.kind == "commit":
            return
        remote_url = resolved.remote_url or origin_url
        response = self.runner.run(
            ["ls-remote", remote_url, resolved.fetch_source],
            cwd=path,
            auth=self._auth_for_url(remote_url),
            safe_url=remote_url,
        )
        matches = [line for line in response.stdout.splitlines() if "\t" in line]
        if not matches:
            raise GitOperationError(f"Requested {resolved.kind} ref was not found on the Bitbucket repository.")
        if resolved.expected_commit is not None:
            advertised = matches[0].split("\t", 1)[0].lower()
            if advertised != resolved.expected_commit:
                raise GitOperationError("Pull request source ref no longer matches its reported source commit; retry the request.")

    def _fetch_ref(self, path: Path, resolved: ResolvedRef, origin_url: str) -> tuple[Optional[str], str]:
        if resolved.kind == "branch":
            local_ref = f"refs/remotes/origin/{resolved.value}"
            refspec = f"{resolved.fetch_source}:{local_ref}"
        elif resolved.kind == "tag":
            local_ref = f"refs/tags/{resolved.value}"
            refspec = f"{resolved.fetch_source}:{local_ref}"
        else:
            local_ref = "FETCH_HEAD"
            refspec = resolved.fetch_source

        before = self._try_commit(path, local_ref)
        remote_url = resolved.remote_url or origin_url
        self.runner.run(
            ["fetch", "--no-tags", remote_url, refspec],
            cwd=path,
            auth=self._auth_for_url(remote_url),
            safe_url=remote_url,
        )
        try:
            commit = self._commit(path, local_ref)
        except GitOperationError as exc:
            raise GitOperationError(
                str(exc),
                state={**exc.state, "fetch_completed": True},
                recovery=exc.recovery,
            ) from exc
        if resolved.expected_commit is not None and commit.lower() != resolved.expected_commit:
            raise GitOperationError("Fetched pull request ref did not resolve to Bitbucket's reported source commit.")
        return before, commit

    def _try_commit(self, path: Path, ref: str) -> Optional[str]:
        result = self.runner.run(
            ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=path,
            acceptable_returncodes=(0, 1),
        )
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None

    def _commit(self, path: Path, ref: str) -> str:
        commit = self.runner.run(
            ["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=path
        ).stdout.strip()
        if not commit:
            raise GitOperationError(f"Git did not resolve {ref} to a commit.")
        return commit

    def _validated_origin(self, path: Path, clone_urls: Sequence[str]) -> str:
        if not path.is_dir():
            raise GitOperationError(f"Repository path is not a directory: {path}")
        try:
            inside = self.runner.run(["rev-parse", "--is-inside-work-tree"], cwd=path)
        except GitOperationError as exc:
            raise GitOperationError(
                f"Path is not a Git checkout for the expected Bitbucket repository: {path}",
                recovery=("Move the directory aside or choose an empty destination.",),
            ) from exc
        if inside.stdout.strip() != "true":
            raise GitOperationError(f"Path is not a Git worktree: {path}")
        root_text = self.runner.run(["rev-parse", "--show-toplevel"], cwd=path).stdout.strip()
        if not root_text or Path(root_text).resolve() != path:
            raise GitOperationError(
                f"Path must be the repository root, not a subdirectory: {path}",
                recovery=(f"Use the checkout root reported by `git -C {shlex.quote(str(path))} rev-parse --show-toplevel`.",),
            )
        remote_result = self.runner.run(
            ["remote", "get-url", "--all", "origin"],
            cwd=path,
            acceptable_returncodes=(0, 2),
        )
        origin_urls = [line.strip() for line in remote_result.stdout.splitlines() if line.strip()]
        if not origin_urls:
            raise GitOperationError("Checkout has no remote.origin.url; refusing to fetch.")
        if len(origin_urls) != 1:
            raise GitOperationError("Checkout origin has multiple fetch URLs; refusing to choose one.")
        origin_url = origin_urls[0]
        _validate_safe_git_url(origin_url)
        expected = {_normalize_clone_url(url) for url in clone_urls}
        if _normalize_clone_url(origin_url) not in expected:
            raise GitOperationError(
                "Checkout origin is not the expected Bitbucket repository; refusing to fetch.",
                recovery=("Inspect `git remote -v` and use the checkout that matches the requested project/repo.",),
            )
        return origin_url

    def _checkout_state(self, path: Path) -> dict[str, Any]:
        branch_result = self.runner.run(
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
            cwd=path,
            acceptable_returncodes=(0, 1),
        )
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
        head = self._commit(path, "HEAD")
        dirty = bool(self.runner.run(["status", "--porcelain"], cwd=path).stdout)
        return {"branch": branch, "head": head, "dirty": dirty, "detached": branch is None}

    def _unsafe_update_reason(self, state: Mapping[str, Any], requested_branch: str) -> Optional[str]:
        if state.get("detached"):
            return "Checkout is detached; fetched the requested ref without changing the worktree."
        if state.get("dirty"):
            return "Checkout is dirty; fetched the requested ref without changing the worktree."
        if state.get("branch") != requested_branch:
            return (
                f"The current branch is {state.get('branch')!r}, not {requested_branch!r}; "
                "fetched without switching branches."
            )
        return None

    def _partial_result(
        self,
        path: Path,
        resolved: ResolvedRef,
        commit: str,
        changed: bool,
        warning: str,
        state: Mapping[str, Any],
    ) -> RepositoryResult:
        return self._result(
            path,
            resolved,
            commit,
            status="partial",
            action="fetched",
            changed=changed,
            warning=warning,
            state=state,
            recovery=(
                f"Inspect the checkout with `git -C {shlex.quote(str(path))} status --short --branch`.",
                "Clean or select the requested branch, then rerun the same btbkt repo ensure command.",
            ),
            exit_code=1,
        )

    def _result(
        self,
        path: Path,
        resolved: ResolvedRef,
        commit: str,
        *,
        action: str,
        changed: bool,
        warning: Optional[str],
        state: Mapping[str, Any],
        status: str = "ok",
        recovery: Sequence[str] = (),
        exit_code: int = 0,
    ) -> RepositoryResult:
        return RepositoryResult(
            status=status,
            action=action,
            project=self.project,
            repo=self.repo,
            path=str(path),
            requested_ref=resolved.requested,
            ref_kind=resolved.kind,
            resolved_commit=commit,
            changed=changed,
            warning=warning,
            recovery=recovery,
            state=state,
            exit_code=exit_code,
        )

    def _require_clone_destination(self, path: Path) -> None:
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise GitOperationError(f"Clone destination exists and is not empty: {path}")

    def _validate_branch(self, branch: str) -> None:
        self.runner.run(["check-ref-format", "--branch", branch])

    def _validate_tag(self, tag: str) -> None:
        self.runner.run(["check-ref-format", f"refs/tags/{tag}"])

    def _auth_for_url(self, url: str) -> Optional[GitAuth]:
        scheme = urlsplit(url).scheme.lower()
        return self.auth if scheme in {"http", "https"} else None

    def _transport_warning(self, url: str) -> Optional[str]:
        return self._HTTP_WARNING if urlsplit(url).scheme.lower() == "http" else None

    def _failure_with_context(
        self,
        error: GitOperationError,
        path: Path,
        operation: str,
        resolved: Optional[ResolvedRef] = None,
    ) -> GitOperationError:
        state = self._safe_checkout_state(path)
        state.update(error.state)
        recovery = list(error.recovery)
        if not recovery:
            recovery.append(f"Inspect the checkout with `git -C {shlex.quote(str(path))} status --short --branch`.")
            if operation == "clone" and path.exists():
                retry_path = Path(f"{path}.partial-retry")
                recovery.append(
                    "Move the partial checkout with `mv -- "
                    f"{shlex.quote(str(path))} {shlex.quote(str(retry_path))}` before retrying."
                )
            if resolved is not None:
                option_kind = resolved.selector_kind or resolved.kind
                option = f"--{option_kind} {shlex.quote(resolved.requested)}"
                recovery.append(
                    "Retry with `btbkt "
                    f"--project {shlex.quote(self.project)} --repo {shlex.quote(self.repo)} "
                    f"repo {operation} {option} {shlex.quote(str(path))}`."
                )
            else:
                recovery.append("Check network access and repository permissions, then rerun the same btbkt command.")
        return GitOperationError(str(error), state=state, recovery=recovery)

    def _with_fetch_phase(self, error: GitOperationError, completed: bool) -> GitOperationError:
        if not completed or error.state.get("fetch_completed") is True:
            return error
        return GitOperationError(
            str(error),
            state={**error.state, "fetch_completed": True},
            recovery=error.recovery,
        )

    def _safe_checkout_state(self, path: Path) -> dict[str, Any]:
        if path.is_dir():
            try:
                return self._checkout_state(path)
            except GitOperationError:
                pass
        return {"path_exists": path.exists(), "path": str(path)}


def _clone_urls(repository: Any, *, required: bool = True) -> list[str]:
    links = repository.get("links") if isinstance(repository, Mapping) else None
    clone_links = links.get("clone") if isinstance(links, Mapping) else None
    urls = []
    if isinstance(clone_links, list):
        for link in clone_links:
            href = link.get("href") if isinstance(link, Mapping) else None
            if isinstance(href, str) and href:
                sanitized = _strip_http_userinfo(href)
                _validate_safe_git_url(sanitized)
                urls.append(sanitized)
    if required and not urls:
        raise GitOperationError("Bitbucket repository response did not include a supported clone URL.")
    return urls


def _repository_identity(repository: Any) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(repository, Mapping):
        return None, None
    project = repository.get("project")
    project_key = project.get("key") if isinstance(project, Mapping) else None
    slug = repository.get("slug")
    return (
        project_key if isinstance(project_key, str) and project_key else None,
        slug if isinstance(slug, str) and slug else None,
    )


def _select_clone_url(urls: Sequence[str]) -> str:
    ranked = []
    for url in urls:
        scheme = urlsplit(url).scheme.lower()
        if scheme == "https":
            rank = 0
        elif scheme == "http":
            rank = 1
        elif scheme == "ssh" or _SCP_URL_RE.fullmatch(url):
            rank = 2
        else:
            continue
        ranked.append((rank, url))
    if not ranked:
        raise GitOperationError("Bitbucket did not return an HTTP(S) or SSH clone URL.")
    return min(ranked, key=lambda item: item[0])[1]


_SCP_URL_RE = re.compile(r"(?:(?P<user>[^@/:]+)@)?(?P<host>[^/:]+):(?P<path>.+)")


def _normalize_clone_url(url: str) -> tuple[Any, ...]:
    _validate_safe_git_url(url)
    parsed = urlsplit(url)
    if parsed.scheme.lower() in {"http", "https"} and parsed.hostname:
        scheme = parsed.scheme.lower()
        default_port = 443 if scheme == "https" else 80
        return (scheme, parsed.hostname.lower(), _parsed_port(parsed, default_port), _clean_git_path(parsed.path))
    if parsed.scheme.lower() == "ssh" and parsed.hostname:
        return ("ssh", parsed.hostname.lower(), _parsed_port(parsed, 22), _clean_git_path(parsed.path))
    match = _SCP_URL_RE.fullmatch(url)
    if match:
        return ("ssh", match.group("host").lower(), 22, _clean_git_path(match.group("path")))
    raise GitOperationError("Unsupported Git clone URL returned by Bitbucket.")


def _clean_git_path(path: str) -> str:
    cleaned = unquote(path).strip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    return cleaned.lower()


def _http_url_has_userinfo(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme.lower() in {"http", "https"} and "@" in parsed.netloc


def _strip_http_userinfo(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise GitOperationError("Bitbucket returned a malformed clone URL; refusing to use it.") from exc
    if parsed.scheme.lower() in {"http", "https"} and parsed.password is not None:
        raise GitOperationError("Bitbucket returned a credential-bearing clone URL; refusing to use it.")
    if parsed.scheme.lower() not in {"http", "https"} or "@" not in parsed.netloc:
        return url
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _validate_safe_git_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() in {"http", "https", "ssh"}:
            parsed.port
    except ValueError as exc:
        raise GitOperationError("Git remote URL is malformed; refusing to use it.") from exc
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https", "ssh"}:
        if not parsed.hostname or parsed.query or parsed.fragment or parsed.password is not None:
            raise GitOperationError("Git remote URL contains credentials or unsupported URL data; refusing to use it.")
        if scheme in {"http", "https"} and _http_url_has_userinfo(url):
            raise GitOperationError("Checkout origin contains HTTP credentials; refusing to use it.")
        return
    if _SCP_URL_RE.fullmatch(url):
        return
    raise GitOperationError("Unsupported Git clone URL returned by Bitbucket.")


def _parsed_port(parsed: Any, default: int) -> int:
    try:
        return parsed.port or default
    except ValueError as exc:
        raise GitOperationError("Git remote URL is malformed; refusing to use it.") from exc


def _strip_ref_prefix(value: str, prefix: str) -> str:
    return value[len(prefix) :] if value.startswith(prefix) else ""


def _join_warnings(*warnings: Optional[str]) -> Optional[str]:
    values = [warning for warning in warnings if warning]
    return " ".join(values) if values else None


def _redact_text(text: str, redactions: Sequence[str]) -> str:
    for value in sorted({value for value in redactions if value}, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    return text
