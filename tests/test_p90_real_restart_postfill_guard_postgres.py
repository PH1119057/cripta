"""Disposable PG16: verify actual restart proof query and permissions.

The required CI mode MUST fail (not SKIP) if the disposable connection is absent.
Two-session first-fill handoff race is a separate, still-open acceptance gate.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

DSN = os.getenv("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
REQUIRED = os.getenv("CRIPTA_P90_POSTFILL_E2E_REQUIRED") == "1"
SOURCE = Path(__file__).resolve().parents[1] / "operations/connectivity/private_runtime.py"


def _sql():
    assert SOURCE.is_file(), "Real private runtime checkout required"
    fn = next(n for n in ast.parse(SOURCE.read_text()).body
              if isinstance(n, ast.FunctionDef) and n.name == "resolve_prestart_entry_commands")
    found = [n.value for n in ast.walk(fn) if isinstance(n, ast.Constant)
             and isinstance(n.value, str)
             and "JOIN strategy_exit.exit_observations o" in n.value]
    assert len(found) == 1, "Exactly one original native Exit handoff proof"
    return found[0]


def test_cripta_role_can_read_guard_on_disposable_pg16_only():
    if not DSN:
        if REQUIRED:
            pytest.fail("Mandatory disposable PG16 DSN missing; skip forbidden")
        pytest.skip("Only explicitly configured disposable DB may be used")
    import psycopg
    with psycopg.connect(DSN) as c:
        assert c.info.dbname == "cripta_integration", "Production DB forbidden"
        assert c.info.server_version >= 160000
        with c.transaction():
            c.execute("SET LOCAL ROLE cripta")
            assert c.execute(
                _sql(),
                ("synthetic-entry-never-present", "synthetic-account-never-present"),
            ).fetchone() is None
