import unittest

from bybit_workbench.app.state_machine import AppStateMachine, InvalidStateTransition
from bybit_workbench.domain.types import AppState


class AppStateMachineTests(unittest.TestCase):
    def test_happy_path_reaches_running(self) -> None:
        machine = AppStateMachine()
        machine.transition(AppState.SYNCING, "startup")
        machine.transition(AppState.READY, "synchronized")
        machine.transition(AppState.ARMED, "validated")
        self.assertFalse(machine.can_create_entry)
        machine.transition(AppState.RUNNING, "manual start")
        self.assertTrue(machine.can_create_entry)

    def test_disconnected_cannot_arm(self) -> None:
        machine = AppStateMachine()
        with self.assertRaises(InvalidStateTransition):
            machine.transition(AppState.ARMED, "unsafe shortcut")

    def test_degraded_cannot_create_entry(self) -> None:
        machine = AppStateMachine()
        machine.transition(AppState.SYNCING, "startup")
        machine.transition(AppState.DEGRADED, "private stream stale")
        self.assertFalse(machine.can_create_entry)


if __name__ == "__main__":
    unittest.main()
