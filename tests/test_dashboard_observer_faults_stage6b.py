from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from operations.dashboard import app

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 6, 16, 50, tzinfo=UTC)


class RowsCursor:
    def __init__(self, *, rows=None, row=None):
        self._rows = [] if rows is None else rows
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class ReadConnection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, parameters=()):
        self.calls.append((statement, tuple(parameters)))
        return RowsCursor(rows=self.rows)


class ResolveConnection:
    def __init__(self, resolved=True):
        self.resolved = resolved
        self.calls = []

    def execute(self, statement, parameters=()):
        self.calls.append((statement, tuple(parameters)))
        return RowsCursor(row=("observer-fault-1",) if self.resolved else None)


def test_observer_fault_read_model_exposes_unresolved_durable_evidence(monkeypatch) -> None:
    rows = [
        (
            "observer-fault-1",
            "ERROR",
            NOW,
            {
                "service": "cripta-universal-entry-observer.service",
                "source_commit": "a" * 40,
            },
            {
                "first_seen_at": "2026-10-06T16:40:00+00:00",
                "last_seen_at": "2026-10-06T16:49:00+00:00",
                "occurrence_count": 7,
                "error_type": "ValueError",
                "error_message": "forced dashboard test",
                "last_observer_epoch_id": "epoch-7",
                "owner_resolution_required": True,
            },
        )
    ]
    connection = ReadConnection(rows)
    monkeypatch.setattr(app.psycopg, "connect", lambda *_a, **_kw: connection)

    state = app.observer_runtime_fault_state()

    assert state["state"] == "red"
    assert state["count"] == 1
    item = state["items"][0]
    assert item["fault_id"] == "observer-fault-1"
    assert item["occurrence_count"] == 7
    assert item["error_type"] == "ValueError"
    assert item["last_observer_epoch_id"] == "epoch-7"
    assert item["source_commit"] == "a" * 40
    assert connection.calls[0][1] == (app.OBSERVER_RUNTIME_FAULT_CODE,)


def test_observer_fault_escalates_dashboard_health_to_red() -> None:
    merged = app.merge_observer_fault_health(
        {"state": "green", "issues": []},
        {"state": "red", "count": 2, "items": [{}, {}], "error": None},
    )
    assert merged["state"] == "red"
    assert merged["issues"][-1]["code"] == app.OBSERVER_RUNTIME_FAULT_CODE
    assert "2" in merged["issues"][-1]["message"]

    unavailable = app.merge_observer_fault_health(
        {"state": "green", "issues": []},
        {"state": "unavailable", "count": 0, "items": [], "error": "db down"},
    )
    assert unavailable["state"] == "red"
    assert unavailable["issues"][-1]["code"] == "OBSERVER_DURABLE_FAULT_READ_UNAVAILABLE"


def test_explicit_resolve_only_closes_open_observer_fault_and_records_operator() -> None:
    connection = ResolveConnection(resolved=True)

    assert app.resolve_observer_runtime_fault(
        connection,
        fault_id="observer-fault-1",
        reason="проверено владельцем",
        operator="alex",
        resolved_at=NOW,
    )

    statement, params = connection.calls[0]
    assert "state='RESOLVED'" in statement
    assert "fault_code=%s" in statement
    assert "state='OPEN'" in statement
    assert "'last_resolution_reason',%s::text" in statement
    assert "'last_resolved_by',%s::text" in statement
    assert "'last_resolved_at',%s::text" in statement
    assert params[1] == "проверено владельцем"
    assert params[2] == "alex"
    assert params[-2] == "observer-fault-1"
    assert params[-1] == app.OBSERVER_RUNTIME_FAULT_CODE


def test_explicit_resolve_requires_reason() -> None:
    with pytest.raises(ValueError, match="причина resolution обязательна"):
        app.resolve_observer_runtime_fault(
            ResolveConnection(),
            fault_id="observer-fault-1",
            reason="   ",
            operator="alex",
            resolved_at=NOW,
        )


def test_dashboard_global_red_banner_and_resolve_ui_are_non_trading() -> None:
    html = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
    backend = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")

    assert html.index('class="console-event-list"') < html.index('id="observerFaultBanner"')
    assert html.index('id="observerFaultBanner"') < html.index('id="liveCriticalEventList"')
    assert "RED · ошибка Universal Entry observer" in html
    assert "function renderObserverFaults" in html
    assert "async function resolveObserverFault" in html
    assert "Причина явного resolution durable observer fault" in html
    assert "renderObserverFaults(d.observer_faults)" in html
    assert "/api/observer-faults/resolve" in html
    resolve_scope = html[
        html.index("async function resolveObserverFault"):
        html.index("function renderObserverFaults")
    ]
    assert "/api/live/" not in resolve_scope
    assert "tradeCommand(" not in resolve_scope

    assert 'if path == "/api/observer-faults/resolve":' in backend
    endpoint_scope = backend[
        backend.index('if path == "/api/observer-faults/resolve":'):
        backend.index('if path == "/api/lifecycle/fault-delivery/ack":')
    ]
    assert "resolve_observer_runtime_fault(" in endpoint_scope
    assert "runtime.trade_commands" not in endpoint_scope
    assert "execution_permissions" not in endpoint_scope


def test_owner_observer_fault_notification_uses_existing_central_console() -> None:
    html = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
    console = html.index('class="operator-console"')
    events = html.index('class="console-events"', console)
    existing_banner = html.index('id="observerFaultBanner"', events)
    feed = html.index('id="liveSystemEventFeed"', existing_banner)
    assert console < events < existing_banner < feed
    assert 'renderObserverFaults(d.observer_faults)' in html
    assert "const count=Number(x.occurrence_count||1)" in html
    # Visibility requires no per-message acknowledgement.
    body = html[html.index("function renderObserverFaults("):]
    body = body.split("\nfunction ", 1)[0]
    assert "acknowledge_delivery(" not in body
    assert "Открытых проблем:" in body
    assert "observerFaultItems" in body


def test_observer_fault_console_does_not_claim_ui_is_durable_delivery() -> None:
    source = (ROOT / "docs/OBSERVATION_ANALYTICS_RU.md").read_text(
        encoding="utf-8"
    )
    assert "OWNER DECISION 2026-10-15" in source
    assert "подтверждения браузером фактического отображения уведомления" in source


def test_browser_display_receipt_does_not_resolve_observer_fault() -> None:
    from operations.dashboard.app import confirm_observer_faults_displayed

    class Result:
        rowcount = 1

    class Connection:
        def __init__(self):
            self.query = ""
            self.parameters = ()

        def execute(self, query, parameters=()):
            self.query = query
            self.parameters = parameters
            return Result()

    connection = Connection()
    confirmed = confirm_observer_faults_displayed(
        connection,
        fault_ids=["observer-fault-test"],
        operator="operator",
        displayed_at=NOW,
    )
    assert confirmed == 1
    assert "d.channel='DASHBOARD_INBOX'" in connection.query
    assert "f.state='OPEN'" in connection.query
    assert "state='ACKNOWLEDGED'" in connection.query
    assert "d.state IN ('PENDING','ESCALATION_REQUIRED')" in connection.query
    assert "runtime.lifecycle_faults SET state='RESOLVED'" not in connection.query
    assert connection.parameters[-1] == ["observer-fault-test"]


def test_browser_receipt_rejects_invalid_ids_without_db_write() -> None:
    from operations.dashboard.app import confirm_observer_faults_displayed

    class DeniedConnection:
        def execute(self, _query, _params=()):
            raise AssertionError("invalid id must not reach database")

    with pytest.raises(ValueError, match="некорректный fault_id"):
        confirm_observer_faults_displayed(
            DeniedConnection(),
            fault_ids=["arbitrary-fault"],
            operator="operator",
            displayed_at=NOW,
        )


def test_display_receipt_uses_existing_authenticated_dashboard_endpoint() -> None:
    html = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
    backend = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
    assert "document.hidden || banner.getClientRects().length===0" in html
    assert "requestAnimationFrame(()=>" in html
    assert "fetch('/api/observer-faults/displayed'" in html
    assert 'if path == "/api/observer-faults/displayed":' in backend
    assert "confirm_observer_faults_displayed(" in backend
    assert backend.index("if self.require_login():") < backend.index(
        'if path == "/api/observer-faults/displayed":'
    )


def test_full_release_coordinates_exact_dashboard_bundle_and_runtime_cutover() -> None:
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert 'ui_release="$DASHBOARD_UI_ROOT/releases/$RELEASE_COMMIT"' in installer
    assert (
        'git -C "$SOURCE" show "$RELEASE_COMMIT:operations/dashboard/index.html"'
        in installer
    )
    assert (
        'git -C "$SOURCE" show "$RELEASE_COMMIT:operations/dashboard/app.py"'
        in installer
    )
    assert "candidate Dashboard HTML differs from exact Git release" in installer
    assert "candidate Dashboard app differs from exact Git release" in installer
    assert installer.index('mv -Tf "$next_tooling" "$RESEARCH_TOOLING_ROOT/current"') < (
        installer.index('mv -Tf "$next_dashboard" "$DASHBOARD_UI_ROOT/current"')
    )
    assert installer.index('mv -Tf "$next_dashboard" "$DASHBOARD_UI_ROOT/current"') < (
        installer.index('systemctl daemon-reload', installer.index('cutover_started=1'))
    )
    assert (
        'install -o cripta -g cripta -m 0644 "$ui_release/app.py" '
        '"$runtime_release/operations/dashboard/app.py"' in installer
    )


def test_dashboard_verifier_restricts_inbox_receipt_to_nontrading_delivery() -> None:
    verifier = (
        ROOT / "operations/infrastructure/deploy_dashboard_ui.sh"
    ).read_text(encoding="utf-8")
    assert '"/api/observer-faults/displayed"' in verifier
    assert '"confirm_observer_faults_displayed"' in verifier
    assert '"UPDATE runtime.lifecycle_fault_deliveries" not in value' in verifier
    assert "d.channel='DASHBOARD_INBOX'" in verifier
    assert "f.state='OPEN'" in verifier
