import ast
from pathlib import Path


def test_post_command_protection_audit_is_read_only():
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    audit = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "record_effective_protection_after_command")
    calls = {node.func.id for node in ast.walk(audit)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "api_get" in calls
    assert "api_post" not in calls
    assert "handle_exchange_mutation_barrier" not in calls
    assert source.index("state='completed'") < source.index(
        "record_effective_protection_after_command(",
        source.index("def execute_command("),
    )


def test_evidence_migration_registered():
    installer = Path("operations/infrastructure/install_verified_release.sh").read_text()
    migration = Path("operations/sql/20261012_effective_protection_evidence_v1.sql").read_text()
    assert "20261012_effective_protection_evidence_v1.sql" in installer
    assert "GRANT SELECT, INSERT ON runtime.effective_protection_evidence TO cripta" in migration
