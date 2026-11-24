"""#865 independent producer release: pure causal simulated cutover/rollback."""
from __future__ import annotations

import importlib
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

if sys.platform != "linux":
    pytest.skip("root-owned systemd component release requires Linux", allow_module_level=True)

package = importlib.import_module(
    "operations.infrastructure.component_release_market_source_stage"
)
rail = importlib.import_module(
    "operations.infrastructure.component_release_market_source_transition"
)


def test_source_old_epoch_must_be_durable_gap_with_ended_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert rail.SOURCE_SHA != rail.LEGACY_SHA
    session = "a" * 32
    monkeypatch.setattr(rail, "_command", lambda *_a, **_kw:
                        json.dumps({"state":"GAP","ended_at":
                                    "2026-11-23T17:00:01+00:00"}))
    rail._prove_old_source_gap(session)
    for state, ended in (("ACTIVE",None),("GAP",None),("STOPPED",None)):
        monkeypatch.setattr(
            rail, "_command", lambda *_a, state=state, ended=ended, **_kw:
            json.dumps({"state":state,"ended_at":ended}),
        )
        with pytest.raises(RuntimeError, match="GAP_NOT_PROVEN"):
            rail._prove_old_source_gap(session)
    with pytest.raises(RuntimeError, match="OLD_EPOCH_IDENTITY_INVALID"):
        rail._prove_old_source_gap("../../unsafe")


def _cutover_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> tuple[dict[str,object], list[str]]:
    root = tmp_path / "component"
    root.mkdir()
    (root / "pinned-market-source.service").write_bytes(b"new-unit")
    old=(
        Path(__file__).resolve().parents[1]
        / "operations/systemd/cripta-native-public-trade-source.service"
    ).read_bytes()
    backup=tmp_path/"old-unit.service"
    backup.write_bytes(old)
    unit=tmp_path/"active-unit.service"
    unit.write_bytes(old)
    state={
        "pid":100,"active":"active",
        "session":"a"*32,"source_commit":rail.LEGACY_SHA,
        "gate":False,"permissions":0,"arms":0,"account_arms":0,
    }
    calls: list[str] = []
    monkeypatch.setattr(rail, "_ROOT",root)
    monkeypatch.setattr(rail, "_ROLLBACK",backup)
    monkeypatch.setattr(rail, "_UNIT_PATH",unit)
    monkeypatch.setattr(rail, "_JOURNAL",tmp_path/"no-existing-journal")
    monkeypatch.setattr(rail.os, "geteuid",lambda:0)
    monkeypatch.setattr(rail.grp,"getgrnam",lambda _n:SimpleNamespace(gr_gid=100))
    monkeypatch.setattr(rail,"_expected_dir",lambda *_a,**_k:None)
    monkeypatch.setattr(rail,"verify_sealed_market_source_controller",lambda:None)
    monkeypatch.setattr(rail,"_source_exact",lambda:b"exact")
    monkeypatch.setattr(rail,"inspect_prepared_market_source",lambda:{})
    monkeypatch.setattr(rail,"_old_unit",lambda:old)
    monkeypatch.setattr(rail,"pinned_market_source_unit",lambda *_a: b"new-unit")
    monkeypatch.setattr(rail,"_exclusive_root_lock",nullcontext)
    monkeypatch.setattr(rail,"_signed_snapshot",lambda:{"proven":True})
    monkeypatch.setattr(rail,"_verify_signed_equal",
                        lambda x,y: (_ for _ in ()).throw(
                            AssertionError("signed changed")) if x!=y else None)
    monkeypatch.setattr(rail,"_neighbors",lambda:{"Entry":(101,0),
                                               "Exit":(102,0),
                                               "Private":(103,0)})
    monkeypatch.setattr(rail,"_read_cr_session",lambda:{
        k:v for k,v in state.items() if k in (
            "session","source_commit","gate","permissions","arms","account_arms"
        )
    })
    monkeypatch.setattr(rail,"_systemd_state",lambda _u:{
        "pid":state["pid"],"active":state["active"],"restarts":0,"sub":"running"
    })
    monkeypatch.setattr(rail,"_prove_old_source_gap",lambda sid:
                        calls.append("DURABLE_GAP:"+sid))
    monkeypatch.setattr(rail,"_append_event",lambda phase,**_kw:
                        calls.append("JOURNAL:"+phase))
    monkeypatch.setattr(rail,"_unit_atomic_replace",lambda blob:
                        (calls.append("UNIT_REPLACE"),unit.write_bytes(blob)))
    monkeypatch.setattr(rail,"_native_ready",lambda _commit,**_kw:{
        "pid":state["pid"],"session":state["session"],
    })
    def systemctl(*args: str, timeout: int=30) -> str:
        assert args[:1]==("/usr/bin/systemctl",)
        operation=args[1]
        calls.append("SYSTEMD:"+operation)
        if operation=="stop":
            assert args[2]==rail._UNIT
            state["active"]="inactive"
            state["pid"]=0
        elif operation=="start":
            assert args[2]==rail._UNIT
            state["active"]="active"
            state["pid"]=200
            state["session"]="b"*32
            state["source_commit"]=rail.SOURCE_SHA
        elif operation=="daemon-reload":
            assert len(args)==2
        else:
            raise AssertionError("UNRELATED_SERVICE_MUTATION")
        return ""
    monkeypatch.setattr(rail,"_command",systemctl)
    return state,calls


def test_scoped_source_only_cutover_keeps_entry_exit_private_and_signed(
    monkeypatch: pytest.MonkeyPatch,tmp_path: Path,
) -> None:
    state,calls=_cutover_fixture(monkeypatch,tmp_path)
    result=rail.cutover_market_source_once()
    assert result["state"]=="MARKET_SOURCE_ONLY_CUTOVER_ACCEPTED"
    assert result["neighbor_units_unchanged"] is True
    assert result["signed_private_protection_unchanged"] is True
    assert result["old_epoch_not_inherited"] is True
    assert state["source_commit"]==package.SOURCE_SHA
    assert calls.count("DURABLE_GAP:"+"a"*32)==1
    assert [x for x in calls if x.startswith("SYSTEMD:")]==[
        "SYSTEMD:stop","SYSTEMD:daemon-reload","SYSTEMD:start",
    ]
    assert calls[-1]=="JOURNAL:ACCEPTED"


def test_scoped_source_cutover_failure_triggers_old_rollback_gate(
    monkeypatch: pytest.MonkeyPatch,tmp_path: Path,
) -> None:
    state,calls=_cutover_fixture(monkeypatch,tmp_path)
    invoked: list[str]=[]
    monkeypatch.setattr(
        rail,"_native_ready",
        lambda *_a,**_k: (_ for _ in ()).throw(RuntimeError("new not ready")),
    )
    monkeypatch.setattr(
        rail,"_unit_rollback_to_old",
        lambda previous_session,previous_pid: invoked.append(
            f"{previous_session}:{previous_pid}"
        ) or True,
    )
    with pytest.raises(RuntimeError,match="RELEASE_FAILED_OLD_SOURCE_RECOVERED"):
        rail.cutover_market_source_once()
    assert invoked==["a"*32+":100"]
    assert "JOURNAL:ACCEPTED" not in calls
    assert "SYSTEMD:start" in calls


def test_no_cutover_if_old_source_or_candidate_unknown(
    monkeypatch: pytest.MonkeyPatch,tmp_path: Path,
) -> None:
    state,calls=_cutover_fixture(monkeypatch,tmp_path)
    state["active"]="failed"
    with pytest.raises(RuntimeError,match="OLD_SOURCE_UNIT_NOT_ACTIVE"):
        rail.cutover_market_source_once()
    assert "SYSTEMD:stop" not in calls
