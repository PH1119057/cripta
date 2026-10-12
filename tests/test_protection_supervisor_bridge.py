from datetime import UTC, datetime
from bybit_workbench.exchange.bybit.protection_supervisor_bridge import account_protection_verdicts

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
            return Cursor([{"payload_json": '{"initial_protection":{"stop_loss_pct":"10","take_profit_price":"110"}}'}])
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
