"""#865 independent MarketSource ONE UNIT cutover; no Entry/Exit/ARM writes.

Root-only sealed no-argument management action. Package staging is separate.
The old source session must remain a real GAP, not be joined to the new one.
Expected old/live release, exact artifact and root ACL are immutable evidence.
"""
from __future__ import annotations

import contextlib
import fcntl
import grp
import json
import os
import re
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from component_release_market_source_stage import (
    LEGACY_SHA, OLD_UNIT_SHA, SOURCE_SHA, _OWNER, _ROOT, _UNIT_PATH,
    _digest, _deny, _expected_dir, _old_unit, _source_exact,
    inspect_prepared_market_source, pinned_market_source_unit,
)

_CONTROLLER = _OWNER / ".owner-controller-865"
_JOURNAL = _OWNER / "journal" / ("market-source-" + SOURCE_SHA + ".jsonl")
_ROLLBACK = _OWNER / "rollback" / ("old-unit-" + OLD_UNIT_SHA + ".service")
_LOCK = Path("/run/lock/cripta-install-verified-release.lock")
_UNIT = "cripta-native-public-trade-source.service"
_STATUS = Path("/var/lib/cripta/native_public_trade_source/status.json")
_OLD_CODE = Path("/srv/cripta/runtime/releases") / LEGACY_SHA
_SIGNED_OWNER = Path("/srv/cripta/components/exit/releases/.owner-controller-849-v3")
_NEIGHBORS = (
    "cripta-universal-entry-observer.service",
    "cripta-universal-entry-consumer.service",
    "cripta-universal-exit-consumer.service",
    "cripta-private-runtime.service",
)
_SOURCE_PROBE = r'''
import json
from datetime import UTC,datetime,timedelta
import psycopg
with psycopg.connect(
    "dbname=cripta user=cripta host=/var/run/postgresql connect_timeout=5 "
    "options='-c default_transaction_read_only=on'",
) as c:
    c.execute("SET LOCAL statement_timeout='7000ms'")
    r=c.execute("""SELECT source_session_id,producer_release_commit,state,
                          subscribed_at,last_accepted_at
                   FROM analytics.native_public_trade_source_sessions
                   WHERE market_source_id='ms-bybit-mainnet-public-v1'
                     AND state='ACTIVE'""").fetchall()
    if len(r)!=1:raise RuntimeError("MARKET_SOURCE_ACTIVE_EPOCH_UNVERIFIED")
    session,commit,state,opened,last=r[0]
    symbols=c.execute("""SELECT external_symbol,accepted_at
        FROM analytics.native_public_trade_source_cursors
        WHERE source_session_id=%s
        ORDER BY external_symbol""",(session,)).fetchall()
    gate=c.execute("""SELECT enabled FROM control.execution_gates
                       WHERE mode='mainnet'""").fetchone()
    permissions=c.execute("""SELECT count(*) FROM
                 strategy_entry.execution_permissions WHERE enabled=true""").fetchone()
    arms=c.execute("""SELECT count(*) FROM control.live_arm_sessions
                       WHERE state='ACTIVE'""").fetchone()
    account_arms=c.execute("""SELECT count(*) FROM
                control.account_live_arm_sessions WHERE state='ACTIVE'""").fetchone()
    print(json.dumps({
       "session":session,"source_commit":commit,"state":state,
       "opened_at":opened.isoformat(),"last_accepted_at":(
           last.isoformat() if last is not None else None),
       "symbol_accepted":{s:(t.isoformat() if t else None) for s,t in symbols},
       "gate":gate[0] if gate else None,
       "permissions":permissions[0] if permissions else None,
       "arms":arms[0] if arms else None,
       "account_arms":account_arms[0] if account_arms else None,
    },sort_keys=True))
'''
def _command(*args: str, timeout: int = 30) -> str:
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout,
        env={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin",
             "LANG":"C", "LC_ALL":"C"},
    )
    if result.returncode:
        _deny("MANAGEMENT_COMMAND_FAILED")
    return result.stdout.strip()


def _read_cr_session() -> dict[str, object]:
    argv = [
        "/usr/sbin/runuser", "-u", "cripta", "--", "/usr/bin/env",
        "PYTHONDONTWRITEBYTECODE=1", "PYTHONPATH=" + str(_OLD_CODE / "src"),
        str(_OLD_CODE / ".venv/bin/python"), "-B", "-c", _SOURCE_PROBE,
    ]
    result = json.loads(_command(*argv,timeout=20))
    if (not isinstance(result,dict) or
        not isinstance(result.get("session"),str) or
        re.fullmatch(r"[0-9a-f]{32}",result["session"]) is None or
        result.get("source_commit") not in (LEGACY_SHA,SOURCE_SHA) or
        result.get("state")!="ACTIVE" or
        not isinstance(result.get("symbol_accepted"),dict)):
        _deny("PG_NATIVE_SESSION_IDENTITY_UNVERIFIED")
    return result


_GAP_PROBE = """
import json,sys,psycopg
with psycopg.connect(
    "dbname=cripta user=cripta host=/var/run/postgresql connect_timeout=5 "
    "options='-c default_transaction_read_only=on'",
) as c:
    row=c.execute(
        "SELECT state,ended_at FROM analytics.native_public_trade_source_sessions "
        "WHERE source_session_id=%s",(sys.argv[1],),
    ).fetchone()
    print(json.dumps({
        "state":row[0] if row else None,
        "ended_at":row[1].isoformat() if row and row[1] else None,
    }))
"""


def _prove_old_source_gap(session: str) -> None:
    if re.fullmatch(r"[a-f0-9]{32}",session) is None:
        _deny("OLD_EPOCH_IDENTITY_INVALID")
    result=json.loads(_command(
        "/usr/sbin/runuser", "-u", "cripta", "--", "/usr/bin/env",
        "PYTHONDONTWRITEBYTECODE=1",
        str(_OLD_CODE / ".venv/bin/python"), "-B",
        "-c", _GAP_PROBE, session, timeout=20,
    ))
    if result != {"state":"GAP","ended_at":result.get("ended_at")} or not isinstance(result.get("ended_at"),str):
        _deny("OLD_SOURCE_SESSION_GAP_NOT_PROVEN")


def _signed_snapshot() -> dict[str, object]:
    # The attested Exit root controller is intentionally NOT readable by the
    # unprivileged cripta actor. Its own signed GET helper delegates private
    # read-only work to cripta and returns a sanitized in-memory snapshot.
    sys.path.insert(0,str(_SIGNED_OWNER))
    try:
        from component_release_exit_transition import (
            _check_sealed_controller_root,
            _collect_credential_local_exit_snapshot,
        )
        _check_sealed_controller_root()
        result = _collect_credential_local_exit_snapshot()
    finally:
        sys.path.pop(0)
    if not isinstance(result,dict):
        _deny("SIGNED_PRIVATE_SNAPSHOT_UNVERIFIED")
    return result

def _verify_signed_equal(before: dict[str,object], after: dict[str,object]) -> None:
    # Use the already-reviewed Exit owner comparison, not a new fake parity.
    sys.path.insert(0,str(_SIGNED_OWNER))
    try:
        from component_release_exit_signed_preflight import _compare_recovery_snapshots
        verdict = _compare_recovery_snapshots(before=before, after=after)
    finally:
        sys.path.pop(0)
    if (verdict.get("state") !=
            "SIGNED_EXISTING_POSITION_AND_PROTECTION_PRESERVED_READ_ONLY"
        or verdict.get("signed_protection_snapshot_unchanged") is not True):
        _deny("SIGNED_EXCHANGE_PROTECTION_CHANGED")


def _systemd_state(unit: str) -> dict[str,object]:
    raw=_command("/usr/bin/systemctl","show",unit,
                 "-p","MainPID","-p","NRestarts","-p","ActiveState",
                 "-p","SubState","-p","ExecMainStartTimestamp",timeout=10)
    data=dict(row.split("=",1) for row in raw.splitlines() if "=" in row)
    try:
        pid=int(data["MainPID"])
        restarts=int(data["NRestarts"])
    except (KeyError,ValueError) as exc:
        raise RuntimeError("MARKET_SOURCE_SYSTEMD_IDENTITY_UNVERIFIED") from exc
    return {
        "pid":pid,"restarts":restarts,"active":data.get("ActiveState"),
        "sub":data.get("SubState"),
    }


def _neighbors() -> dict[str,dict[str,object]]:
    return {name:_systemd_state(name) for name in _NEIGHBORS}


def _write_file_exact(path: Path, contents: bytes, mode: int) -> None:
    if path.exists() or path.is_symlink():
        _deny("PROTECTED_OUTPUT_ALREADY_EXISTS")
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    try:
        os.write(fd,contents)
        os.fchmod(fd,mode)
        os.fchown(fd,0,0)
        os.fsync(fd)
    finally:os.close(fd)


def _append_event(phase: str, **fields: object) -> None:
    # Only fixed event names, never log secrets or raw signed account details.
    allowed = frozenset({
        "BASELINE","OLD_STOP_REQUESTED","OLD_STOPPED_GAP",
        "UNIT_CANDIDATE_REPLACED","NEW_START_REQUESTED",
        "NEW_PROCESS_PROVEN","NEW_SIGNED_PROTECTION_PRESERVED",
        "NEIGHBORS_AND_GATE_PRESERVED","ACCEPTED",
        "ROLLBACK_REQUESTED","ROLLBACK_OLD_UNIT_RESTORED",
        "ROLLBACK_OLD_PROCESS_RECOVERED","ROLLBACK_UNKNOWN",
    })
    if phase not in allowed:
        _deny("JOURNAL_PHASE_NOT_APPROVED")
    entry={"phase":phase,"at":datetime.now(UTC).isoformat(),"component":SOURCE_SHA,
           **fields}
    fd=os.open(_JOURNAL,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        if (st.st_uid!=0 or not stat.S_ISREG(st.st_mode)
                or stat.S_IMODE(st.st_mode)!=0o600):
            _deny("JOURNAL_OWNER_UNVERIFIED")
        os.write(fd,(json.dumps(entry,sort_keys=True,separators=(",",":"))+"\n").encode())
        os.fsync(fd)
    finally:os.close(fd)


def _unit_atomic_replace(new: bytes) -> None:
    if not _UNIT_PATH.is_file() or _UNIT_PATH.is_symlink():
        _deny("UNIT_PATH_NOT_REGULAR")
    st=_UNIT_PATH.lstat()
    if st.st_uid!=0 or st.st_gid!=0 or stat.S_IMODE(st.st_mode)!=0o644:
        _deny("UNIT_OWNER_OR_MODE_DRIFT")
    scratch=_UNIT_PATH.with_name(".cripta-market-source-865-pending.service")
    _write_file_exact(scratch,new,0o644)
    try:
        os.replace(scratch,_UNIT_PATH)
        dirfd=os.open(_UNIT_PATH.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
    finally:
        if scratch.exists():scratch.unlink()


def _status_current(expected_commit: str, *, previous_session: str,
                    new_pid: int) -> dict[str,object]:
    status=json.loads(_STATUS.read_text())
    actual=_systemd_state(_UNIT)
    if (actual["active"]!="active" or actual["pid"]<=0
        or actual["pid"]==new_pid
        or status.get("state")!="ACTIVE"
        or status.get("source_commit")!=expected_commit
        or not re.fullmatch(r"[0-9a-f]{32}",str(status.get("session") or ""))
        or status["session"]==previous_session):
        _deny("NEW_LOADED_PROCESS_OR_SOURCE_SESSION_UNVERIFIED")
    cwd=Path("/proc")/str(actual["pid"])/"cwd"
    target=_ROOT if expected_commit==SOURCE_SHA else _OLD_CODE
    if cwd.resolve()!=target:
        _deny("PROCESS_CWD_NOT_PINNED")
    data=(Path("/proc")/str(actual["pid"])/"cmdline").read_bytes().split(b"\x00")
    executable=(target/"operations/monitoring/native_public_trade_source.py")
    loaded_source = (
        Path(item.decode("utf-8")).resolve()
        for item in data if item and item.endswith(b"/native_public_trade_source.py")
    )
    if not any(path == executable for path in loaded_source):
        _deny("PROCESS_EXECUTABLE_SOURCE_NOT_PINNED")
    return {"pid":actual["pid"],"session":status["session"]}


def _native_ready(
    expected_commit: str, *, before_session: str, before_pid: int,
) -> dict[str,object]:
    end=time.monotonic()+45
    while time.monotonic()<end:
        try:
            loaded=_status_current(expected_commit,
                                   previous_session=before_session,
                                   new_pid=before_pid)
            state=_read_cr_session()
            if (state["session"]==loaded["session"]
                and state["source_commit"]==expected_commit
                and set(state["symbol_accepted"]) ==
                  {"APTUSDT","ARBUSDT","DOTUSDT","INJUSDT","LTCUSDT"}
                and all(state["symbol_accepted"].values())):
                at=datetime.fromisoformat(state["last_accepted_at"])
                if timedelta(seconds=-2)<=datetime.now(UTC)-at<timedelta(seconds=100):
                    return loaded
        except (OSError,KeyError,ValueError,RuntimeError):
            pass
        time.sleep(1)
    _deny("POSTCUTOVER_FIVE_SYMBOL_NATIVE_LIVENESS_NOT_VERIFIED")


@contextlib.contextmanager
def _exclusive_root_lock():
    fd=os.open(_LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=0:
            _deny("GLOBAL_RELEASE_LOCK_OWNER_DRIFT")
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            _deny("RELEASE_ALREADY_OWNED")
        yield
    finally:
        with contextlib.suppress(OSError):fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)


def _unit_rollback_to_old(previous_session: str, previous_pid: int) -> bool:
    try:
        _append_event("ROLLBACK_REQUESTED")
        _command("/usr/bin/systemctl","stop",_UNIT,timeout=30)
        old=_ROLLBACK.read_bytes()
        if _digest(old)!=OLD_UNIT_SHA:
            _deny("OLD_ROLLBACK_BYTES_DRIFT")
        _unit_atomic_replace(old)
        _command("/usr/bin/systemctl","daemon-reload",timeout=20)
        _append_event("ROLLBACK_OLD_UNIT_RESTORED")
        _command("/usr/bin/systemctl","start",_UNIT,timeout=30)
        observed=_native_ready(LEGACY_SHA,before_session=previous_session,
                               before_pid=previous_pid)
        if _digest(_UNIT_PATH.read_bytes())!=OLD_UNIT_SHA:
            _deny("OLD_UNIT_NOT_RESTORED")
        _append_event("ROLLBACK_OLD_PROCESS_RECOVERED",
                      current_pid=observed["pid"])
        return True
    except Exception:
        _append_event("ROLLBACK_UNKNOWN")
        return False


def cutover_market_source_once() -> dict[str,object]:
    """Single authentic root cutover, no args, no trading or other unit writes."""
    if os.geteuid()!=0 or sys.platform!="linux":
        _deny("ROOT_LINUX_REQUIRED")
    gid=grp.getgrnam("cripta").gr_gid
    _expected_dir(_OWNER,uid=0,gid=gid,mode=0o750)
    _expected_dir(_OWNER/"journal",uid=0,gid=gid,mode=0o750)
    _expected_dir(_OWNER/"rollback",uid=0,gid=gid,mode=0o750)
    _expected_dir(_CONTROLLER,uid=0,gid=gid,mode=0o550)
    with _exclusive_root_lock():
        if _JOURNAL.exists():
            _deny("PREVIOUS_RELEASE_ATTEMPT_EXISTS_RECONCILE_FIRST")
        _source_exact()
        inspect_prepared_market_source()
        old=_old_unit()
        new=pinned_market_source_unit(old)
        if _digest(new)!=_digest((_ROOT/"pinned-market-source.service").read_bytes()):
            _deny("PINNED_UNIT_NOT_PROVEN")
        baseline=_systemd_state(_UNIT)
        if baseline["active"]!="active" or baseline["pid"]<=0:
            _deny("OLD_SOURCE_UNIT_NOT_ACTIVE")
        source=_read_cr_session()
        if source["source_commit"]!=LEGACY_SHA:
            _deny("OLD_SOURCE_COMMIT_DRIFT")
        before_neighbors=_neighbors()
        signed=_signed_snapshot()
        if not _ROLLBACK.exists():
            _write_file_exact(_ROLLBACK,old,0o440)
        elif _digest(_ROLLBACK.read_bytes())!=OLD_UNIT_SHA:
            _deny("OLD_ROLLBACK_DRIFT")
        _append_event("BASELINE",old_pid=baseline["pid"],
                      old_session=source["session"],
                      unit_sha=OLD_UNIT_SHA)
        installed=False
        try:
            _append_event("OLD_STOP_REQUESTED")
            _command("/usr/bin/systemctl","stop",_UNIT,timeout=30)
            old_result=_systemd_state(_UNIT)
            if old_result["active"]=="active" or old_result["pid"]!=0:
                _deny("OLD_SOURCE_NOT_STOPPED")
            # Native source stop marks a real GAP. Never replace an ACTIVE
            # session's continuity with the future producer's cursor.
            _prove_old_source_gap(str(source["session"]))
            _append_event("OLD_STOPPED_GAP")
            _unit_atomic_replace(new)
            installed=True
            _append_event("UNIT_CANDIDATE_REPLACED")
            _command("/usr/bin/systemctl","daemon-reload",timeout=20)
            _append_event("NEW_START_REQUESTED")
            _command("/usr/bin/systemctl","start",_UNIT,timeout=30)
            current=_native_ready(SOURCE_SHA,
                                  before_session=source["session"],
                                  before_pid=baseline["pid"])
            _append_event("NEW_PROCESS_PROVEN",new_pid=current["pid"],
                          new_session=current["session"])
            _verify_signed_equal(signed,_signed_snapshot())
            _append_event("NEW_SIGNED_PROTECTION_PRESERVED")
            after=_read_cr_session()
            before_tuple={k:source.get(k) for k in
                          ("gate","permissions","arms","account_arms")}
            after_tuple={k:after.get(k) for k in
                         ("gate","permissions","arms","account_arms")}
            if before_tuple!=after_tuple or _neighbors()!=before_neighbors:
                _deny("FINANCIAL_OR_NEIGHBOR_CHANGED")
            _append_event("NEIGHBORS_AND_GATE_PRESERVED")
            if _digest(_UNIT_PATH.read_bytes())!=_digest(new):
                _deny("ACTIVE_UNIT_HASH_DRIFT")
            _append_event("ACCEPTED",new_pid=current["pid"])
            return {
                "state":"MARKET_SOURCE_ONLY_CUTOVER_ACCEPTED",
                "source_commit":SOURCE_SHA,
                "new_pid":current["pid"],
                "neighbor_units_unchanged":True,
                "signed_private_protection_unchanged":True,
                "old_epoch_not_inherited":True,
            }
        except Exception:
            recovered=_unit_rollback_to_old(
                previous_session=source["session"],
                previous_pid=baseline["pid"],
            ) if installed or _systemd_state(_UNIT)["active"]!="active" else True
            if not recovered:
                _deny("RELEASE_FAILED_ROLLBACK_UNKNOWN")
            _deny("RELEASE_FAILED_OLD_SOURCE_RECOVERED")


if __name__ == "__main__":
    if (len(sys.argv)!=1 or
        Path(sys.argv[0])!=_CONTROLLER/"component_release_market_source_transition.py"):
        raise SystemExit("MARKET_SOURCE_ROOT_CONTROLLER_NO_ARGUMENTS_REQUIRED")
    print(json.dumps(cutover_market_source_once(),sort_keys=True))
