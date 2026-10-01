"""Vercel WSGI entrypoint for the existing VeriMetrix API.

The rest of the application keeps its standard-library HTTP routes. This
adapter exposes exactly one Vercel-supported WSGI entrypoint (``app``),
restores the original API path after the rewrite in vercel.json, and forwards
the request to the existing route handlers.
"""

from email.message import Message
from http import HTTPStatus
import io
import json
import os
from pathlib import Path
import sys
import threading
import traceback
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server


_initialized = False
_init_lock = threading.Lock()


def _ensure_initialized():
    """Seed the server-side database once per warm Vercel function process."""
    global _initialized

    # A deployed database is required.
    if not (os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")):
        return False

    # server.py now contains the fixed testing fallback credentials:
    #
    #   naren@legalmetrology.com
    #   naren142727
    #
    # Therefore VERIMETRIX_DEMO_OFFICER_PASSWORD does not have to be
    # configured for the testing deployment.
    if not _initialized:
        with _init_lock:
            if not _initialized:
                server.seed()
                _initialized = True

    return True


def _restore_route(request_target, query_string=""):
    """Restore /api/<route> from Vercel's rewritten request."""

    target = request_target or "/"
    parsed = urlsplit(target)

    path = parsed.path or "/"
    query = parsed.query or query_string or ""

    # Normal Vercel rewrite:
    #
    # /api/officer/login
    #
    # becomes:
    #
    # /api/index.py?__vmx_route=officer/login
    #
    if path.rstrip("/") == "/api/index.py":
        query_items = parse_qsl(
            query,
            keep_blank_values=True,
        )

        routed_path = next(
            (
                value
                for key, value in query_items
                if key == "__vmx_route"
            ),
            None,
        )

        if routed_path is not None:
            path = "/api/" + routed_path.lstrip("/")

            query = urlencode(
                [
                    (key, value)
                    for key, value in query_items
                    if key != "__vmx_route"
                ]
            )
        else:
            path = "/api"

    # Also support path-style invocation:
    #
    # /api/index.py/officer/login
    #
    elif path.startswith("/api/index.py/"):
        route = path[len("/api/index.py/"):].lstrip("/")
        path = "/api/" + route

    return path, query


class _VercelHandler(server.Handler):
    """Small in-memory HTTP handler surface used by the existing API routes."""

    def __init__(self, method, path, headers, body, client_ip):
        self.command = method
        self.path = path
        self.headers = headers
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO(body)
        self.client_address = (client_ip, 0)
        self.request_version = "HTTP/1.1"
        self._status = 200
        self._response_headers = []

    def send_response(self, code, message=None):
        self._status = int(code)

    def send_header(self, key, value):
        self._response_headers.append(
            (str(key), str(value))
        )

    def end_headers(self):
        return None

    def log_message(self, fmt, *args):
        return None


def _status_line(status):
    try:
        phrase = HTTPStatus(status).phrase
    except ValueError:
        phrase = "Unknown"

    return f"{status} {phrase}"


def _adapter_response(
    start_response,
    status,
    payload,
    *,
    head_only=False,
):
    body = json.dumps(
        payload,
        ensure_ascii=False,
    ).encode("utf-8")

    headers = [
        (
            "Content-Type",
            "application/json; charset=utf-8",
        ),
        (
            "Cache-Control",
            "no-store",
        ),
        (
            "X-Content-Type-Options",
            "nosniff",
        ),
        (
            "X-Frame-Options",
            "DENY",
        ),
        (
            "Referrer-Policy",
            "strict-origin-when-cross-origin",
        ),
        (
            "Content-Length",
            str(len(body)),
        ),
    ]

    start_response(
        _status_line(status),
        headers,
    )

    return [
        b"" if head_only else body
    ]


def _request_headers(environ):
    headers = Message()

    for key, value in environ.items():
        if key.startswith("HTTP_"):
            headers[
                key[5:].replace("_", "-")
            ] = str(value)

    if environ.get("CONTENT_TYPE"):
        headers["Content-Type"] = str(
            environ["CONTENT_TYPE"]
        )

    if environ.get("CONTENT_LENGTH"):
        headers["Content-Length"] = str(
            environ["CONTENT_LENGTH"]
        )

    return headers


def _wsgi_response(start_response, inner, method):
    body = inner.wfile.getvalue()

    headers = [
        (key, value)
        for key, value in inner._response_headers
        if key.lower() != "content-length"
    ]

    existing = {
        key.lower()
        for key, _ in headers
    }

    security_headers = [
        (
            "X-Content-Type-Options",
            "nosniff",
        ),
        (
            "X-Frame-Options",
            "DENY",
        ),
        (
            "Referrer-Policy",
            "strict-origin-when-cross-origin",
        ),
    ]

    headers.extend(
        (key, value)
        for key, value in security_headers
        if key.lower() not in existing
    )

    if inner._status != 204:
        headers.append(
            (
                "Content-Length",
                str(len(body)),
            )
        )

    start_response(
        _status_line(inner._status),
        headers,
    )

    return [
        b"" if method == "HEAD" else body
    ]


def app(environ, start_response):
    """Vercel-supported WSGI application."""

    method = str(
        environ.get(
            "REQUEST_METHOD",
            "GET",
        )
    ).upper()

    head_only = method == "HEAD"

    try:
        request_target = (
            environ.get("RAW_URI")
            or environ.get("REQUEST_URI")
            or environ.get(
                "PATH_INFO",
                "/",
            )
        )

        path, query = _restore_route(
            request_target,
            environ.get(
                "QUERY_STRING",
                "",
            ),
        )

        full_path = (
            path
            + (
                "?" + query
                if query
                else ""
            )
        )

        # Only API routes are handled by this WSGI entrypoint.
        if not path.startswith("/api/"):
            return _adapter_response(
                start_response,
                404,
                {
                    "error": "Not found"
                },
                head_only=head_only,
            )

        supported = {
            "GET",
            "HEAD",
            "POST",
            "PUT",
            "OPTIONS",
        }

        if method not in supported:
            return _adapter_response(
                start_response,
                405,
                {
                    "error": "Method not allowed"
                },
                head_only=head_only,
            )

        # Read and validate request body.
        try:
            length = int(
                environ.get(
                    "CONTENT_LENGTH"
                ) or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            return _adapter_response(
                start_response,
                400,
                {
                    "error": "Invalid request body"
                },
                head_only=head_only,
            )

        if length < 0:
            return _adapter_response(
                start_response,
                400,
                {
                    "error": "Invalid request body"
                },
                head_only=head_only,
            )

        headers = _request_headers(environ)

        is_multipart = (
            path == "/api/documents"
            and str(
                environ.get(
                    "CONTENT_TYPE",
                    "",
                )
            ).lower().startswith(
                "multipart/form-data"
            )
        )

        max_body = (
            10 * 1024 * 1024
            if is_multipart
            else server.MAX_BODY
        )

        if length > max_body:
            return _adapter_response(
                start_response,
                413,
                {
                    "error": "Request body too large"
                },
                head_only=head_only,
            )

        body_stream = environ.get(
            "wsgi.input",
            io.BytesIO(),
        )

        body = (
            body_stream.read(length)
            if length
            else b""
        )

        if len(body) != length:
            return _adapter_response(
                start_response,
                400,
                {
                    "error": "Incomplete request body"
                },
                head_only=head_only,
            )

        # CORS preflight does not need database initialization.
        if method != "OPTIONS":

            if not _ensure_initialized():
                return _adapter_response(
                    start_response,
                    503,
                    {
                        "error": (
                            "Required server environment "
                            "variables are missing"
                        )
                    },
                    head_only=head_only,
                )

        client_ip = (
            environ.get("REMOTE_ADDR")
            or "vercel"
        )

        inner = _VercelHandler(
            method,
            full_path,
            headers,
            body,
            client_ip,
        )

        dispatch = {
            "GET": inner.do_GET,
            "HEAD": inner.do_GET,
            "POST": inner.do_POST,
            "PUT": inner.do_PUT,
            "OPTIONS": inner.do_OPTIONS,
        }[method]

        dispatch()

        return _wsgi_response(
            start_response,
            inner,
            method,
        )

    except Exception:
        request_id = (
            environ.get("HTTP_X_VERCEL_ID")
            or environ.get("HTTP_X_REQUEST_ID")
            or uuid.uuid4().hex
        )

        print(
            "[verimetrix] API request failed; "
            f"request_id={request_id}",
            file=sys.stderr,
        )

        traceback.print_exc()

        return _adapter_response(
            start_response,
            500,
            {
                "error": "Vercel API runtime error",
                "request_id": request_id,
            },
            head_only=head_only,
        )