import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

# Use a temporary SQLite file so tests never touch a developer database.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["VERIMETRIX_DB"] = _tmp.name
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class VeriMetrixRoadmapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.seed()

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(_tmp.name)
        except FileNotFoundError:
            pass

    def test_measurement_formula_and_fail_closed(self):
        helper = object.__new__(server.Handler)
        measurements, result = helper._measurement_result([
            {"nominal_value": 100, "indication": 101.5, "permissible_error": 1},
        ])
        self.assertEqual(result, "FAIL")
        self.assertEqual(measurements[0]["error_value"], 1.5)
        self.assertEqual(measurements[0]["result"], "FAIL")

        measurements, result = helper._measurement_result([
            {"nominal_value": 100, "indication": 100.5, "permissible_error": 1},
        ])
        self.assertEqual(result, "PASS")
        self.assertEqual(measurements[0]["error_value"], 0.5)

    def test_notification_deduplication(self):
        with server.conn() as c:
            app = c.execute("SELECT id FROM applications LIMIT 1").fetchone()[0]
            server.notify(c, app, "Test milestone", "one", "TEST_MILESTONE")
            server.notify(c, app, "Test milestone", "duplicate", "TEST_MILESTONE")
            count = c.execute("SELECT COUNT(*) FROM notifications WHERE application_id=? AND milestone_key=?", (app, "TEST_MILESTONE")).fetchone()[0]
        self.assertEqual(count, 1)

    def test_public_tracking_redacts_private_fields(self):
        with server.conn() as c:
            app = c.execute("SELECT * FROM applications LIMIT 1").fetchone()
            public = server.public_app_dict(c, app)
        for private in ("email", "phone", "address", "organization", "officer_name"):
            self.assertNotIn(private, public)
        self.assertIn("application_number", public)
        self.assertIn("status", public)

    def test_audit_chain_verifies(self):
        with server.conn() as c:
            result = server.verify_audit_chain(c)
        self.assertEqual(result["integrity"], "VALID")

    def test_dynamic_statuses(self):
        self.assertEqual(server.status_for({"status_override": "REVOKED", "valid_until": "2099-01-01"}), "REVOKED")
        self.assertEqual(server.status_for({"status_override": None, "valid_until": "2000-01-01"}), "EXPIRED")


if __name__ == "__main__":
    unittest.main()
