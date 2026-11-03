"""Stable Control Core v1 public contract.

Pure control semantics only.  Importing this package performs no I/O and no
production runtime imports it until a later explicitly reviewed cutover.
"""

from .model import (
    CORE_CONTRACT_VERSION,
    ControlAction,
    ControlEvent,
    ControlState,
    TechnicalReadiness,
    TransitionDecision,
    TransitionReason,
)
from .transitions import transition

__all__ = [
    "CORE_CONTRACT_VERSION",
    "ControlAction",
    "ControlEvent",
    "ControlState",
    "TechnicalReadiness",
    "TransitionDecision",
    "TransitionReason",
    "transition",
]
