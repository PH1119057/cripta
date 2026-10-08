from datetime import UTC, datetime

from bybit_workbench.release_arm_safety import check_release_arm_invariant


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _Connection:
    def __init__(self, *, gate=True, sessions=(), permissions=()):
        self.gate = gate
        self.sessions = sessions
        self.permissions = permissions
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if "SELECT enabled FROM control.execution_gates" in sql:
            return _Result([(self.gate,)])
        if "FROM control.live_arm_sessions" in sql:
            return _Result(self.sessions)
        if "FROM strategy_entry.execution_permissions" in sql:
            return _Result(self.permissions)
        return _Result([])


def _writes(connection):
    return [sql for sql, _ in connection.statements if sql.lstrip().startswith(
        ("UPDATE", "INSERT")
    )]


def test_gate_on_old_session_creates_durable_critical_and_closes_new_entry():
    c = _Connection(
        gate=True,
        sessions=[("r1_injusdt", "1.1-micro-live", "INJUSDT", "a" * 40)],
        permissions=[("r1_injusdt", "1.1-micro-live")],
    )
    assert check_release_arm_invariant(
        c, loaded_commit="b" * 40, now=datetime(2026, 10, 9, tzinfo=UTC)
    ) is False
    writes = "\n".join(_writes(c))
    assert "INSERT INTO control.release_arm_incidents" in writes
    assert "UPDATE control.execution_gates" in writes
    assert "INSERT INTO control.execution_gate_events" in writes
    assert "runtime.trade_commands" not in writes
    assert "runtime.hot_positions" not in writes
    assert "strategy_exit" not in writes


def test_guard_runs_without_signal_and_does_not_change_matching_gate():
    c = _Connection(
        sessions=[("r1_injusdt", "1.1-micro-live", "INJUSDT", "a" * 40)],
        permissions=[("r1_injusdt", "1.1-micro-live")],
    )
    assert check_release_arm_invariant(c, loaded_commit="a" * 40) is True
    writes = "\n".join(_writes(c))
    assert "UPDATE control.execution_gates" not in writes
    assert "INSERT INTO control.release_arm_incidents" not in writes


def test_gate_closed_still_records_stale_release_as_operator_incident():
    c = _Connection(
        gate=False,
        sessions=[("r1_injusdt", "1.1-micro-live", "INJUSDT", "a" * 40)],
    )
    assert check_release_arm_invariant(c, loaded_commit="b" * 40) is False
    writes = "\n".join(_writes(c))
    assert "INSERT INTO control.release_arm_incidents" in writes
    assert "UPDATE control.execution_gates" not in writes


def test_gate_on_missing_all_live_arm_sessions_fails_closed():
    c = _Connection(gate=True, sessions=[], permissions=[
        ("r1_injusdt", "1.1-micro-live")
    ])
    assert check_release_arm_invariant(c, loaded_commit="b" * 40) is False
    assert "UPDATE control.execution_gates" in "\n".join(_writes(c))


def test_recovery_closes_earlier_incident_not_opens_new_position():
    c = _Connection(gate=False, sessions=[], permissions=[])
    assert check_release_arm_invariant(c, loaded_commit="b" * 40) is False
    writes = "\n".join(_writes(c))
    assert "UPDATE control.release_arm_incidents" in writes
    assert "INSERT INTO control.release_arm_incidents" not in writes
