"""Reusable service profiles for REAL readiness preparation."""

from __future__ import annotations

from .real_service_readiness import ServiceSpec


def p90_real_v3_service_specs(*, sub_account_id: str) -> tuple[ServiceSpec, ...]:
    """First consumer of generic REAL readiness infrastructure.

    P90 supplies a concrete profile; the planner/broker remain Strategy-agnostic.
    Dashboard is intentionally excluded: it is the authenticated initiator and
    cannot repair its own absence from an in-flight request.
    """
    private_sub = f"cripta-private-runtime@{sub_account_id}.service"
    entry_sub = f"cripta-universal-entry-consumer@{sub_account_id}.service"
    lifecycle_sub = f"cripta-lifecycle-account@{sub_account_id}.service"
    position_sub = f"cripta-account-position@{sub_account_id}.service"

    return (
        ServiceSpec("cripta-private-runtime.service", (), True),
        ServiceSpec(private_sub, (), True),
        ServiceSpec(
            "cripta-universal-entry-observer.service",
            ("cripta-private-runtime.service", private_sub),
            True,
        ),
        ServiceSpec(
            "cripta-universal-entry-consumer.service",
            ("cripta-private-runtime.service", "cripta-universal-entry-observer.service"),
            True,
        ),
        ServiceSpec(
            entry_sub,
            (private_sub, "cripta-universal-entry-observer.service"),
            True,
        ),
        ServiceSpec(
            "cripta-lifecycle-supervisor.service",
            ("cripta-private-runtime.service",),
            True,
        ),
        ServiceSpec(lifecycle_sub, (private_sub,), True),
        ServiceSpec(
            "cripta-position-supervisor.service",
            ("cripta-private-runtime.service",),
            True,
        ),
        ServiceSpec(position_sub, (private_sub,), True),
    )
