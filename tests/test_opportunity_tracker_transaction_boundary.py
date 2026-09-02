import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "research" / "server" / "monitoring" / "opportunity_tracker.py"


def _calls(tree: ast.AST, owner: str, method: str) -> list[ast.Call]:
    matches: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != method or not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id == owner:
            matches.append(node)
    return matches


def test_opportunity_tracker_has_explicit_long_lived_transaction_boundary() -> None:
    source = TRACKER.read_text(encoding="utf-8")
    tree = ast.parse(source)

    connect_calls = _calls(tree, "psycopg", "connect")
    assert len(connect_calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in connect_calls[0].keywords}
    autocommit = keywords.get("autocommit")
    assert isinstance(autocommit, ast.Constant)
    assert autocommit.value is True

    # Long-lived reads must not create an implicit outer transaction. Writes remain
    # atomic through explicit transaction() contexts instead of manual commit().
    assert not _calls(tree, "connection", "commit")
    assert len(_calls(tree, "connection", "transaction")) >= 2
