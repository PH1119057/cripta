"""Execute the release preparation path without DB, filesystem or Exchange writes."""
import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.p90l90_real_source_compatibility import (
    P90RealSourceCompatibilityUnverified,
)

SCRIPT = Path("operations/implementation/register_p90_real_source_compatibility.py")
RELEASE = "b" * 40
ORIGIN = "a" * 40
EPOCH = "p90-real-epoch-" + "c" * 40


def preparation(tmp_path, *, epochs=None, reason=None, gate=0, registration_error=None):
    root = tmp_path / RELEASE
    calls = []
    inspections = []

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def transaction(self):
            return nullcontext()

        def execute(self, sql, params=()):
            if "SELECT epoch_id" in sql:
                return SimpleNamespace(fetchall=lambda: [(EPOCH,)] if epochs is None else epochs)
            if "SELECT enabled" in sql:
                return SimpleNamespace(fetchone=lambda: (gate,))
            if "SELECT source_commit" in sql:
                return SimpleNamespace(fetchone=lambda: (ORIGIN,))
            assert sql == "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, READ WRITE"

    def inspect(*_, **__):
        inspections.append(True)
        if reason and not calls:
            raise P90RealSourceCompatibilityUnverified(reason)
        return SimpleNamespace(epoch_id=EPOCH, origin_commit=ORIGIN, loaded_commit=RELEASE)

    def register(*_, **kw):
        calls.append(kw)
        if registration_error:
            raise RuntimeError(registration_error)

    ns = {
        "os": SimpleNamespace(geteuid=lambda: 107),
        "pwd": SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=107)),
        "sys": SimpleNamespace(prefix=str(root / ".venv")),
        "__file__": str(root / SCRIPT), "ROOT": root, "Path": Path,
        "SOURCE_CHECKOUT": tmp_path / "source_checkout",
        "psycopg": SimpleNamespace(connect=lambda *_, **__: DB()),
        "load_active_strategy_bundles": lambda _: (), "MAIN_ID": "main", "SUB_ID": "sub",
        "inspect_real_source_identity": inspect,
        "register_real_source_compatibility": register,
        "P90RealSourceCompatibilityUnverified": P90RealSourceCompatibilityUnverified,
    }
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "perform")
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SCRIPT), "exec"), ns)
    return lambda: ns["perform"](
        release_commit=RELEASE, epoch_id=None, active_epoch=True,
        register=True, release_preparation=True,
    ), calls, inspections


def test_fresh_install_without_epoch_does_not_register(tmp_path, capsys):
    run, calls, inspections = preparation(tmp_path, epochs=[])
    run()
    assert not calls and not inspections
    assert "NOT_APPLICABLE_NO_ACTIVE_EPOCH" in capsys.readouterr().out


def test_repeat_install_validates_existing_evidence_without_registration(tmp_path, capsys):
    run, calls, inspections = preparation(tmp_path, gate=1)
    run()
    assert not calls and len(inspections) == 1
    assert "ALREADY_VERIFIED=1" in capsys.readouterr().out


def test_missing_release_is_registered_once_then_independently_read_back(tmp_path, capsys):
    run, calls, inspections = preparation(
        tmp_path, reason="P90_REAL_SOURCE_COMPATIBILITY_RELEASE_NOT_REGISTERED",
    )
    run()
    assert len(calls) == 1 and len(inspections) == 2
    assert calls[0]["epoch_id"] == EPOCH and calls[0]["loaded_commit"] == RELEASE
    assert "ORIGIN=" + ORIGIN in capsys.readouterr().out


def test_open_gate_defers_missing_registration_without_changing_controls(tmp_path, capsys):
    run, calls, _ = preparation(
        tmp_path, reason="P90_REAL_SOURCE_COMPATIBILITY_RELEASE_NOT_REGISTERED", gate=1,
    )
    run()
    assert not calls
    assert "DEFERRED_ENTRY_GATE_NOT_OFF" in capsys.readouterr().out


@pytest.mark.parametrize("reason", [
    "P90_REAL_SOURCE_COMPATIBILITY_ATTESTATION_CORRUPT_OR_INCOMPATIBLE",
    "P90_REAL_SOURCE_COMPATIBILITY_CHECKPOINT_PROVENANCE_UNVERIFIED",
])
def test_incompatible_evidence_is_never_registered_or_hidden(tmp_path, reason):
    run, calls, _ = preparation(tmp_path, reason=reason)
    with pytest.raises(P90RealSourceCompatibilityUnverified, match=reason):
        run()
    assert not calls


def test_ambiguous_epoch_never_registers(tmp_path):
    run, calls, _ = preparation(tmp_path, epochs=[(EPOCH,), ("other",)])
    with pytest.raises(RuntimeError, match="ACTIVE_EPOCH_UNVERIFIED"):
        run()
    assert not calls


def test_registrar_failure_is_not_reported_as_pass(tmp_path, capsys):
    run, _, _ = preparation(
        tmp_path, reason="P90_REAL_SOURCE_COMPATIBILITY_RELEASE_NOT_REGISTERED",
        registration_error="INCOMPATIBLE_BYTES",
    )
    with pytest.raises(RuntimeError, match="INCOMPATIBLE_BYTES"):
        run()
    assert "=PASS" not in capsys.readouterr().out
