"""Real HTTP requests against a fake native client: no editor required."""
import http.client
import json
import socket
import threading
import unittest

from effectcraft_codex.api import MAX_BODY, make_server
from effectcraft_codex.openapi import build_openapi
from effectcraft_codex.transport import ControlError


class FakeClient:
    def __init__(self):
        self.calls = []
        self.error = None
        self.result = {"native": {"value": False, "items": [0, 1.25, None]}}

    def request(self, method, params=None):
        if method not in {"engine.execute", "engine.commands", "ui.inspect", "render.frame"}:
            raise ControlError("upstream_error", "Unsupported native TCP method: " + method)
        self.calls.append((method, params))
        if self.error:
            raise self.error
        return self.result

    def command(self, command, params=None):
        return self.request("engine.execute", {"command": command, "params": params or {}})


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = FakeClient()
        cls.server = make_server(cls.client, "test-token-do-not-use", port=0)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.client.calls.clear()
        self.client.error = None
        self.client.result = {"native": {"value": False, "items": [0, 1.25, None]}}

    def request(self, method="GET", path="/v1/state", body=None, headers=None):
        defaults = {"Authorization": "Bearer test-token-do-not-use"}
        if body is not None:
            defaults["Content-Type"] = "application/json"
            if not isinstance(body, (str, bytes)):
                body = json.dumps(body)
        if headers:
            defaults.update(headers)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            conn.request(method, path, body=body, headers=defaults)
            response = conn.getresponse()
            data = response.read()
            result = json.loads(data) if data else None
            return response.status, result, dict(response.getheaders())
        finally:
            conn.close()

    def assert_rejected(self, expected, **kwargs):
        status, body, _ = self.request(**kwargs)
        self.assertEqual(expected, status, body)
        self.assertFalse(body["ok"])
        self.assertFalse(body["error"]["outcome_unknown"])
        self.assertEqual([], self.client.calls)

    def test_native_state_result_is_preserved(self):
        status, body, headers = self.request()
        self.assertEqual(200, status)
        self.assertEqual({"ok": True, "result": self.client.result}, body)
        self.assertEqual([("engine.execute", {"command": "editor.state", "params": {}})], self.client.calls)
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_health_probes_native_editor(self):
        status, _, _ = self.request(path="/health")
        self.assertEqual(200, status)
        self.assertEqual([("engine.execute", {"command": "editor.state", "params": {}})], self.client.calls)

    def test_ui_route(self):
        self.assertEqual(200, self.request(path="/v1/ui")[0])
        self.assertEqual([("ui.inspect", None)], self.client.calls)

    def test_commands_request_schemas_and_filter(self):
        status, _, _ = self.request(path="/v1/commands?filter=layer.%2A")
        self.assertEqual(200, status)
        self.assertEqual([("engine.execute", {"command": "command.list", "params": {"schemas": True, "filter": "layer.*"}})], self.client.calls)

    def test_commands_without_filter(self):
        self.assertEqual(200, self.request(path="/v1/commands")[0])
        self.assertEqual([("engine.execute", {"command": "command.list", "params": {"schemas": True}})], self.client.calls)

    def test_command_route(self):
        self.assertEqual(200, self.request("POST", "/v1/command", {"command": "comp.create", "params": {"name": "Тест", "width": 1920}})[0])
        self.assertEqual([("engine.execute", {"command": "comp.create", "params": {"name": "Тест", "width": 1920}})], self.client.calls)

    def test_rpc_route_and_default_params(self):
        self.assertEqual(200, self.request("POST", "/v1/rpc", {"method": "engine.commands"})[0])
        self.assertEqual([("engine.commands", {})], self.client.calls)

    def test_rpc_rejects_native_commands_as_direct_tcp_methods(self):
        status, body, _ = self.request("POST", "/v1/rpc", {"method": "editor.state"})
        self.assertEqual(422, status)
        self.assertEqual("upstream_error", body["error"]["code"])
        self.assertEqual([], self.client.calls)

    def test_render_frame_returns_native_base64(self):
        self.client.result = {"base64": "aGVsbG8=", "width": 10, "height": 5}
        status, body, _ = self.request("POST", "/v1/render-frame", {"comp": "Scene", "time": 1.25, "max_side": 720})
        self.assertEqual(200, status)
        self.assertEqual(self.client.result, body["result"])
        self.assertEqual([("render.frame", {"comp": "Scene", "time": 1.25, "max_side": 720, "base64": True})], self.client.calls)

    def test_render_native_boundary_values(self):
        self.assertEqual(200, self.request("POST", "/v1/render-frame", {"comp": 0, "time": -0.5, "max_side": 0})[0])
        self.assertEqual([("render.frame", {"comp": 0, "time": -0.5, "max_side": 0, "base64": True})], self.client.calls)

    def test_render_empty_body_object(self):
        self.assertEqual(200, self.request("POST", "/v1/render-frame", {})[0])
        self.assertEqual([("render.frame", {"base64": True})], self.client.calls)

    def test_openapi_matches_every_route(self):
        status, body, _ = self.request(path="/v1/openapi.json")
        self.assertEqual(200, status)
        spec = body["result"]
        self.assertEqual("3.1.0", spec["openapi"])
        self.assertEqual("1.0.0", spec["info"]["version"])
        self.assertEqual(f"http://127.0.0.1:{self.port}", spec["servers"][0]["url"])
        self.assertEqual({"/health", "/v1/state", "/v1/ui", "/v1/commands", "/v1/openapi.json", "/v1/command", "/v1/rpc", "/v1/render-frame"}, set(spec["paths"]))
        self.assertEqual([{"bearerAuth": []}], spec["security"])
        self.assertEqual([], self.client.calls)
        spec["paths"].clear()
        self.assertEqual(8, len(build_openapi()["paths"]))

    def test_missing_invalid_and_nearly_correct_auth(self):
        for value in ("", "test-token-do-not-use", "Bearer wrong", "Bearer test-token-do-not-usf", "Basic test-token-do-not-use"):
            with self.subTest(value=value):
                self.assert_rejected(401, headers={"Authorization": value})

    def test_missing_authorization(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            conn.request("GET", "/health")
            response = conn.getresponse()
            self.assertEqual(401, response.status)
            self.assertEqual("unauthorized", json.loads(response.read())["error"]["code"])
        finally:
            conn.close()
        self.assertEqual([], self.client.calls)

    def test_foreign_origin_and_null_origin_are_refused(self):
        for value in ("https://attacker.invalid", "null", ""):
            with self.subTest(value=value):
                self.assert_rejected(403, headers={"Origin": value})

    def test_host_rebinding_is_refused(self):
        for value in ("attacker.invalid", "127.0.0.1", f"127.0.0.1:{self.port + 1}", f"127.0.0.1:{self.port}.attacker.invalid"):
            with self.subTest(value=value):
                self.assert_rejected(403, headers={"Host": value})
        self.assertEqual(200, self.request(headers={"Host": f"localhost:{self.port}"})[0])

    def test_transfer_encoding_is_rejected(self):
        self.assert_rejected(400, method="POST", path="/v1/rpc", body={}, headers={"Transfer-Encoding": "chunked"})

    def test_expect_is_rejected_before_body(self):
        self.assert_rejected(400, method="POST", path="/v1/rpc", body={}, headers={"Expect": "100-continue"})

    def test_oversized_declared_body_is_rejected_before_read(self):
        self.assert_rejected(413, method="POST", path="/v1/rpc", body="", headers={"Content-Length": str(MAX_BODY + 1)})

    def test_large_header_is_rejected(self):
        self.assert_rejected(431, headers={"X-Large": "x" * 17_000})

    def test_wrong_media_type_or_encoding(self):
        for value in ("text/plain", "application/json; charset=latin1", "application/json; arbitrary=1", ""):
            with self.subTest(value=value):
                self.assert_rejected(415, method="POST", path="/v1/rpc", body={}, headers={"Content-Type": value})

    def test_invalid_json_and_nonfinite_numbers_never_dispatch(self):
        for body in ("{", "[]", "null", "42", '{"method":"x","params":{"x":NaN}}', '{"method":"x","params":{"x":Infinity}}', '{"method":"x","params":{"x":1e400}}', '{"method":"x","method":"y"}', '{"method":"x","params":{"k":1,"k":2}}', b'\xff'):
            with self.subTest(body=body):
                self.assert_rejected(400, method="POST", path="/v1/rpc", body=body)

    def test_unknown_fields_and_parameter_types(self):
        for path, body in [
            ("/v1/rpc", {"method": "editor.state", "path": "/tmp/secret"}),
            ("/v1/command", {"command": "comp.create", "params": []}),
            ("/v1/rpc", {"method": "editor.state", "params": None}),
            ("/v1/rpc", {"method": "bad name"}),
            ("/v1/rpc", {"method": True}),
            ("/v1/rpc", {"method": "x" * 129}),
            ("/v1/rpc", {}),
            ("/v1/render-frame", {"path": "/tmp/output.png"}),
            ("/v1/render-frame", {"base64": False}),
        ]:
            with self.subTest(path=path, body=body):
                self.assert_rejected(400, method="POST", path=path, body=body)

    def test_render_rejects_bool_as_number_and_bad_types(self):
        for body in ({"comp": True}, {"comp": 1.5}, {"comp": ""}, {"comp": []}, {"time": True}, {"time": "1"}, {"time": None}, {"max_side": True}, {"max_side": 1.5}, {"max_side": -1}, {"max_side": 8193}):
            with self.subTest(body=body):
                self.assert_rejected(400, method="POST", path="/v1/render-frame", body=body)

    def test_bad_queries(self):
        for path in ("/v1/state?filter=x", "/v1/commands?filter=x&filter=y", "/v1/commands?unknown=x", "/v1/commands?filter", "/v1/commands?filter=%00", "/v1/commands?filter=" + "x" * 257):
            with self.subTest(path=path):
                self.assert_rejected(400, path=path)

    def test_get_body_is_rejected(self):
        self.assert_rejected(400, body={"method": "editor.state"})

    def test_unknown_routes_and_wrong_methods(self):
        self.assert_rejected(404, path="/files/etc/passwd")
        self.assert_rejected(405, method="GET", path="/v1/rpc")
        self.assert_rejected(405, method="POST", path="/v1/state", body={})
        self.assert_rejected(405, method="DELETE")

    def test_native_errors_are_not_retried(self):
        for code, unknown, status in [("not_connected", False, 503), ("upstream_error", False, 422), ("invalid_request", False, 400), ("request_too_large", False, 413), ("outcome_unknown", True, 502), ("upstream_timeout", True, 504)]:
            with self.subTest(code=code):
                self.client.calls.clear()
                self.client.error = ControlError(code, "native message", outcome_unknown=unknown)
                actual, body, _ = self.request("POST", "/v1/command", {"command": "comp.create"})
                self.assertEqual(status, actual)
                self.assertEqual({"ok": False, "error": self.client.error.as_dict()}, body)
                self.assertEqual(1, len(self.client.calls))

    def test_unexpected_upstream_failure_is_uncertain(self):
        self.client.error = RuntimeError("private upstream information")
        status, body, _ = self.request("POST", "/v1/rpc", {"method": "engine.execute"})
        self.assertEqual(502, status)
        self.assertTrue(body["error"]["outcome_unknown"])
        self.assertNotIn("private", json.dumps(body))
        self.assertEqual(1, len(self.client.calls))

    def test_invalid_upstream_json_is_uncertain(self):
        self.client.result = {"bad": float("nan")}
        status, body, _ = self.request()
        self.assertEqual(502, status)
        self.assertTrue(body["error"]["outcome_unknown"])
        self.assertEqual(1, len(self.client.calls))

    def test_duplicate_sensitive_headers_and_missing_host(self):
        for headers, status in [
            ([f"Host: 127.0.0.1:{self.port}", f"Host: 127.0.0.1:{self.port}"], 403),
            ([f"Host: 127.0.0.1:{self.port}", "Authorization: Bearer wrong"], 401),
            ([], 403),
            ([f"Host: 127.0.0.1:{self.port}", "Content-Length: 0", "Content-Length: 0"], 400),
        ]:
            with self.subTest(headers=headers):
                request = "GET /health HTTP/1.1\r\nAuthorization: Bearer test-token-do-not-use\r\n" + "\r\n".join(headers) + "\r\nConnection: close\r\n\r\n"
                with socket.create_connection(("127.0.0.1", self.port), timeout=2) as conn:
                    conn.sendall(request.encode("ascii"))
                    with conn.makefile("rb") as stream:
                        response_line = stream.readline()
                        self.assertEqual(status, int(response_line.split()[1]), response_line)
                self.assertEqual([], self.client.calls)

    def test_listens_only_on_loopback(self):
        self.assertEqual("127.0.0.1", self.server.server_address[0])


class ServerValidationTests(unittest.TestCase):
    def test_invalid_port_and_token_are_rejected(self):
        for port in (-1, 65536, True, "8767", 1.5):
            with self.subTest(port=port), self.assertRaises(ValueError):
                make_server(FakeClient(), "test-token", port)
        for token in ("", None, "bad token", "bad\nvalue", "é"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                make_server(FakeClient(), token)


if __name__ == "__main__":
    unittest.main()
