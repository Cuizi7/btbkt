import ast
from pathlib import Path


def test_source_files_parse_with_python_39_grammar():
    source_root = Path(__file__).resolve().parents[1] / "src"

    for path in source_root.rglob("*.py"):
        ast.parse(path.read_text(), filename=str(path), feature_version=(3, 9))
