import ast
from pathlib import Path


def _python_files(repo_root):
    for directory in ("src", "scripts"):
        yield from (repo_root / directory).rglob("*.py")


def test_source_files_parse_with_python_39_grammar():
    repo_root = Path(__file__).resolve().parents[1]

    for path in _python_files(repo_root):
        ast.parse(path.read_text(), filename=str(path), feature_version=(3, 9))


def test_python_39_grammar_coverage_includes_scripts():
    repo_root = Path(__file__).resolve().parents[1]
    assert repo_root / "scripts" / "sync_skill.py" in _python_files(repo_root)
