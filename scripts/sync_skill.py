#!/usr/bin/env python3
"""Install the canonical btbkt workflow skill or check exact parity."""

import argparse
import hashlib
from pathlib import Path
import shutil
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SKILL = REPOSITORY_ROOT / "skills" / "using-btbkt-pr-workflows"
DEFAULT_DESTINATION = Path.home() / ".agents" / "skills" / "using-btbkt-pr-workflows"

def _tree_manifest(root: Path) -> dict[str, tuple[str, str]]:
    manifest = {}
    for path in sorted(root.rglob("*")):
        relative_path = path.relative_to(root).as_posix()
        if path.is_symlink():
            manifest[relative_path] = ("symlink", str(path.readlink()))
        elif path.is_dir():
            manifest[relative_path] = ("directory", "")
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest[relative_path] = ("file", digest)
        else:
            manifest[relative_path] = ("other", "")
    return manifest


def skills_match(source: Path, destination: Path) -> bool:
    """Return whether destination has exactly the same tree and file content."""
    source = Path(source)
    destination = Path(destination)
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"skill source must be a directory: {source}")
    if destination.is_symlink() or not destination.is_dir():
        return False
    return _tree_manifest(source) == _tree_manifest(destination)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(str(path))


def _unused_sibling(parent: Path, prefix: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent)))
    path.rmdir()
    return path


def _validated_paths(source: Path, destination: Path) -> tuple[Path, Path]:
    source = source.expanduser()
    destination = destination.expanduser()
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"skill source must be a directory: {source}")
    if destination.name == "..":
        raise ValueError(
            "skill source and destination must not overlap: "
            f"destination ends with a parent path component: {destination}"
        )

    resolved_source = source.resolve()
    resolved_destination = destination.parent.resolve() / destination.name
    if (
        resolved_source == resolved_destination
        or resolved_destination.is_relative_to(resolved_source)
        or resolved_source.is_relative_to(resolved_destination)
    ):
        raise ValueError(
            "skill source and destination must not overlap: "
            f"source={resolved_source}, destination={resolved_destination}"
        )
    return resolved_source, resolved_destination


def sync_skill(source: Path, destination: Path) -> None:
    """Replace destination with an exact copy staged beside it."""
    source, destination = _validated_paths(Path(source), Path(destination))

    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = _unused_sibling(destination.parent, f".{destination.name}.stage-")
    backup = None
    preserve_failure_artifacts = False
    try:
        shutil.copytree(str(source), str(staged), symlinks=True)
        if destination.is_symlink() or destination.exists():
            backup = _unused_sibling(
                destination.parent, f".{destination.name}.backup-"
            )
            destination.rename(backup)
        try:
            staged.rename(destination)
        except Exception as install_error:
            if backup is not None:
                try:
                    backup.rename(destination)
                except Exception as rollback_error:
                    preserve_failure_artifacts = True
                    raise RuntimeError(
                        "skill install and rollback both failed; preserving "
                        f"staged tree at {staged} and backup at {backup}; "
                        f"install error: {install_error}; "
                        f"rollback error: {rollback_error}"
                    ) from rollback_error
            raise
        if backup is not None:
            _remove_path(backup)
    finally:
        if not preserve_failure_artifacts:
            _remove_path(staged)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install or check the canonical btbkt workflow skill."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the destination without changing it.",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=DEFAULT_DESTINATION,
        help=f"Installed skill directory (default: {DEFAULT_DESTINATION}).",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    destination = args.destination.expanduser()
    if args.check:
        if skills_match(CANONICAL_SKILL, destination):
            print(f"skill matches canonical source: {destination}")
            return 0
        print(f"skill differs from canonical source: {destination}")
        return 1

    sync_skill(CANONICAL_SKILL, destination)
    print(f"synced canonical skill to: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
