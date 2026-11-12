"""R06 protected root management switch: no-order, no-privilege-expansion regressions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from operations.management import r06_pg_mode_switch as switch


def test_config_switch_only_appends_exact_gated_key_and_preserves_other_bytes():
    src = b"API_TOKEN=protected-test-fixture\nSOME_MODE=VALUE\n"
    out = switch._new_config(src)
    assert out.startswith(src)
    assert out[len(src):] == b"CRIPTA_R06_PG_ONLY=ENABLED\n"
    assert out.count(b"CRIPTA_R06_PG_ONLY=") == 1
    assert b"API_TOKEN=protected-test-fixture" in out


@pytest.mark.parametrize("existing", [
    b"CRIPTA_R06_PG_ONLY=ENABLED\n",
    b"CRIPTA_R06_PG_ONLY=DISABLED\n",
    b"  CRIPTA_R06_PG_ONLY = ENABLED\n",
])
def test_existing_mode_never_overwritten_or_duplicated(existing):
    with pytest.raises(switch.R06ModeSwitchBlocked, match="EXISTING_FLAG"):
        switch._new_config(existing)


def test_unknown_source_scope_cannot_manage_other_service():
    with pytest.raises(switch.R06ModeSwitchBlocked, match="SCOPE_NOT_ALLOWED"):
        switch._is_active("cripta-private-runtime.service")


def test_failure_before_signed_source_preflight_has_no_service_or_config_mutation(
    monkeypatch,
):
    actions = []
    monkeypatch.setattr(switch, "_verify_release", lambda: "a" * 40)
    monkeypatch.setattr(switch, "_load_marker", lambda: None)
    monkeypatch.setattr(switch, "_require_units", lambda: 100)
    def deny(_phase):
        raise switch.R06ModeSwitchBlocked("R06_SIGNED_MAIN_SUB_FLAT_MISSING")
    monkeypatch.setattr(switch, "_probe", deny)
    monkeypatch.setattr(
        switch, "_service", lambda action: actions.append(("systemctl", action)),
    )
    monkeypatch.setattr(
        switch, "_atomic", lambda *_args, **_kwargs: actions.append(("write",)),
    )
    with pytest.raises(switch.R06ModeSwitchBlocked, match="SIGNED_MAIN_SUB"):
        switch._cutover()
    assert actions == []


def test_protected_cutover_stops_old_then_verifies_signed_flat_and_starts_new(
    monkeypatch,
):
    marker = []
    events = []
    service = {"active": True, "pid": 100}
    original = b"EXISTING_API_REFERENCE=do-not-log-or-alter\n"
    metadata = SimpleNamespace(st_uid=0, st_gid=109)
    monkeypatch.setattr(switch, "_verify_release", lambda: "a" * 40)
    monkeypatch.setattr(switch, "_load_marker", lambda: None)
    monkeypatch.setattr(switch, "_require_units", lambda: 100)
    monkeypatch.setattr(switch, "_mode_source", lambda: (original, metadata))
    monkeypatch.setattr(
        switch, "_atomic",
        lambda _content, path, **_kw: events.append(("atomic", str(path))),
    )
    monkeypatch.setattr(
        switch, "_write_marker",
        lambda data: marker.append(dict(data)),
    )
    def is_active(unit):
        return service["active"] if unit == switch.OBSERVER else True
    def perform(action):
        events.append(("service", action))
        if action == "stop":
            service.update(active=False, pid=0)
        elif action == "start":
            service.update(active=True, pid=101)
    def probe(mode):
        events.append(("probe", mode))
        return {
            "result": "R06_PHYSICAL_PG_ONLY_NO_ORDER_PASS",
            "release": "a" * 40,
            "source_session": "b" * 32,
            "trading_effect": "NONE",
        }
    monkeypatch.setattr(switch, "_is_active", is_active)
    monkeypatch.setattr(switch, "_service", perform)
    monkeypatch.setattr(switch, "_pid", lambda _unit: service["pid"])
    monkeypatch.setattr(switch, "_probe", probe)
    switch._cutover()
    assert ("probe", "before") in events
    assert events.index(("service", "stop")) < events.index(("probe", "detached"))
    assert events.index(("probe", "detached")) < events.index(("service", "start"))
    assert events.index(("service", "start")) < events.index(("probe", "after"))
    assert marker[0]["state"] == "PREPARED"
    assert marker[-1]["state"] == "ACTIVE"
    assert marker[-1]["new_pid"] == 101
    assert marker[-1]["original_sha"] == switch._sha(original)
    assert "EXISTING_API_REFERENCE" not in str(marker)


def test_forged_previous_switch_prevents_double_cutover(monkeypatch):
    monkeypatch.setattr(switch, "_verify_release", lambda: "a" * 40)
    monkeypatch.setattr(
        switch, "_load_marker", lambda: {"state": "ACTIVE"},
    )
    with pytest.raises(switch.R06ModeSwitchBlocked, match="PREVIOUS_SWITCH"):
        switch._cutover()


def test_auto_rollback_does_not_undo_prior_accepted_active_cutover(
    monkeypatch,
):
    monkeypatch.setattr(switch, "_load_marker", lambda: {"state": "ACTIVE"})
    monkeypatch.setattr(
        switch, "_service",
        lambda _mode: pytest.fail("active cutover must never auto-rollback"),
    )
    switch._rollback_inner(automatic=True)


def test_no_marker_auto_recovery_is_nonmutating(monkeypatch):
    monkeypatch.setattr(switch, "_load_marker", lambda: None)
    monkeypatch.setattr(
        switch, "_service",
        lambda _mode: pytest.fail("no marker must not stop/start service"),
    )
    switch._rollback_inner(automatic=True)


def test_installer_installs_inert_fixed_units_and_root_owned_verified_bytes():
    root = Path(__file__).resolve().parents[1]
    installer = (
        root / "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert "R06_MANAGEMENT_ROOT_BINARY_GIT_BYTES=PASS" in installer
    assert "R06_ROOT_ONLY_SWITCH_RAIL=INSTALLED_INERT" in installer
    for unit in ("cutover", "rollback", "recovery"):
        filename = f"cripta-r06-entry-{unit}.service"
        path = root / "operations/systemd" / filename
        content = path.read_text(encoding="utf-8")
        assert "User=root" in content
        assert "ProtectSystem=strict" in content
        assert "NoNewPrivileges=true" in content
        assert "ReadWritePaths=/etc/cripta " in content
        assert "ExecStart=/usr/bin/python3 /usr/local/libexec/cripta-r06-switch/" in content
        assert "[Install]" not in content
        assert f"operations/systemd/{filename}|{filename}|runtime" in installer
    assert "OnFailure=cripta-r06-entry-recovery.service" in (
        root / "operations/systemd/cripta-r06-entry-cutover.service"
    ).read_text(encoding="utf-8")
    assert "install -o root -g root -m 0755" in installer
    assert "/usr/local/libexec/cripta-r06-switch/r06_pg_mode_switch.py" in installer


def test_rollback_never_accepts_altered_config_or_backup(monkeypatch):
    original = b"ONLY_ONE_SECRET_REFERENCE=unchanged\n"
    altered = b"UNEXPECTED_MALICIOUS_EDIT=yes\n"
    modified = switch._new_config(original)
    marker = {
        "state": "ACTIVE", "release": "a" * 40,
        "original_sha": switch._sha(original),
        "edited_sha": switch._sha(modified),
    }
    backup = SimpleNamespace(
        exists=lambda: True, is_symlink=lambda: False,
        stat=lambda **_kw: SimpleNamespace(st_uid=0, st_mode=0o100600),
        read_bytes=lambda: original,
    )
    monkeypatch.setattr(switch, "_load_marker", lambda: marker)
    monkeypatch.setattr(switch, "_verify_release", lambda: "a" * 40)
    monkeypatch.setattr(switch, "BACKUP", backup)
    monkeypatch.setattr(
        switch, "_mode_source",
        lambda: (altered, SimpleNamespace(st_uid=0, st_gid=109)),
    )
    monkeypatch.setattr(
        switch, "_service",
        lambda _mode: pytest.fail("tampered source must not stop/start"),
    )
    with pytest.raises(switch.R06ModeSwitchBlocked, match="CONCURRENT_CHANGE"):
        switch._rollback_inner(automatic=False)


def test_automatic_rollback_uses_local_off_gate_not_missing_signed_proof(
    monkeypatch,
):
    original = b"R06_UNRELATED_SETTING=unchanged\n"
    modified = switch._new_config(original)
    marker = {
        "state": "FLAG_WRITTEN", "release": "a" * 40,
        "original_sha": switch._sha(original),
        "edited_sha": switch._sha(modified),
    }
    backup = SimpleNamespace(
        exists=lambda: True, is_symlink=lambda: False,
        stat=lambda **_kw: SimpleNamespace(st_uid=0, st_mode=0o100600),
        read_bytes=lambda: original,
    )
    actions = []
    running = {"observer": False, "pid": 0}
    monkeypatch.setattr(switch, "_load_marker", lambda: marker)
    monkeypatch.setattr(switch, "_verify_release", lambda: "a" * 40)
    monkeypatch.setattr(switch, "BACKUP", backup)
    monkeypatch.setattr(
        switch, "_mode_source",
        lambda: (modified, SimpleNamespace(st_uid=0, st_gid=109)),
    )
    monkeypatch.setattr(
        switch, "_is_active",
        lambda unit: running["observer"] if unit == switch.OBSERVER else True,
    )
    monkeypatch.setattr(
        switch, "_pid", lambda _unit: running["pid"],
    )
    def restart(action):
        actions.append(("service", action))
        if action == "start":
            running.update(observer=True, pid=102)
        if action == "stop":
            running.update(observer=False, pid=0)
    monkeypatch.setattr(switch, "_service", restart)
    monkeypatch.setattr(
        switch, "_probe", lambda mode: actions.append(("probe", mode)),
    )
    monkeypatch.setattr(
        switch, "_atomic",
        lambda content, *_args, **_kwargs: actions.append(("restore", content)),
    )
    monkeypatch.setattr(
        switch, "_write_marker",
        lambda state: actions.append(("state", state["state"])),
    )
    switch._rollback_inner(automatic=True)
    assert ("probe", "rollback_local") in actions
    assert ("restore", original) in actions
    assert ("service", "start") in actions
    assert ("state", "ROLLED_BACK") in actions
    assert not any("signed" in str(event).lower() for event in actions)


def test_existing_old_observer_does_not_get_restart_on_early_failure(
    monkeypatch,
):
    original = b"R06_EXISTING=old\n"
    marker = {
        "state": "PREPARED", "release": "a" * 40,
        "original_sha": switch._sha(original),
        "edited_sha": switch._sha(switch._new_config(original)),
    }
    backup = SimpleNamespace(
        exists=lambda: True, is_symlink=lambda: False,
        stat=lambda **_kw: SimpleNamespace(st_uid=0, st_mode=0o100600),
        read_bytes=lambda: original,
    )
    monkeypatch.setattr(switch, "_load_marker", lambda: marker)
    monkeypatch.setattr(switch, "_verify_release", lambda: "a" * 40)
    monkeypatch.setattr(switch, "BACKUP", backup)
    monkeypatch.setattr(
        switch, "_mode_source",
        lambda: (original, SimpleNamespace(st_uid=0, st_gid=109)),
    )
    monkeypatch.setattr(switch, "_is_active", lambda _unit: True)
    monkeypatch.setattr(switch, "_probe", lambda _phase: None)
    monkeypatch.setattr(
        switch, "_service", lambda _action: pytest.fail("do not restart old"),
    )
    monkeypatch.setattr(switch, "_write_marker", lambda _state: None)
    switch._rollback_inner(automatic=True)
    assert marker["state"] == "ROLLED_BACK"


def test_no_switch_if_source_mirror_has_a_different_commit(monkeypatch):
    from operations.management import r06_pg_switch_probe as probe

    class WrongHead:
        returncode = 0
        stdout = "b" * 40 + "\n"

    monkeypatch.setattr(probe.subprocess, "run", lambda *_a, **_kw: WrongHead())
    with pytest.raises(
        probe.R06SwitchPreflightBlocked, match="SOURCE_INSTALLED_HEAD",
    ):
        probe.assert_source_mirror_matches_loaded("a" * 40)


def test_rollback_cannot_reuse_foreign_release_state(monkeypatch):
    monkeypatch.setattr(
        switch, "_load_marker",
        lambda: {"state": "FLAG_WRITTEN", "release": "b" * 40},
    )
    monkeypatch.setattr(switch, "_verify_release", lambda: "a" * 40)
    monkeypatch.setattr(
        switch, "_service",
        lambda _mode: pytest.fail("wrong release cannot mutate Observer"),
    )
    with pytest.raises(switch.R06ModeSwitchBlocked, match="RELEASE_LINEAGE"):
        switch._rollback_inner(automatic=True)




def test_cri_probe_is_fixed_systemd_user_and_never_switches_uid():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    unit = (
        root / "operations/systemd"
        / "cripta-r06-entry-preflight@.service"
    ).read_text(encoding="utf-8")
    assert "User=cripta" in unit
    assert "Group=cripta" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "StandardOutput=journal" in unit
    assert "StandardError=null" in unit
    assert "[Install]" not in unit
    assert "runuser" not in unit
    manager = (
        root / "operations/management/r06_pg_mode_switch.py"
    ).read_text(encoding="utf-8")
    assert '"/usr/bin/systemctl", "start", unit' in manager
    assert "user=identity.pw_uid" not in manager
    assert '"/usr/bin/journalctl"' in manager
    assert (
        "operations/systemd/cripta-r06-entry-preflight@.service"
    ) in (
        root / "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")


def test_probe_exact_actor_attested_systemd_invocation_and_journal(
    monkeypatch,
):
    from types import SimpleNamespace

    phase = "before"
    invocation = "b" * 32
    record = {
        "result": "R06_PG_SWITCH_PREFLIGHT_PASS", "mode": phase,
        "release": "a" * 40, "trading_effect": "NONE",
        "unit_invocation_id": invocation,
    }
    seen = []
    monkeypatch.setattr(switch.time, "monotonic_ns", lambda: 1_000_000)
    monkeypatch.setattr(
        switch.RELEASE.__class__, "read_text",
        lambda _self, **_kw: "a" * 40 + "\n",
    )
    def fake_call(*args, **_kwargs):
        seen.append(args)
        if args[1] == "start":
            assert args[2] == "cripta-r06-entry-preflight@before.service"
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0] == "/usr/bin/systemctl":
            output = "\n".join([
                "User=cripta", "Group=cripta", "NoNewPrivileges=yes",
                "FragmentPath=/etc/systemd/system/cripta-r06-entry-preflight@.service",
                f"InvocationID={invocation}", "ExecMainStatus=0",
                "ExecMainStartTimestampMonotonic=2000",
                "Result=success", "ActiveState=inactive",
                "ExecStart=/srv/cripta/runtime/current/.venv/bin/python "
                "/srv/cripta/runtime/current/operations/management/"
                "r06_pg_switch_probe.py before",
            ])
            return SimpleNamespace(returncode=0, stdout=output)
        if args[0] == "/usr/bin/journalctl":
            entry = {
                "_SYSTEMD_UNIT": "cripta-r06-entry-preflight@before.service",
                "MESSAGE": json.dumps(record),
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(entry))
        raise AssertionError(args)
    monkeypatch.setattr(switch, "_call", fake_call)
    assert switch._probe(phase) == record
    assert len(seen) == 3


def test_probe_refuses_incorrect_unix_actor_without_phase_effect(
    monkeypatch,
):
    from types import SimpleNamespace

    monkeypatch.setattr(switch.time, "monotonic_ns", lambda: 1_000_000)
    def fake_call(*args, **_kwargs):
        if args[1] == "start":
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(
            returncode=0, stdout="\n".join([
                "User=root", "Group=root", "NoNewPrivileges=yes",
                "FragmentPath=/etc/systemd/system/cripta-r06-entry-preflight@.service",
                "InvocationID=" + "b"*32,
                "ExecMainStatus=0", "ExecMainStartTimestampMonotonic=2000",
                "Result=success", "ActiveState=inactive",
                "ExecStart=/srv/cripta/runtime/current/.venv/bin/python "
                "/srv/cripta/runtime/current/operations/management/"
                "r06_pg_switch_probe.py before",
            ])
        )
    monkeypatch.setattr(switch, "_call", fake_call)
    with pytest.raises(
        switch.R06ModeSwitchBlocked, match="CRIPTA_ACTOR_OR_INVOCATION",
    ):
        switch._probe("before")


def test_probe_bad_or_stale_journal_never_passes(
    monkeypatch,
):
    from types import SimpleNamespace

    monkeypatch.setattr(switch.time, "monotonic_ns", lambda: 1_000_000)
    def fake_call(*args, **_kwargs):
        if args[1] == "start":
            return SimpleNamespace(returncode=0, stdout="")
        if args[0] == "/usr/bin/systemctl":
            return SimpleNamespace(returncode=0, stdout="\n".join([
                "User=cripta", "Group=cripta", "NoNewPrivileges=yes",
                "FragmentPath=/etc/systemd/system/cripta-r06-entry-preflight@.service",
                "InvocationID=" + "b"*32,
                "ExecMainStatus=0", "ExecMainStartTimestampMonotonic=2000",
                "Result=success", "ActiveState=inactive",
                "ExecStart=/srv/cripta/runtime/current/.venv/bin/python "
                "/srv/cripta/runtime/current/operations/management/"
                "r06_pg_switch_probe.py before",
            ]))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "_SYSTEMD_UNIT": "cripta-r06-entry-preflight@before.service",
                "MESSAGE": json.dumps({
                    "result": "R06_PG_SWITCH_PREFLIGHT_PASS",
                    "mode": "before", "unit_invocation_id": "old"*10+"00",
                    "trading_effect": "NONE",
                }),
            }),
        )
    monkeypatch.setattr(switch, "_call", fake_call)
    with pytest.raises(
        switch.R06ModeSwitchBlocked,
        match="CURRENT_INVOCATION_RESULT_NOT_ATTESTED",
    ):
        switch._probe("before")


def test_read_only_probe_rejects_wrong_unix_actor_before_db(monkeypatch):
    from types import SimpleNamespace

    from operations.management import r06_pg_switch_probe as probe

    pwd = pytest.importorskip("pwd")
    monkeypatch.setattr(
        pwd, "getpwnam",
        lambda _name: SimpleNamespace(pw_uid=107, pw_gid=109),
    )
    monkeypatch.setattr(probe.os, "getuid", lambda: 0)
    with pytest.raises(
        probe.R06SwitchPreflightBlocked,
        match="R06_PROBE_WRONG_UNIX_CRIPTA_ACTOR",
    ):
        probe.verify("before")


def test_read_only_probe_journal_errors_redact_provider_details(monkeypatch):
    from operations.management import r06_pg_switch_probe as probe

    def denied(_phase):
        raise RuntimeError("API_SECRET=DO_NOT_LOG")

    monkeypatch.setattr(probe, "verify", denied)
    monkeypatch.setattr(probe.sys, "argv", ["prog", "before"])
    assert probe.main() == 2

