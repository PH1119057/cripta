"""Read-only classification of protection evidence and Entry blocks."""

from __future__ import annotations


def classify_protection(state: str) -> str:
    return {"UNKNOWN": "PROOF_UNAVAILABLE", "NOT_VERIFIED": "PROTECTION_NOT_CONFIRMED", "VERIFIED": "CONFIRMED_AT_OBSERVATION"}.get(state, "UNCLASSIFIED")
