import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from wsgiref.validate import validator
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server

_SPEC = importlib.util.spec_from_file_location("verimetrix_vercel_api", ROOT / "api" / "index.py")
api = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(api)

TEST_PASSWORD = "UnitTestOfficerPassword2026!"


class VeriMetrixVercelApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="verimetrix-api-test-")
        self.old_db_path = server.DB_PATH
        self.old_demo_email = server.DEMO_OFFICER_EMAIL
        self.old_demo_password = server.DEMO_OFFICER_PASSWORD
        self.old_initialized = api._initialized
        self.old_sessions = dict(server.SESSIONS)
        self.old_login_failures = dict(server.LOGIN_FAILURES)
        self.old_env = {
            key: os.environ.get(key)
            for key in (
                "SUPABASE_DB_URL",
                "DATABASE_URL",
                "VERIMETRIX_DB",
                "VERIMETRIX_DEMO_OFFICER_PASSWORD",
                "VERIMETRIX_SECURE_COOKIES",
            )
        }
        os.environ.pop("SUPABASE_DB_URL", None)
        os.environ.pop("DATABASE_URL", None)
        os.environ["VERIMETRIX_DEMO_OFFICER_PASSWORD"] = TEST_PASSWORD
        os.environ["VERIMETRIX_SECURE_COOKIES"] = "0"
        server.DB_PATH = Path(self.tempdir.name) / "test.db"
        server.DEMO_OFFICER_EMAIL = "officer@verimetrix.local"
        server.DEMO_OFFICER_PASSWORD = TEST_PASSWORD
        server.SESSIONS.clear()
        server.LOGIN_FAILURES.clear()
        api._initialized = False

        def initialize_local_sqlite():
            if not api._initialized:
                server.seed()
                api._initialized = True
            return True

        self._init_patch = patch.object(api, "_ensure_initialized", side_effect=initialize_local_sqlite)
        self._init_patch.start()

    def tearDown(self):
        self._init_patch.stop()
        server.DB_PATH = self.old_db_path
        server.DEMO_OFFICER_EMAIL = self.old_demo_email
        server.DEMO_OFFICER_PASSWORD = self.old_demo_password
        server.SESSIONS.clear()
        server.SESSIONS.update(self.old_sessions)
        server.LOGIN_FAILURES.clear()
        server.LOGIN_FAILURES.update(self.old_login_failures)
        api._initialized = self.old_initialized
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tempdir.cleanup()

    def request(self, path, *, method="GET", query="", body=b"", headers=None, application=None):
        environ = {
            "REQUEST_METHOD": method,
            "SCRIPT_NAME": "",
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_LENGTH": str(len(body)),
            "CONTENT_TYPE": "",
            "REMOTE_ADDR": "127.0.0.1",
            "SERVER_NAME": "verimetrix.test",
            "SERVER_PORT": "443",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "https",
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        for key, value in (headers or {}).items():
            normalized = key.upper().replace("-", "_")
            if normalized == "CONTENT_TYPE":
                environ["CONTENT_TYPE"] = value
            elif normalized == "CONTENT_LENGTH":
                environ["CONTENT_LENGTH"] = value
            else:
                environ["HTTP_" + normalized] = value

        captured = {}

        def start_response(status, response_headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = response_headers
            return lambda data: None

        result = (application or api.app)(environ, start_response)
        response_body = b"".join(result)
        if hasattr(result, "close"):
            result.close()
        captured["body"] = response_body
        return captured

    def test_rewritten_public_stats_route_seeds_test_database(self):
        response = self.request("/api/index.py", query="__vmx_route=public-stats")
        self.assertEqual(response["status"], "200 OK")
        payload = json.loads(response["body"])
        self.assertEqual(payload["mode"], "SYNTHETIC_DEMO_REGISTRY")
        self.assertEqual(payload["stats"]["applications"], 21)
        self.assertEqual(payload["stats"]["instruments"], 18)

    def test_rewritten_officer_login_returns_csrf_token(self):
        body = json.dumps(
            {"email": "officer@verimetrix.local", "password": TEST_PASSWORD}
        ).encode("utf-8")
        response = self.request(
            "/api/index.py",
            method="POST",
            query="__vmx_route=officer%2Flogin",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response["status"], "200 OK")
        payload = json.loads(response["body"])
        self.assertTrue(payload["csrf_token"])
        self.assertTrue(
            any(key.lower() == "set-cookie" and "vmx_session=" in value
                for key, value in response["headers"])
        )

    def test_applicant_submission_officer_review_and_status_sync(self):
        email = "applicant@example.test"
        form = {
            "applicant_name": "Test Applicant",
            "email": email,
            "phone": "9876543210",
            "organization": "Test Organization",
            "address": "Test Address",
            "state": "Maharashtra",
            "district": "Pune",
            "instrument_category": "Weighing Instruments",
            "instrument_type": "Digital scale",
            "manufacturer": "Test Maker",
            "model_number": "Scale-1",
            "serial_number": "TEST-SERIAL-001",
        }
        submitted = self.request(
            "/api/index.py",
            method="POST",
            query="__vmx_route=applications",
            body=json.dumps(form).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(submitted["status"], "201 Created")
        application = json.loads(submitted["body"])["application"]
        app_id = application["id"]
        app_number = application["application_number"]
        self.assertEqual(application["model"], "Scale-1")

        # A listing/detail request is private until officer authentication.
        denied = self.request(
            "/api/index.py",
            query="__vmx_route=officer%2Fapplication&id=" + app_id,
        )
        self.assertEqual(denied["status"], "401 Unauthorized")
        denied_queue = self.request(
            "/api/index.py", query="__vmx_route=officer%2Fdashboard"
        )
        self.assertEqual(denied_queue["status"], "401 Unauthorized")

        login = self.request(
            "/api/index.py",
            method="POST",
            query="__vmx_route=officer%2Flogin",
            body=json.dumps(
                {"email": "officer@verimetrix.local", "password": TEST_PASSWORD}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(login["status"], "200 OK")
        login_json = json.loads(login["body"])
        cookie_header = next(
            value for key, value in login["headers"]
            if key.lower() == "set-cookie"
        )
        cookie = cookie_header.split(";", 1)[0]
        csrf = login_json["csrf_token"]

        # A different serverless instance has no shared Python globals; DB session still works.
        server.SESSIONS.clear()
        session = self.request(
            "/api/index.py",
            query="__vmx_route=officer%2Fsession",
            headers={"Cookie": cookie},
        )
        self.assertEqual(session["status"], "200 OK")
        self.assertEqual(json.loads(session["body"])["csrf_token"], csrf)

        detail = self.request(
            "/api/index.py",
            query="__vmx_route=officer%2Fapplication&id=" + app_id,
            headers={"Cookie": cookie},
        )
        self.assertEqual(detail["status"], "200 OK")
        detail_json = json.loads(detail["body"])
        self.assertIn("documents", detail_json)
        self.assertIn("events", detail_json)
        self.assertEqual(detail_json["application"]["model"], "Scale-1")
        dashboard = self.request(
            "/api/index.py",
            query="__vmx_route=officer%2Fdashboard",
            headers={"Cookie": cookie},
        )
        self.assertEqual(dashboard["status"], "200 OK")
        self.assertTrue(any(x["id"] == app_id for x in json.loads(dashboard["body"])["applications"]))

        review = self.request(
            "/api/index.py",
            method="POST",
            query="__vmx_route=officer%2Freview",
            body=json.dumps(
                {"id": app_id, "status": "UNDER_REVIEW", "notes": "Review started"}
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Cookie": cookie,
                "X-Vmx-CSRF": csrf,
            },
        )
        self.assertEqual(review["status"], "200 OK")

        premature_approval = self.request(
            "/api/index.py",
            method="POST",
            query="__vmx_route=officer%2Freview",
            body=json.dumps(
                {"id": app_id, "status": "DOCUMENTS_VERIFIED", "notes": "Ready"}
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Cookie": cookie,
                "X-Vmx-CSRF": csrf,
            },
        )
        self.assertEqual(premature_approval["status"], "409 Conflict")

        document_ids = []
        with server.conn() as c:
            for document_type in ("gst_certificate", "purchase_invoice", "instrument_photo"):
                document_id = server.uuid.uuid4().hex
                document_ids.append(document_id)
                c.execute(
                    "INSERT INTO documents (id,application_id,document_type,filename,checksum,status,extracted_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (document_id, app_id, document_type, document_type + ".pdf", "test-checksum", "PENDING", "{}", server.now()),
                )
            c.commit()
        for document_id in document_ids:
            approved = self.request(
                "/api/index.py",
                method="PUT",
                query="__vmx_route=officer%2Fdocuments",
                body=json.dumps({"id": document_id, "status": "VERIFIED"}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Cookie": cookie,
                    "X-Vmx-CSRF": csrf,
                },
            )
            self.assertEqual(approved["status"], "200 OK")

        document_approval = self.request(
            "/api/index.py",
            method="POST",
            query="__vmx_route=officer%2Freview",
            body=json.dumps(
                {"id": app_id, "status": "DOCUMENTS_VERIFIED", "notes": "Required documents approved"}
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Cookie": cookie,
                "X-Vmx-CSRF": csrf,
            },
        )
        self.assertEqual(document_approval["status"], "200 OK")

        tracking_query = urlencode(
            {"__vmx_route": "applications", "number": app_number, "email": email}
        )
        tracking = self.request("/api/index.py", query=tracking_query)
        self.assertEqual(tracking["status"], "200 OK")
        tracked_app = json.loads(tracking["body"])["application"]
        self.assertEqual(tracked_app["status"], "DOCUMENTS_VERIFIED")
        self.assertTrue(any(e["to_status"] == "UNDER_REVIEW" for e in tracked_app["events"]))
        self.assertTrue(any(e["to_status"] == "DOCUMENTS_VERIFIED" for e in tracked_app["events"]))
        self.assertTrue(any("Documents Verified" in n["message"] for n in tracked_app["notifications"]))
        self.assertEqual(len(tracked_app["documents"]), 3)
        self.assertTrue(all(d["verification_status"] == "VERIFIED" for d in tracked_app["documents"]))

        preferences = self.request(
            "/api/index.py",
            method="PUT",
            query="__vmx_route=preferences",
            body=json.dumps(
                {
                    "application_number": app_number,
                    "email": email,
                    "email_enabled": False,
                    "sms_enabled": True,
                    "in_app_enabled": True,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(preferences["status"], "200 OK")
        preference_read = self.request(
            "/api/index.py",
            query=urlencode(
                {
                    "__vmx_route": "preferences",
                    "application_number": app_number,
                    "email": email,
                }
            ),
        )
        self.assertEqual(
            json.loads(preference_read["body"]),
            {"email_enabled": False, "sms_enabled": True, "in_app_enabled": True},
        )

        marked = self.request(
            "/api/index.py",
            method="POST",
            query="__vmx_route=notifications",
            body=json.dumps({"application_number": app_number, "email": email}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(marked["status"], "200 OK")
        refreshed = self.request("/api/index.py", query=tracking_query)
        refreshed_app = json.loads(refreshed["body"])["application"]
        self.assertTrue(all(n["read_at"] for n in refreshed_app["notifications"]))
        for private in ("id", "instrument_pk", "applicant_name", "email", "phone", "address", "organization", "officer_name"):
            self.assertNotIn(private, tracked_app)

        bad_email = urlencode(
            {"__vmx_route": "applications", "number": app_number, "email": "wrong@example.test"}
        )
        self.assertEqual(self.request("/api/index.py", query=bad_email)["status"], "404 Not Found")

    def test_missing_server_configuration_fails_closed(self):
        with patch.object(api, "_ensure_initialized", return_value=False):
            response = self.request(
                "/api/index.py", query="__vmx_route=public-stats"
            )
        self.assertEqual(response["status"], "503 Service Unavailable")
        self.assertIn("Required server environment variables", response["body"].decode())

    def test_seed_without_password_creates_disabled_account(self):
        server.DEMO_OFFICER_PASSWORD = ""
        server.seed()
        with server.conn() as c:
            officer = server.qone(
                c,
                "SELECT active,password_hash FROM officers WHERE email=?",
                ("officer@verimetrix.local",),
            )
        self.assertEqual(officer["active"], 0)
        self.assertIsNone(officer["password_hash"])

    def test_wsgi_callable_passes_standard_validator(self):
        validated_app = validator(api.app)
        response = self.request(
            "/api/index.py",
            query="__vmx_route=public-stats",
            application=validated_app,
        )
        self.assertEqual(response["status"], "200 OK")

    def test_only_one_documented_vercel_entrypoint_is_exported(self):
        self.assertTrue(callable(api.app))
        self.assertFalse(hasattr(api, "handler"))


if __name__ == "__main__":
    unittest.main()
