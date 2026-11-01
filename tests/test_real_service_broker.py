from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

broker = pytest.importorskip(
    "operations.management.real_service_broker",
    reason="REAL service broker is a POSIX-only production component",
)


def request(*units: str) -> dict[str, object]:
    return {
        "action": "START",
        "request_id": "owner-ready-1234",
        "release_commit": "a" * 40,
        "units": list(units),
    }


def test_allowlist_is_narrow():
    assert broker._is_allowed_unit("cripta-universal-entry-consumer.service")
    assert broker._is_allowed_unit(
        "cripta-lifecycle-account@ta-853ac5fcd58185697396d33cd57459fa.service"
    )
    assert not broker._is_allowed_unit("ssh.service")
    assert not broker._is_allowed_unit("cripta-dashboard@../../evil.service")
    assert not broker._is_allowed_unit("cripta-private-runtime@bad.service")
    assert not broker._is_allowed_unit(
        "cripta-lifecycle-account@ta-" + "1" * 32 + ".service"
    )


def test_broker_accepts_only_dashboard_cgroup(tmp_path) -> None:
    pid = 4321
    cgroup = tmp_path / str(pid)
    cgroup.mkdir()
    (cgroup / "cgroup").write_text(
        "0::/system.slice/cripta-dashboard.service\n",
        encoding="utf-8",
    )
    assert broker._peer_is_dashboard(pid, proc_root=tmp_path)

    (cgroup / "cgroup").write_text(
        "0::/system.slice/cripta-private-runtime.service\n",
        encoding="utf-8",
    )
    assert not broker._peer_is_dashboard(pid, proc_root=tmp_path)
    assert not broker._peer_is_dashboard(0, proc_root=tmp_path)


def test_request_rejects_extra_fields(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(broker, "verify_release", lambda _commit: None)
    payload = request("cripta-private-runtime.service")
    payload["shell"] = "id"
    with pytest.raises(broker.BrokerError, match="REQUEST_FIELDS_INVALID"):
        broker.handle_request(payload)


def test_request_rejects_unapproved_unit_before_systemctl(
    monkeypatch: pytest.MonkeyPatch,
):
    called = False

    def show(_unit: str) -> dict[str, str]:
        nonlocal called
        called = True
        return {"LoadState": "loaded", "ActiveState": "inactive", "FragmentPath": "/x"}

    monkeypatch.setattr(broker, "_systemctl_show", show)
    with pytest.raises(broker.BrokerError, match="UNIT_NOT_ALLOWED"):
        broker.handle_request(request("ssh.service"))
    assert not called


def test_active_unit_is_never_restarted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(broker, "verify_release", lambda _commit: None)
    monkeypatch.setattr(
        broker,
        "_systemctl_show",
        lambda _unit: {
            "LoadState": "loaded",
            "ActiveState": "active",
            "FragmentPath": "/etc/systemd/system/x.service",
        },
    )
    monkeypatch.setattr(
        broker,
        "_start_unit",
        lambda _unit: (_ for _ in ()).throw(AssertionError("must not start")),
    )
    result = broker.handle_request(request("cripta-private-runtime.service"))
    assert result["ok"] is True
    assert result["units"] == [
        {
            "unit": "cripta-private-runtime.service",
            "before": "active",
            "after": "active",
            "started": False,
        }
    ]


def test_failed_or_activating_state_blocks_all_starts(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(broker, "verify_release", lambda _commit: None)
    states = {
        "cripta-private-runtime.service": "inactive",
        "cripta-universal-entry-consumer.service": "failed",
    }

    def show(unit: str) -> dict[str, str]:
        return {
            "LoadState": "loaded",
            "ActiveState": states[unit],
            "FragmentPath": "/etc/systemd/system/" + unit,
        }

    monkeypatch.setattr(broker, "_systemctl_show", show)
    monkeypatch.setattr(
        broker,
        "_start_unit",
        lambda _unit: (_ for _ in ()).throw(AssertionError("preflight must block")),
    )
    with pytest.raises(broker.BrokerError, match="SERVICE_NOT_RECOVERABLE"):
        broker.handle_request(
            request(
                "cripta-private-runtime.service",
                "cripta-universal-entry-consumer.service",
            )
        )


def test_inactive_unit_uses_exact_systemctl_argv(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[list[str], bool]] = []

    def run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((args, bool(kwargs.get("shell", False))))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(broker.subprocess, "run", run)
    monkeypatch.setattr(
        broker,
        "_systemctl_show",
        lambda _unit: {
            "LoadState": "loaded",
            "ActiveState": "active",
            "FragmentPath": "/etc/systemd/system/x.service",
        },
    )
    ok, _detail = broker._start_unit("cripta-private-runtime.service")
    assert ok
    assert calls == [
        (
            ["/usr/bin/systemctl", "start", "cripta-private-runtime.service"],
            False,
        )
    ]


def test_request_deadline_prevents_late_second_start(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(broker, "verify_release", lambda _commit: None)
    monkeypatch.setattr(
        broker,
        "_systemctl_show",
        lambda unit: {
            "LoadState": "loaded",
            "ActiveState": "inactive",
            "FragmentPath": "/etc/systemd/system/" + unit,
        },
    )
    starts: list[str] = []

    def start(unit: str) -> tuple[bool, str]:
        starts.append(unit)
        return True, "active"

    monkeypatch.setattr(broker, "_start_unit", start)
    ticks = iter((0.0, 0.0, 61.0))
    monkeypatch.setattr(broker.time, "monotonic", lambda: next(ticks))
    result = broker.handle_request(
        request(
            "cripta-private-runtime.service",
            "cripta-universal-entry-consumer.service",
        )
    )
    assert result["ok"] is False
    assert result["error"] == "SERVICE_START_DEADLINE_EXCEEDED"
    assert starts == ["cripta-private-runtime.service"]


def test_broker_main_isolates_peer_io_errors() -> None:
    source = broker.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")
    assert "except OSError:" in text
    assert "must not terminate the privileged broker process" in text


def test_release_identity_must_match_both_files(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    installed = tmp_path / "installed"
    runtime = tmp_path / "runtime"
    installed.write_text("a" * 40 + "\n")
    runtime.write_text("b" * 40 + "\n")
    monkeypatch.setattr(broker, "INSTALLED_COMMIT", installed)
    monkeypatch.setattr(broker, "RUNTIME_COMMIT", runtime)
    with pytest.raises(broker.BrokerError, match="RELEASE_IDENTITY_MISMATCH"):
        broker.verify_release("a" * 40)
