"""Immutable CLI instance guard for explicit SUB service units.

Environment files may override systemd Environment= keys. An instance
argument embedded in ExecStart is independent of their contents and guards
against a SUB worker accidentally assuming legacy MAIN authority.
"""

from __future__ import annotations

from collections.abc import Sequence


def require_exact_sub_instance(
    *, account_kind: str, trading_account_id: str, argv: Sequence[str]
) -> None:
    args = tuple(argv)
    if account_kind == "MAIN" and not args:
        return
    if (
        account_kind == "SUB"
        and bool(trading_account_id)
        and args == (f"--sub-account={trading_account_id}",)
    ):
        return
    raise RuntimeError("PRIVATE_SUB_INSTANCE_IDENTITY_MISMATCH")
