import tempfile
import unittest
from pathlib import Path

from bybit_workbench.persistence import EventJournal


class EventJournalTests(unittest.TestCase):
    def test_persists_structured_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = EventJournal(Path(directory) / "events.db")
            try:
                event_id = journal.append(
                    "app.state_transition",
                    "DISCONNECTED -> SYNCING",
                    details={"reason": "startup"},
                )
                events = journal.recent()
            finally:
                journal.close()
        self.assertEqual(events[0].event_id, event_id)
        self.assertEqual(events[0].details["reason"], "startup")


if __name__ == "__main__":
    unittest.main()
