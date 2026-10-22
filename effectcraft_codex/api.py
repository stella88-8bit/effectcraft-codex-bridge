"""Authenticated, loopback-only HTTP facade for the native control channel.

The bearer token grants native EffectCraft authority (including files/scripts).
This API is not a file sandbox. No request is implicitly retried.
"""
from __future__ import annotations

import hmac
import json
import math
import re
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from .openapi import MAX_BODY, MAX_SIDE, NAME_PATTERN, build_openapi
from .transport import ControlError

MAX_HEADERS = 16_384
INCOMING_TIMEOUT = 10.0
_NAME = re.compile(NAME_PATTERN)


class _InputError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "invalid_request"):
        self.message, self.status, self.code = message, status, code


def _error_status(error: ControlError) -> int:
    code = error.code.lower()
    if "timeout" in code or "timed_out" in code:
        return 504
    if error.outcome_unknown:
        return 502
    if code == "invalid_request":
        return 400
    if code == "request_too_large":
        return 413
    if code in {"not_connected", "connection_refused", "unavailable", "connect_failed", "connection_failed", "upstream_unavailable"}:
        return 503
    if "protocol" in code or "connection" in code or "invalid_response" in code:
        return 502
    return 422


def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate JSON keys are forbidden.")
        obj[key] = value
    return obj


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Numbers must be finite.")
    return number


def _constant(value):
    raise ValueError("Numbers must be finite.")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "EffectCraftBridge"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(INCOMING_TIMEOUT)

    def log_message(self, format, *args):
        # Native parameters, request targets and credentials must not reach logs.
        pass

    def send_error(self, code, message=None, explain=None):
        descriptions = {400: "Malformed HTTP request.", 414: "Request target is too long.", 431: "Request headers are too large.", 501: "Unsupported HTTP method."}
        self._json(code, {"ok": False, "error": {"code": "invalid_http", "message": descriptions.get(code, "HTTP request rejected."), "outcome_unknown": False}})

    def _json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        if status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="EffectCraft"')
        self.end_headers()
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(data)

    def _guard(self):
        if sum(len(k) + len(v) + 4 for k, v in self.headers.items()) > MAX_HEADERS:
            raise _InputError("Request headers are too large.", 431, "headers_too_large")
        hosts = self.headers.get_all("Host", [])
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if len(hosts) != 1 or hosts[0] not in allowed:
            raise _InputError("Host must identify this loopback API.", 403, "forbidden_host")
        if self.headers.get_all("Origin") is not None:
            raise _InputError("Browser Origin requests are refused.", 403, "forbidden_origin")
        auth = self.headers.get_all("Authorization", [])
        expected = "Bearer " + self.server.bridge_token
        if len(auth) != 1 or not hmac.compare_digest(auth[0].encode("utf-8"), expected.encode("utf-8")):
            raise _InputError("A valid bearer token is required.", 401, "unauthorized")
        if self.headers.get_all("Transfer-Encoding") is not None:
            raise _InputError("Transfer-Encoding is forbidden.")
        if self.headers.get_all("Expect") is not None:
            raise _InputError("Expect is not supported.")
        if not self.path.startswith("/") or self.path.startswith("//"):
            raise _InputError("Use an origin-form request target.")
        try:
            target = urlsplit(self.path)
            if target.fragment:
                raise ValueError()
            query = parse_qs(target.query, keep_blank_values=True, strict_parsing=True, max_num_fields=8)
        except ValueError:
            raise _InputError("Invalid request target or query.") from None
        for value in query.values():
            if len(value) != 1:
                raise _InputError("Duplicate query fields are forbidden.")
        if target.path != "/v1/commands" and query:
            raise _InputError("This route has no query parameters.")
        if set(query) - {"filter"}:
            raise _InputError("Unknown query field.")
        if "filter" in query and (len(query["filter"][0]) > 256 or any(ord(c) < 32 for c in query["filter"][0])):
            raise _InputError("filter must be at most 256 characters without control characters.")
        return target.path, query

    def handle_expect_100(self):
        # Never acknowledge a body before authentication/header validation.
        try:
            self._guard()
        except _InputError as exc:
            self._json(exc.status, {"ok": False, "error": {"code": exc.code, "message": exc.message, "outcome_unknown": False}})
        return False

    def _length(self, required=False):
        values = self.headers.get_all("Content-Length", [])
        if not values and not required:
            return 0
        if len(values) != 1 or not re.fullmatch(r"[0-9]{1,12}", values[0]):
            raise _InputError("One valid Content-Length is required.")
        length = int(values[0])
        if length > MAX_BODY:
            raise _InputError("JSON body is too large.", 413, "body_too_large")
        return length

    def _body(self):
        lengths = self._length(required=True)
        content_types = self.headers.get_all("Content-Type", [])
        if len(content_types) != 1:
            raise _InputError("Content-Type must be application/json.", 415, "unsupported_media_type")
        parts = [p.strip().lower() for p in content_types[0].split(";")]
        if parts[0] != "application/json" or any(p not in {'charset=utf-8', 'charset="utf-8"'} for p in parts[1:]):
            raise _InputError("Content-Type must be application/json encoded as UTF-8.", 415, "unsupported_media_type")
        raw = self.rfile.read(lengths)
        if len(raw) != lengths:
            raise _InputError("Incomplete request body.")
        try:
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_float=_float, parse_constant=_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise _InputError("Body must be a UTF-8 JSON object with unique keys and finite numbers.") from None
        if not isinstance(data, dict):
            raise _InputError("Body must be a JSON object.")
        return data

    @staticmethod
    def _only(data, keys):
        if set(data) - set(keys):
            raise _InputError("Unknown body field.")

    def _dispatch(self, method):
        dispatched = False
        try:
            path, query = self._guard()
            if method == "GET":
                if self._length() != 0:
                    raise _InputError("GET requests must not contain a body.")
                if path == "/v1/openapi.json":
                    result = build_openapi(self.server.server_port)
                else:
                    native = {"/health": "editor.state", "/v1/state": "editor.state", "/v1/commands": "command.list", "/v1/ui": "ui.inspect"}.get(path)
                    if native is None:
                        if path in {"/v1/command", "/v1/rpc", "/v1/render-frame"}:
                            raise _InputError("Use POST for this route.", 405, "method_not_allowed")
                        raise _InputError("Unknown route.", 404, "not_found")
                    params = {"schemas": True} if path == "/v1/commands" else None
                    if path == "/v1/commands" and "filter" in query:
                        params["filter"] = query["filter"][0]
                    dispatched = True
                    if path == "/v1/ui":
                        result = self.server.bridge_client.request(native, params)
                    else:
                        result = self.server.bridge_client.command(native, params)
            elif method == "POST":
                if path not in {"/v1/command", "/v1/rpc", "/v1/render-frame"}:
                    if path in {"/health", "/v1/state", "/v1/commands", "/v1/ui", "/v1/openapi.json"}:
                        raise _InputError("Use GET for this route.", 405, "method_not_allowed")
                    raise _InputError("Unknown route.", 404, "not_found")
                if query:
                    raise _InputError("POST routes have no query parameters.")
                data = self._body()
                if path in {"/v1/command", "/v1/rpc"}:
                    key = "command" if path == "/v1/command" else "method"
                    self._only(data, (key, "params"))
                    name = data.get(key)
                    if not isinstance(name, str) or not _NAME.fullmatch(name):
                        raise _InputError(f"{key} must be a valid native name (1-128 characters).")
                    params = data.get("params", {})
                    if not isinstance(params, dict):
                        raise _InputError("params must be an object.")
                    dispatched = True
                    if key == "command":
                        result = self.server.bridge_client.command(name, params)
                    else:
                        result = self.server.bridge_client.request(name, params)
                else:
                    self._only(data, ("comp", "time", "max_side"))
                    if "comp" in data:
                        comp = data["comp"]
                        if not (type(comp) is int or (isinstance(comp, str) and 1 <= len(comp) <= 256 and not any(ord(c) < 32 for c in comp))):
                            raise _InputError("comp must be an integer ID or a nonempty name of at most 256 characters without control characters.")
                    if "time" in data and (type(data["time"]) not in (int, float)):
                        raise _InputError("time must be a finite number.")
                    if "max_side" in data and (type(data["max_side"]) is not int or not 0 <= data["max_side"] <= MAX_SIDE):
                        raise _InputError(f"max_side must be an integer between 0 and {MAX_SIDE}.")
                    dispatched = True
                    result = self.server.bridge_client.request("render.frame", {**data, "base64": True})
            else:
                raise _InputError("Unsupported HTTP method.", 405, "method_not_allowed")
            self._json(200, {"ok": True, "result": result})
        except _InputError as exc:
            self._json(exc.status, {"ok": False, "error": {"code": exc.code, "message": exc.message, "outcome_unknown": False}})
        except ControlError as exc:
            self._json(_error_status(exc), {"ok": False, "error": exc.as_dict()})
        except (TimeoutError, socket.timeout):
            self._json(504 if dispatched else 408, {"ok": False, "error": {"code": "upstream_timeout" if dispatched else "request_timeout", "message": "EffectCraft response timed out." if dispatched else "Incoming request timed out.", "outcome_unknown": dispatched}})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self._json(502 if dispatched else 500, {"ok": False, "error": {"code": "bridge_error", "message": "Bridge could not complete the request.", "outcome_unknown": dispatched}})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def __getattr__(self, name):
        if name.startswith("do_"):
            return lambda: self._dispatch(name[3:])
        raise AttributeError(name)


class _Server(HTTPServer):
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # Do not emit tracebacks containing native data or incoming credentials.
        pass


def make_server(client, token: str, port: int = 0) -> HTTPServer:
    """Build a server bound exclusively to 127.0.0.1 (port=0 for tests)."""
    if not isinstance(token, str) or not 1 <= len(token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("token must be a nonempty printable ASCII secret without whitespace")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be an integer between 0 and 65535")
    server = _Server(("127.0.0.1", port), _Handler)
    server.bridge_client = client
    server.bridge_token = token
    return server


def serve(client, token: str, port: int = 8767) -> None:
    """Serve until interrupted, then release the listening socket."""
    with make_server(client, token, port) as server:
        server.serve_forever(poll_interval=0.2)
