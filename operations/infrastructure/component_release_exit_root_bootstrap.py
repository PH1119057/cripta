"""#849: bounded root-owned Exit component path bootstrap (NOT deployment).

Production-facing API: inspect_exit_root_tree() (READ-only) and
bootstrap_exit_root_once() (root-only, NO ARGUMENTS and NO CLI).
Only a separately authorized OS preparation step may invoke bootstrap.
The installed Release Controller's strict sandbox cannot create the first
missing /srv/cripta/components from its optional ReadWritePaths alone.

No Git, systemd, SQL, network, Exchange, service/unit/ARM, or artifact writes.
An actual component install, immutable rollback and signed protection have
SEPARATE gates. Private alternate-anchor helpers are disposable tests only.
"""
from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

_ANCHOR = Path("/srv/cripta")
_SHARED_LOCK = Path("/run/lock/cripta-install-verified-release.lock")
_COMPONENTS = ("components", "exit", "releases", "rollback")
_TREE = (
    "components",
    "components/exit",
    "components/exit/releases",
    "components/exit/rollback",
)
_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_EXPECTED_ANCHOR_MODE = 0o755
_EXPECTED_CHILD_MODE = 0o750


def _deny(code: str) -> None:
    raise ValueError("EXIT_ROOT_" + code)


def _check_dir(
    fd: int, *, uid: int, gid: int, mode: int, dev: int | None = None,
) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != uid or info.st_gid != gid
        or stat.S_IMODE(info.st_mode) != mode
        or (dev is not None and info.st_dev != dev)
    ):
        _deny("OWNER_MODE_OR_MOUNT_INVALID")
    # ACL and default ACL may bypass the expected simple DAC surface.
    # Fail closed rather than silently rewriting any existing directory.
    for key in ("system.posix_acl_access", "system.posix_acl_default"):
        try:
            os.getxattr(fd, key)
        except OSError as exc:
            if exc.errno == errno.ENODATA:
                continue
            raise ValueError("EXIT_ROOT_ACL_NOT_VERIFIED") from exc
        else:
            _deny("UNREVIEWED_ACL")


def _child_fd(parent_fd: int, name: str) -> int | None:
    try:
        return os.open(name, _FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("EXIT_ROOT_EXISTING_PATH_UNTRUSTED") from exc


def _snapshot(
    anchor_fd: int, *, anchor_uid: int, anchor_gid: int,
    components_uid: int, components_gid: int,
) -> dict[str, object]:
    _check_dir(
        anchor_fd, uid=anchor_uid, gid=anchor_gid,
        mode=_EXPECTED_ANCHOR_MODE,
    )
    dev = os.fstat(anchor_fd).st_dev
    with ExitStack() as opened:
        comp = _child_fd(anchor_fd, _COMPONENTS[0])
        if comp is None:
            missing = list(_TREE)
        else:
            opened.callback(os.close, comp)
            _check_dir(
                comp, uid=components_uid, gid=components_gid,
                mode=_EXPECTED_CHILD_MODE, dev=dev,
            )
            exit_fd = _child_fd(comp, _COMPONENTS[1])
            if exit_fd is None:
                missing = list(_TREE[1:])
            else:
                opened.callback(os.close, exit_fd)
                _check_dir(
                    exit_fd, uid=components_uid, gid=components_gid,
                    mode=_EXPECTED_CHILD_MODE, dev=dev,
                )
                missing = []
                for i, name in enumerate(_COMPONENTS[2:], start=2):
                    child = _child_fd(exit_fd, name)
                    if child is None:
                        missing.append(_TREE[i])
                    else:
                        opened.callback(os.close, child)
                        _check_dir(
                            child, uid=components_uid, gid=components_gid,
                            mode=_EXPECTED_CHILD_MODE, dev=dev,
                        )
    if not missing:
        state = "ROOT_TREE_PREPARED_METADATA_ONLY"
    elif len(missing) == len(_TREE):
        state = "ROOT_TREE_NEEDS_FIRST_BOOTSTRAP"
    else:
        state = "ROOT_TREE_PARTIAL_BLOCKED"
    return {
        "state": state,
        "missing_paths": missing,
        "install_authorized": False,
        "rollback_ready": False,
        "live_cutover_authorized": False,
        "financial_mutation_authorized": False,
        "service_restart_authorized": False,
        "runtime_behavior_verified": False,
    }


def _make_child(
    parent_fd: int, name: str, *, uid: int, gid: int, dev: int,
) -> int:
    try:
        # Private until ownership and final group-readable mode are proven.
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        fd = os.open(name, _FLAGS, dir_fd=parent_fd)
        try:
            os.fchown(fd, uid, gid)
            os.fchmod(fd, _EXPECTED_CHILD_MODE)
            _check_dir(
                fd, uid=uid, gid=gid, mode=_EXPECTED_CHILD_MODE, dev=dev,
            )
            # Persist each new directory's owner/mode before its parent
            # entry; a crash must not masquerade as a complete bootstrap.
            os.fsync(fd)
            os.fsync(parent_fd)
            return fd
        except BaseException:
            os.close(fd)
            raise
    except OSError as exc:
        # A partial created tree remains for manual outcome reconciliation.
        # Do not remove it or change any existing owner/mode opportunistically.
        raise ValueError("EXIT_ROOT_BOOTSTRAP_OUTCOME_UNKNOWN") from exc


def _bootstrap_from_fd(
    anchor_fd: int, *, anchor_uid: int, anchor_gid: int,
    components_uid: int, components_gid: int,
) -> dict[str, object]:
    first = _snapshot(
        anchor_fd, anchor_uid=anchor_uid, anchor_gid=anchor_gid,
        components_uid=components_uid, components_gid=components_gid,
    )
    if first["state"] == "ROOT_TREE_PREPARED_METADATA_ONLY":
        return {
            **first, "state": "ROOT_TREE_ALREADY_PREPARED_METADATA_ONLY",
            "created_paths": [],
        }
    if first["state"] != "ROOT_TREE_NEEDS_FIRST_BOOTSTRAP":
        _deny("ROOT_TREE_PARTIAL_BLOCKED")
    dev = os.fstat(anchor_fd).st_dev
    with ExitStack() as opened:
        comp = _make_child(
            anchor_fd, "components", uid=components_uid,
            gid=components_gid, dev=dev,
        )
        opened.callback(os.close, comp)
        exit_fd = _make_child(
            comp, "exit", uid=components_uid, gid=components_gid, dev=dev,
        )
        opened.callback(os.close, exit_fd)
        for name in ("releases", "rollback"):
            child = _make_child(
                exit_fd, name, uid=components_uid, gid=components_gid,
                dev=dev,
            )
            opened.callback(os.close, child)
        result = _snapshot(
            anchor_fd, anchor_uid=anchor_uid, anchor_gid=anchor_gid,
            components_uid=components_uid, components_gid=components_gid,
        )
        if result["state"] != "ROOT_TREE_PREPARED_METADATA_ONLY":
            _deny("POSTCREATE_METADATA_NOT_VERIFIED")
    return {**result, "created_paths": list(_TREE)}


def _inspect_under_verified_anchor(
    anchor: Path, *, anchor_uid: int, anchor_gid: int,
    components_uid: int, components_gid: int,
) -> dict[str, object]:
    """Private disposable-fixture adapter; never trusted prod proof."""
    if not isinstance(anchor, Path) or not anchor.is_absolute():
        _deny("ANCHOR_NOT_ABSOLUTE")
    try:
        with ExitStack() as opened:
            fd = os.open(anchor, _FLAGS)
            opened.callback(os.close, fd)
            return _snapshot(
                fd, anchor_uid=anchor_uid, anchor_gid=anchor_gid,
                components_uid=components_uid, components_gid=components_gid,
            )
    except OSError as exc:
        raise ValueError("EXIT_ROOT_ANCHOR_UNAVAILABLE") from exc


def _bootstrap_under_verified_anchor(
    anchor: Path, *, anchor_uid: int, anchor_gid: int,
    components_uid: int, components_gid: int,
) -> dict[str, object]:
    """Private disposable-fixture adapter; NOT the root management entrypoint."""
    if not isinstance(anchor, Path) or not anchor.is_absolute():
        _deny("ANCHOR_NOT_ABSOLUTE")
    try:
        with ExitStack() as opened:
            fd = os.open(anchor, _FLAGS)
            opened.callback(os.close, fd)
            return _bootstrap_from_fd(
                fd, anchor_uid=anchor_uid, anchor_gid=anchor_gid,
                components_uid=components_uid, components_gid=components_gid,
            )
    except OSError as exc:
        raise ValueError("EXIT_ROOT_ANCHOR_UNAVAILABLE") from exc


def _production_anchor_fd(opened: ExitStack) -> int:
    """No symlink/ACL/mount drift in / -> srv -> cripta trusted ancestors."""
    fd = os.open("/", _FLAGS)
    opened.callback(os.close, fd)
    _check_dir(fd, uid=0, gid=0, mode=0o755)
    dev = os.fstat(fd).st_dev
    for name in ("srv", "cripta"):
        child = _child_fd(fd, name)
        if child is None:
            _deny("PRODUCTION_ANCHOR_MISSING")
        opened.callback(os.close, child)
        _check_dir(child, uid=0, gid=0, mode=0o755, dev=dev)
        fd = child
    return fd


def _service_group_gid() -> int:
    import grp  # Linux-only: never used in Windows CI synthetic fixture.

    return grp.getgrnam("cripta").gr_gid


def inspect_exit_root_tree() -> dict[str, object]:
    """Actual system read-only metadata, never install or runtime proof."""
    with ExitStack() as opened:
        anchor = _production_anchor_fd(opened)
        return _snapshot(
            anchor, anchor_uid=0, anchor_gid=0,
            components_uid=0, components_gid=_service_group_gid(),
        )


@contextmanager
def _hold_shared_release_lock(
    *, path: Path, uid: int, gid: int,
) -> Iterator[None]:
    """Required shared installer lock; no new lockfile is ever created.

    The variable path/UID adapter is PRIVATE for disposable actor-owned test
    fixtures. Production caller uses the one exact fixed OS path and root
    identity; no CLI or caller-selected lock is exposed.
    """
    if not isinstance(path, Path) or not path.is_absolute():
        _deny("RELEASE_LOCK_PATH_INVALID")
    import fcntl  # Linux-only; Windows CI does not invoke this function.

    try:
        fd = os.open(
            path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise ValueError("EXIT_ROOT_RELEASE_LOCK_MISSING_OR_UNTRUSTED") from exc
    locked = False
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != uid or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != 0o644
            or info.st_nlink != 1
        ):
            _deny("RELEASE_LOCK_OWNER_MODE_INVALID")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise ValueError("EXIT_ROOT_RELEASE_LOCK_BUSY") from exc
            raise ValueError("EXIT_ROOT_RELEASE_LOCK_FAILED") from exc
        locked = True
        yield
    finally:
        if locked:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def bootstrap_exit_root_once() -> dict[str, object]:
    """Fixed root-only OS bootstrap. NOT a deploy command or installer.

    Physical invocation later requires separately approved root actor,
    tool/security gates, exact installed source identity and sandbox proof.
    Do not invoke it from source sync, CI, test suite or the current
    strict-sandbox Release Controller (whose optional path is absent).
    """
    if os.geteuid() != 0:
        _deny("ROOT_REQUIRED")
    # This exact root-only bootstrap is mutually exclusive with the existing
    # FULL/Dashboard/Native install owner, even though it starts no services.
    with (
        _hold_shared_release_lock(path=_SHARED_LOCK, uid=0, gid=0),
        ExitStack() as opened,
    ):
        anchor = _production_anchor_fd(opened)
        return _bootstrap_from_fd(
            anchor, anchor_uid=0, anchor_gid=0,
            components_uid=0, components_gid=_service_group_gid(),
        )
