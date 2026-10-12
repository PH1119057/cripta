from datetime import UTC, datetime

from bybit_workbench.exchange.bybit.protection_supervisor_bridge import (
    account_protection_verdicts,
)

NOW = datetime(2026, 10, 12, tzinfo=UTC)


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, legacy=False):
        self.legacy = legacy

    def execute(self, statement, params=()):
        if "command_type='entry'" in statement:
            return Cursor([{
                "payload_json": (
                    '{"initial_protection":{"stop_loss_pct":"10","take_profit_price":"110"}}'
                )
            }])
        if "requested_at_epoch_ms" in statement:
            if self.legacy:
                return Cursor([{
                    "command_id": "other-be", "command_type": "break_even",
                    "payload_json": '{"symbol":"APTUSDT"}',
                    "result_json": '{"retCode":0}',
                }])
            return Cursor([])
        raise AssertionError("unexpected query")


def _position():
    return {
        "position_id": "p1", "symbol": "APTUSDT", "position_idx": 0,
        "side": "Buy", "entry_command_id": "entry1", "actual_avg_fill": "100",
        "fill_at": NOW,
    }


def _exchange():
    return {
        "complete": True, "account_ref": "BYBIT:UNIFIED",
        "account_user_id": "approved-existing-account",
        "observed_at": NOW.isoformat(),
        "symbols": {"APTUSDT": {
            "tick": "0.01",
            "positions": [{"symbol": "APTUSDT", "positionIdx": 0,
                           "side": "Buy", "size": "1",
                           "stopLoss": "90", "takeProfit": "110"}],
            "orders": [],
        }},
    }


def test_exact_position_verifies_only_with_fresh_exchange():
    assert account_protection_verdicts(
        FakeConnection(), positions=[_position()],
        account_evidence=_exchange(), now=NOW,
    )["p1"]["state"] == "VERIFIED"
    assert account_protection_verdicts(
        FakeConnection(), positions=[_position()],
        account_evidence=None, now=NOW,
    )["p1"]["state"] == "UNKNOWN"


def test_legacy_unattributed_stop_is_unknown_not_protected():
    outcome = account_protection_verdicts(
        FakeConnection(legacy=True), positions=[_position()],
        account_evidence=_exchange(), now=NOW,
    )
    assert outcome["p1"]["state"] == "UNKNOWN"


def test_exchange_missing_stop_is_not_verified():
    snapshot = _exchange()
    snapshot["symbols"]["APTUSDT"]["positions"][0]["stopLoss"] = "0"
    assert account_protection_verdicts(
        FakeConnection(), positions=[_position()],
        account_evidence=snapshot, now=NOW,
    )["p1"]["state"] == "NOT_VERIFIED"


def test_supervisor_runtime_uses_independent_read_and_keeps_lifecycle_nontrading():
    import ast
    from pathlib import Path
    source = Path("operations/monitoring/lifecycle_supervisor_runtime.py").read_text()
    tree = ast.parse(source)
    functions = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)}
    assert "_independent_exchange_snapshot" in functions
    assert "account_protection_verdicts" in source
    assert "exchange_protection=protection" in source
    reader = ast.unparse(functions["_independent_exchange_snapshot"])
    assert "direct_account_snapshot(" in reader
    assert "if not symbols" not in reader
    assert "api_post(" not in reader
    assert "submit_order" not in reader
    assert "trade" not in reader.lower().split("return asyncio.run", 1)[-1]


def test_legacy_supervisor_marker_cannot_prove_protection():
    from pathlib import Path
    source = Path("src/bybit_workbench/lifecycle_supervisor.py").read_text()
    assert "independent Exchange account protection evidence not supplied" in source
    assert '"VERIFIED" if row["initial_protection_confirmed_at"]' not in source


def test_supervisor_scans_all_managed_open_positions_across_bot_instances():
    from pathlib import Path

    source = Path("operations/monitoring/lifecycle_supervisor_runtime.py").read_text()
    selection = source.split("def _account_positions(", 1)[1].split(
        "def _independent_exchange_snapshot(", 1
    )[0]
    assert "FROM runtime.position_ownership" in selection
    assert "WHERE state='OPEN'" in selection
    assert "bot_instance_id='universal-entry'" not in selection


def test_supervisor_uses_existing_single_encrypted_credential_read_only():
    from pathlib import Path

    unit = Path("operations/systemd/cripta-lifecycle-supervisor.service").read_text()
    runtime = Path("operations/monitoring/lifecycle_supervisor_runtime.py").read_text()
    transport = Path("src/bybit_workbench/exchange/bybit/transport.py").read_text()

    assert "User=cripta" in unit
    assert "LoadCredentialEncrypted=bybit-mainnet:" in unit
    assert '"bybit-mainnet"' in runtime
    assert "approved_user_id" not in runtime
    assert "direct_account_snapshot(" in runtime
    assert "LifecycleAccountReadTransport" in transport
    assert '"/v5/position/list"' in transport
    assert '"/v5/order/realtime"' in transport
    assert '"/v5/user/query-api"' in transport
    assert '"/v5/order/create"' not in transport.split("class LifecycleAccountReadTransport", 1)[1]
