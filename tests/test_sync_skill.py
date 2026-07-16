import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "skills" / "using-btbkt-pr-workflows" / "SKILL.md"
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_skill.py"


def load_sync_skill_module():
    spec = importlib.util.spec_from_file_location("btbkt_sync_skill", SYNC_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skill_documents_safe_review_mutation_contract():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    required_guidance = [
        "source ~/.zshrc",
        "public comment",
        "participant",
        "PENDING",
        "btbkt pr review-pending",
        "btbkt pr review-submit",
        "btbkt pr review-discard",
        "NoSuchPullRequestReviewException",
        "Do not blindly resend",
        "btbkt raw METHOD /rest/",
        "page.last",
        "inferred default branch",
    ]
    missing = [item for item in required_guidance if item not in skill]
    assert not missing, f"skill is missing required guidance: {missing}"

    unsafe_combined_command = (
        "btbkt pr review PR_ID --comment TEXT --approve|--needs-work|--unapprove"
    )
    assert unsafe_combined_command not in skill


def test_skill_requires_state_reread_after_failed_mutation():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "After any failed mutation" in skill
    assert "reread" in skill.lower()
    assert "never assume the failed request made no change" in skill.lower()


def test_skill_preserves_reply_404_and_diff_display_guidance():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "reply` returns 404" in skill
    assert "do not post a top-level" in skill
    assert "unified diff" in skill
    assert "200 lines" in skill
    assert "--diff-format structured" in skill


def test_make_compile_includes_scripts():
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "compileall -q src tests scripts" in makefile


def test_sync_skill_copies_an_exact_directory_tree(tmp_path):
    sync_module = load_sync_skill_module()

    source = tmp_path / "source"
    destination = tmp_path / "installed" / "workflow"
    (source / "agents").mkdir(parents=True)
    (source / "SKILL.md").write_text("canonical\n", encoding="utf-8")
    (source / "agents" / "openai.yaml").write_bytes(b"agent: btbkt\n")

    sync_module.sync_skill(source, destination)

    assert sync_module.skills_match(source, destination)
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == "canonical\n"
    assert (destination / "agents" / "openai.yaml").read_bytes() == b"agent: btbkt\n"

    (destination / "extra.txt").write_text("drift", encoding="utf-8")
    assert not sync_module.skills_match(source, destination)

    sync_module.sync_skill(source, destination)
    assert sync_module.skills_match(source, destination)
    assert not (destination / "extra.txt").exists()


def test_check_cli_succeeds_after_sync_and_fails_after_drift(tmp_path):
    sync_module = load_sync_skill_module()

    canonical_source = REPO_ROOT / "skills" / "using-btbkt-pr-workflows"
    destination = tmp_path / "workflow"
    sync_module.sync_skill(canonical_source, destination)

    matched = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), "--check", "--destination", str(destination)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert matched.returncode == 0, matched.stderr

    (destination / "SKILL.md").write_text("drift\n", encoding="utf-8")
    drifted = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), "--check", "--destination", str(destination)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert drifted.returncode != 0


def test_check_cli_does_not_create_a_missing_destination(tmp_path):
    destination = tmp_path / "missing" / "workflow"

    result = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), "--check", "--destination", str(destination)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not destination.exists()
    assert not destination.parent.exists()


def test_sync_replaces_destination_symlink_without_touching_target(tmp_path):
    sync_module = load_sync_skill_module()

    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("canonical\n", encoding="utf-8")

    symlink_target = tmp_path / "keep"
    symlink_target.mkdir()
    sentinel = symlink_target / "sentinel.txt"
    sentinel.write_text("untouched\n", encoding="utf-8")

    destination = tmp_path / "installed"
    try:
        destination.symlink_to(symlink_target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")

    sync_module.sync_skill(source, destination)

    assert destination.is_dir()
    assert not destination.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "untouched\n"
    assert sync_module.skills_match(source, destination)


@pytest.mark.parametrize("layout", ["equal", "destination_below", "source_below"])
def test_sync_rejects_overlapping_trees_without_writes(tmp_path, layout):
    sync_module = load_sync_skill_module()

    if layout == "equal":
        source = tmp_path / "tree"
        destination = source
    elif layout == "destination_below":
        source = tmp_path / "tree"
        destination = source / "nested" / "installed"
    else:
        destination = tmp_path / "tree"
        source = destination / "nested" / "source"

    source.mkdir(parents=True)
    sentinel = source / "SKILL.md"
    sentinel.write_text("canonical\n", encoding="utf-8")
    before = sorted(
        (path.relative_to(tmp_path).as_posix(), path.read_bytes() if path.is_file() else None)
        for path in tmp_path.rglob("*")
    )

    with pytest.raises(ValueError, match="overlap"):
        sync_module.sync_skill(source, destination)

    after = sorted(
        (path.relative_to(tmp_path).as_posix(), path.read_bytes() if path.is_file() else None)
        for path in tmp_path.rglob("*")
    )
    assert after == before
    assert sentinel.read_text(encoding="utf-8") == "canonical\n"


def test_sync_rejects_physical_destination_below_source_without_writes(tmp_path):
    sync_module = load_sync_skill_module()

    source = tmp_path / "source"
    source.mkdir()
    sentinel = source / "SKILL.md"
    sentinel.write_text("canonical\n", encoding="utf-8")
    physical_parent = source / "physical-parent"
    physical_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(physical_parent, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")
    destination = linked_parent / "installed"

    with pytest.raises(ValueError, match="overlap"):
        sync_module.sync_skill(source, destination)

    assert sentinel.read_text(encoding="utf-8") == "canonical\n"
    assert not destination.exists()
    assert not list(tmp_path.glob(".*.stage-*"))


def test_sync_rejects_trailing_parent_destination_without_writes(tmp_path):
    sync_module = load_sync_skill_module()

    source = tmp_path / "source" / "nested"
    source.mkdir(parents=True)
    sentinel = source / "SKILL.md"
    sentinel.write_text("canonical\n", encoding="utf-8")
    uncreated_parent = tmp_path / "new-parent"
    destination = uncreated_parent / ".."

    with pytest.raises(ValueError, match="overlap"):
        sync_module.sync_skill(source, destination)

    assert not uncreated_parent.exists()
    assert sentinel.read_text(encoding="utf-8") == "canonical\n"


def test_sync_can_replace_destination_symlink_pointing_at_source(tmp_path):
    sync_module = load_sync_skill_module()

    source = tmp_path / "source"
    source.mkdir()
    sentinel = source / "SKILL.md"
    sentinel.write_text("canonical\n", encoding="utf-8")
    destination = tmp_path / "installed"
    try:
        destination.symlink_to(source, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")

    sync_module.sync_skill(source, destination)

    assert not destination.is_symlink()
    assert sync_module.skills_match(source, destination)
    assert sentinel.read_text(encoding="utf-8") == "canonical\n"


def test_sync_preserves_stage_and_backup_when_install_and_rollback_fail(
    tmp_path, monkeypatch
):
    sync_module = load_sync_skill_module()

    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("canonical\n", encoding="utf-8")
    destination = tmp_path / "installed"
    destination.mkdir()
    (destination / "SKILL.md").write_text("previous\n", encoding="utf-8")

    original_rename = Path.rename

    def fail_install_and_restore(path, target):
        if path.name.startswith(".installed.stage-"):
            raise OSError("injected install rename failure")
        if path.name.startswith(".installed.backup-"):
            raise OSError("injected rollback rename failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_install_and_restore)

    with pytest.raises(RuntimeError) as error_info:
        sync_module.sync_skill(source, destination)

    message = str(error_info.value)
    assert "injected install rename failure" in message
    assert "injected rollback rename failure" in message
    staged = next(tmp_path.glob(".installed.stage-*"))
    backup = next(tmp_path.glob(".installed.backup-*"))
    assert str(staged) in message
    assert str(backup) in message
    assert (staged / "SKILL.md").read_text(encoding="utf-8") == "canonical\n"
    assert (backup / "SKILL.md").read_text(encoding="utf-8") == "previous\n"
    assert (source / "SKILL.md").read_text(encoding="utf-8") == "canonical\n"
