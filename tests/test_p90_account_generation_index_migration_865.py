"""Fixed #865 migration management rail: all production calls are mocked."""
from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from operations.infrastructure import p90_account_generation_index_migration_865 as rail


def state(index=None):
    return {
        "role": "postgres", "session": "postgres", "db": "cripta", "local": True,
        "recovery": False, "version": 160012, "pgdata": str(rail.PGDATA), "port": 5432,
        "owner": "postgres", "kind": "r", "gate": 0, "permissions": 0,
        "arms": 0, "account_arms": 0, "grants": [True, True, True, False],
        "rows": 810000, "max_wal_bytes": rail.GIB, "replication_slots": 0,
        "archive_mode": "off", "long_xacts": 0, "index_builds": 0, "index": index,
    }


def valid_index():
    return {"definition": rail.DEFINITION, "valid": True, "ready": True,
            "live": True, "bytes": 128 * rail.MIB}


class MigrationRailTests(unittest.TestCase):
    def test_only_exact_modes_and_fixed_psql_actor(self):
        for mode in ("--check", "--apply", "--rollback"):
            self.assertEqual(rail.parse_mode([mode]), mode)
        for args in ([], ["--apply", "other.sql"], ["--apply", "--rollback"],
                     ["--check", "-c", "SELECT 1"]):
            with self.assertRaises(rail.Blocked):
                rail.parse_mode(args)
        with patch.object(rail.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, b"ok", b"")
            self.assertEqual(rail.psql(b"SELECT 1;", readonly=True, timeout=12), "ok")
            args, kwargs = run.call_args
            self.assertEqual(args[0], rail.PSQL)
            self.assertEqual(args[0][:5], ("/usr/sbin/runuser", "-u", "postgres",
                                          "--", "/usr/bin/psql"))
            self.assertIn("default_transaction_read_only=on", kwargs["env"]["PGOPTIONS"])
            self.assertEqual(kwargs["input"], b"SELECT 1;")
            self.assertNotIn("shell", kwargs)

    def test_wrong_role_gate_and_bad_index_refused(self):
        for key, value in (("role", "cripta"), ("owner", "cripta"), ("gate", 1),
                           ("account_arms", 1), ("local", False), ("version", 150001)):
            proof = state()
            proof[key] = value
            with self.assertRaises(rail.Blocked):
                rail.validate(proof)
        proof = state({**valid_index(), "definition": "wrong"})
        with self.assertRaises(rail.Blocked):
            rail.validate(proof, allow_invalid=True)
        proof = state({**valid_index(), "valid": False})
        with self.assertRaises(rail.Blocked):
            rail.validate(proof)
        rail.validate(proof, allow_invalid=True)

    def test_budget_blocks_small_disk_and_concurrent_build(self):
        result = rail.budget(state(), 4 * rail.GIB)
        self.assertGreater(result["required"], 2 * rail.GIB)
        with self.assertRaises(rail.Blocked):
            rail.budget(state(), 2 * rail.GIB)
        proof = state()
        proof["index_builds"] = 1
        with self.assertRaises(rail.Blocked):
            rail.budget(proof, 10 * rail.GIB)

    def test_apply_checks_backup_before_exact_sql_and_rechecks(self):
        with (patch.object(rail, "probe", side_effect=[state(), state(), state(valid_index())]),
              patch.object(rail, "free_bytes", return_value=4 * rail.GIB),
              patch.object(rail, "backup", return_value={"stamp": "test"}),
              patch.object(rail, "psql") as sql):
            result = rail.execute("--apply", b"UP", b"DOWN")
            sql.assert_called_once_with(b"UP", readonly=False, timeout=145)
            self.assertEqual(result["state"], "INDEX_VALID_READY_LIVE")
            self.assertFalse(result["runtime_behavior_verified"])

    def test_backup_failure_never_executes_sql(self):
        with (patch.object(rail, "probe", return_value=state()),
              patch.object(rail, "free_bytes", return_value=4 * rail.GIB),
              patch.object(rail, "backup", side_effect=rail.Blocked("backup stale")),
              patch.object(rail, "psql") as sql,
              self.assertRaises(rail.Blocked)):
            rail.execute("--apply", b"UP", b"DOWN")
        sql.assert_not_called()

    def test_check_read_only_and_rollback_exact_down(self):
        with (patch.object(rail, "probe", return_value=state()),
              patch.object(rail, "free_bytes", return_value=4 * rail.GIB),
              patch.object(rail, "backup", return_value={"stamp": "test"}),
              patch.object(rail, "psql") as sql):
            self.assertEqual(rail.execute("--check", b"UP", b"DOWN")["state"], "CHECK_ONLY")
            sql.assert_not_called()
        with (patch.object(rail, "probe", side_effect=[state(valid_index()), state()]),
              patch.object(rail, "psql") as sql):
            self.assertEqual(rail.execute("--rollback", b"UP", b"DOWN")["state"],
                             "ROLLBACK_INDEX_ABSENT")
            sql.assert_called_once_with(b"DOWN", readonly=False, timeout=145)


if __name__ == "__main__":
    unittest.main()
