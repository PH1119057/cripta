"""Fail-closed critical-fault guards on the two real Entry execution paths.

The snippets are evaluated without Exchange mutation or database writes.
"""

from __future__ import annotations

import textwrap
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "operations" / "connectivity"


def source(name: str) -> str:
    return (SOURCE / name).read_text(encoding="utf-8")


def helper(name: str):
    text = source(name)
    start = text.index("def _critical_lifecycle_fault_code(")
    end = text.index("\n\ndef ", start)
    namespace: dict[str, object] = {}
    code = "from __future__ import annotations\n" + text[start:end]
    exec(compile(code, name, "exec"), namespace)
    return namespace["_critical_lifecycle_fault_code"]


class Cursor:
    def __init__(self, row: object) -> None:
        self.row = row

    def fetchone(self) -> object:
        return self.row


class DB:
    def __init__(self, faults=(), *, row_kind="dict", fail=False) -> None:
        self.faults = tuple(faults)
        self.row_kind = row_kind
        self.fail = fail
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def execute(self, sql: str, params=()) -> Cursor:
        self.calls.append((sql, tuple(params)))
        if self.fail:
            raise RuntimeError("database unavailable")
        assert "runtime.lifecycle_faults" in sql
        assert "state='OPEN'" in sql
        assert "severity='CRITICAL'" in sql
        assert "NULLIF(exact_ids->>" in sql
        assert "ORDER BY detected_at,fault_id LIMIT 1" in sql
        assert len(params) == 1
        account = params[0]
        matching = [
            item
            for item in self.faults
            if item["state"] == "OPEN"
            and item["severity"] == "CRITICAL"
            and (not item.get("account") or item["account"] == account)
        ]
        if not matching:
            return Cursor(None)
        code = matching[0]["code"]
        row = {"fault_code": code} if self.row_kind == "dict" else (code,)
        return Cursor(row)

    def transaction(self):
        return nullcontext()


def fault(*, account="account-a", state="OPEN", severity="CRITICAL"):
    return {"account": account, "state": state, "severity": severity, "code": "EXIT_LOST"}


@pytest.mark.parametrize(
    ("name", "row_kind", "kwargs"),
    [
        ("universal_entry_consumer.py", "dict", {"trading_account_id": "account-a"}),
        ("private_runtime.py", "tuple", {}),
    ],
)
@pytest.mark.parametrize(
    ("faults", "expected"),
    [
        ([fault()], "EXIT_LOST"),
        ([fault(account="account-b")], None),
        ([fault(account=None)], "EXIT_LOST"),
        ([fault(account="")], "EXIT_LOST"),
        ([fault(state="RESOLVED")], None),
        ([fault(severity="ERROR")], None),
        ([], None),
    ],
)
def test_scoped_critical_faults(name, row_kind, kwargs, faults, expected):
    gate = helper(name)
    if name == "private_runtime.py":
        gate.__globals__["TRADING_ACCOUNT_ID"] = "account-a"
    db = DB(faults, row_kind=row_kind)
    assert gate(db, **kwargs) == expected
    assert db.calls[-1][1] == ("account-a",)


@pytest.mark.parametrize(
    ("name", "kwargs"),
    [
        ("universal_entry_consumer.py", {"trading_account_id": ""}),
        ("private_runtime.py", {}),
    ],
)
def test_missing_account_refuses_lookup(name, kwargs):
    gate = helper(name)
    if name == "private_runtime.py":
        gate.__globals__["TRADING_ACCOUNT_ID"] = ""
    db = DB()
    with pytest.raises(RuntimeError, match="TradingAccount identity missing"):
        gate(db, **kwargs)
    assert not db.calls


@pytest.mark.parametrize(
    ("name", "kwargs", "kind"),
    [
        ("universal_entry_consumer.py", {"trading_account_id": "account-a"}, "dict"),
        ("private_runtime.py", {}, "tuple"),
    ],
)
def test_query_failure_does_not_fail_open(name, kwargs, kind):
    gate = helper(name)
    if name == "private_runtime.py":
        gate.__globals__["TRADING_ACCOUNT_ID"] = "account-a"
    with pytest.raises(RuntimeError, match="database unavailable"):
        gate(DB(row_kind=kind, fail=True), **kwargs)


def consumer_fragment() -> str:
    text = source("universal_entry_consumer.py")
    start = text.index("    fault_code = _critical_lifecycle_fault_code(\n")
    end = text.index("    live_ready, failed_live_checks = _live_arm_ready(", start)
    return text[start:end]


def test_pending_request_cancelled_before_dispatch():
    actions: list[tuple[str, dict[str, object]]] = []

    def capture(label):
        def record(*_args, **kwargs):
            actions.append((label, kwargs))

        return record

    env = {
        "_critical_lifecycle_fault_code": lambda *_a, **_k: "EXIT_LOST",
        "_record_blocked": capture("blocked"),
        "_release_pre_exchange_admission": capture("released"),
        "_record_request_state": capture("state"),
    }
    exec(
        "def gate(request, connection, current):\n"
        + consumer_fragment()
        + '    return "CONTINUE"\n',
        env,
    )
    request = SimpleNamespace(trading_account_id="account-a")
    assert env["gate"](request, DB(), datetime.now(UTC)) == "BLOCKED:CRITICAL_LIFECYCLE_FAULT"
    assert [item[0] for item in actions] == ["blocked", "released", "state"]
    assert actions[1][1]["reason"] == "PRE_EXCHANGE_BLOCK:CRITICAL_LIFECYCLE_FAULT"
    assert actions[2][1]["state"] == "REQUEST_CANCELLED"


def test_healthy_account_reaches_existing_checks():
    env = {"_critical_lifecycle_fault_code": lambda *_a, **_k: None}
    exec(
        "def gate(request, connection, current):\n"
        + consumer_fragment()
        + '    return "CONTINUE"\n',
        env,
    )
    request = SimpleNamespace(trading_account_id="account-a")
    assert env["gate"](request, DB(), datetime.now(UTC)) == "CONTINUE"


def test_queued_private_entry_refuses_ready_with_critical_fault():
    text = source("private_runtime.py")
    start = text.index("    fault_code = _critical_lifecycle_fault_code(connection)\n")
    end_marker = '    return True, "REARM_READY"\n'
    end = text.index(end_marker, start) + len(end_marker)
    env = {"_critical_lifecycle_fault_code": lambda _db: "EXIT_LOST"}
    exec("def gate(connection):\n" + text[start:end], env)
    assert env["gate"](DB()) == (False, "CRITICAL_LIFECYCLE_FAULT:EXIT_LOST")
    env["_critical_lifecycle_fault_code"] = lambda _db: None
    assert env["gate"](DB()) == (True, "REARM_READY")


def test_pre_first_mutation_safety_block():
    text = source("private_runtime.py")
    # The fault check now precedes policy/order validation AND the first POST.
    # Isolate the safety guard itself before evaluating it independently.
    target = "        contract = initial_protection_contract(payload)"
    entry_start = text.index('    elif kind == "entry":\n')
    endpoint = text.index(target, entry_start)
    start = text.rfind(
        "        fault_code = _critical_lifecycle_fault_code(connection)\n",
        entry_start,
        endpoint,
    )
    assert start > 0
    block = textwrap.indent(textwrap.dedent(text[start:endpoint]), "    ")

    class PreMutationSafetyBlock(RuntimeError):
        pass

    env = {
        "_critical_lifecycle_fault_code": lambda _db: "EXIT_LOST",
        "PreMutationSafetyBlock": PreMutationSafetyBlock,
    }
    exec("def gate(connection):\n" + block + '    return "ALLOWED"\n', env)
    with pytest.raises(PreMutationSafetyBlock, match="CRITICAL_LIFECYCLE_FAULT:EXIT_LOST"):
        env["gate"](DB())
    env["_critical_lifecycle_fault_code"] = lambda _db: None
    assert env["gate"](DB()) == "ALLOWED"


def test_entry_only_mutation_barrier_is_before_first_exchange_post():
    text = source("private_runtime.py")
    sent = text.index('        api_post(\n            "/v5/position/set-leverage",')
    guard = text.rfind("        fault_code = _critical_lifecycle_fault_code(connection)", 0, sent)
    protection = text.index("        contract = initial_protection_contract(payload)", guard)
    order = text.index("        order: dict[str, object] = {", protection)
    assert 0 < guard < protection < order < sent
    assert text.count('raise PreMutationSafetyBlock(f"CRITICAL_LIFECYCLE_FAULT:{fault_code}")') == 1
    consumer = source("universal_entry_consumer.py")
    assert consumer.index('return "BLOCKED:CRITICAL_LIFECYCLE_FAULT"') < consumer.index(
        "live_ready, failed_live_checks"
    )
