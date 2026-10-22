"""Transport contract tests using real loopback TCP and ephemeral ports."""
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

from effectcraft_codex.transport import ControlClient, ControlError, strict_json


class NativeServer:
    """Small native-protocol oracle; never touches the editor's control port."""

    def __init__(self, handler):
        self.handler = handler
        self.requests = []
        self.errors = []
        self.active = self.max_active = 0
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.workers = []
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.05)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.accept, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def accept(self):
        while not self.stopped.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            worker = threading.Thread(target=self.handle, args=(conn,), daemon=True)
            self.workers.append(worker)
            worker.start()

    def handle(self, conn):
        with conn:
            conn.settimeout(2)
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                with conn.makefile("rb") as stream:
                    request = json.loads(stream.readline())
                with self.lock:
                    self.requests.append(request)
                reply = self.handler(request)
                if reply is not None:
                    if isinstance(reply, dict):
                        reply = json.dumps(reply, ensure_ascii=False).encode() + b"\n"
                    conn.sendall(reply)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Deliberate client timeouts are part of the tests.
            except Exception as exc:
                self.errors.append(exc)
            finally:
                with self.lock:
                    self.active -= 1

    def __exit__(self, *_):
        self.stopped.set()
        self.listener.close()
        self.thread.join(timeout=2)
        for worker in self.workers:
            worker.join(timeout=2)
        if self.errors:
            raise AssertionError("Fake native server failed: " + repr(self.errors))


class TransportTests(unittest.TestCase):
    def test_native_request_envelope_and_unicode_result_are_preserved(self):
        expected = {"text": "Проверка", "zero": 0, "false": False, "null": None}
        with NativeServer(lambda req: {"id": req["id"], "ok": True, "result": expected}) as server:
            result = ControlClient(server.port, 1).command("comp.new", {"name": "Тест"})
            self.assertEqual(expected, result)
        self.assertEqual(1, len(server.requests))
        request = server.requests[0]
        self.assertIsInstance(request["id"], str)
        self.assertTrue(request["id"])
        self.assertEqual("engine.execute", request["method"])
        self.assertEqual({"command": "comp.new", "params": {"name": "Тест"}}, request["params"])

    def test_explicit_native_rejection_is_not_retried(self):
        with NativeServer(lambda req: {"id": req["id"], "ok": False, "error": "Unknown command"}) as server:
            with self.assertRaises(ControlError) as caught:
                ControlClient(server.port, 1).command("invalid.command")
            self.assertEqual("upstream_error", caught.exception.code)
            self.assertFalse(caught.exception.outcome_unknown)
            self.assertIn("Unknown command", caught.exception.message)
        self.assertEqual(1, len(server.requests))

    def test_native_ui_queue_timeout_has_unknown_outcome(self):
        # The native server's recv_timeout does not cancel the queued UI request.
        with NativeServer(lambda req: {"id": req["id"], "ok": False, "error": "timeout"}) as server:
            with self.assertRaises(ControlError) as caught:
                ControlClient(server.port, 1).command("layer.newText", {"text": "once"})
            self.assertTrue(caught.exception.outcome_unknown)
            self.assertIn(caught.exception.code, {"outcome_unknown", "upstream_timeout"})
        self.assertEqual(1, len(server.requests))

    def test_closed_port_is_known_not_connected(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        with self.assertRaises(ControlError) as caught:
            ControlClient(port, 0.5).command("editor.state")
        self.assertEqual("not_connected", caught.exception.code)
        self.assertFalse(caught.exception.outcome_unknown)

    def test_lost_or_invalid_reply_never_repeats_mutation(self):
        replies = [
            None,
            b"not JSON\n",
            b'{"id":"wrong","ok":true,"result":1}\n',
            b'{"ok":true}\n',
            b"[]\n",
            b"\xff\n",
            b'{"ok":true}',  # No framing newline.
        ]
        for reply in replies:
            with self.subTest(reply=reply):
                with NativeServer(lambda _, reply=reply: reply) as server:
                    with self.assertRaises(ControlError) as caught:
                        ControlClient(server.port, 1).command("layer.newText", {"text": "once"})
                    self.assertEqual("outcome_unknown", caught.exception.code)
                    self.assertTrue(caught.exception.outcome_unknown)
                self.assertEqual(1, len(server.requests))

    def test_nonfinite_overflow_and_duplicate_reply_keys_are_uncertain(self):
        def reply(req):
            return ('{"id":' + json.dumps(req["id"]) + ',"ok":true,' + req["params"]["fragment"] + '}\n').encode()
        fragments = ['"result":NaN', '"result":Infinity', '"result":1e400', '"result":1,"result":2', '"ok":false,"result":0']
        with NativeServer(reply) as server:
            for fragment in fragments:
                with self.subTest(fragment=fragment), self.assertRaises(ControlError) as caught:
                    ControlClient(server.port, 1).request("engine.execute", {"fragment": fragment})
                self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(len(fragments), len(server.requests))

    def test_read_timeout_does_not_retry(self):
        def delayed(req):
            time.sleep(0.15)
            return {"id": req["id"], "ok": True, "result": "applied"}
        with NativeServer(delayed) as server:
            with self.assertRaises(ControlError) as caught:
                ControlClient(server.port, 0.03).command("comp.new")
            self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(1, len(server.requests))

    def test_response_size_limit_is_enforced_without_retry(self):
        with NativeServer(lambda req: {"id": req["id"], "ok": True, "result": "x" * 512}) as server:
            with patch("effectcraft_codex.transport.MAX_RESPONSE", 128):
                with self.assertRaises(ControlError) as caught:
                    ControlClient(server.port, 1).command("comp.new")
            self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(1, len(server.requests))

    def test_invalid_and_oversized_requests_never_connect(self):
        client = ControlClient(1, 1)
        with patch("effectcraft_codex.transport.socket.create_connection") as connect:
            for params in ({"x": float("nan")}, {"x": float("inf")}, {"x": object()}, []):
                with self.subTest(params=params), self.assertRaises(ControlError) as caught:
                    client.request("engine.execute", params)
                self.assertEqual("invalid_request", caught.exception.code)
            with patch("effectcraft_codex.transport.MAX_REQUEST", 128):
                with self.assertRaises(ControlError) as caught:
                    client.command("comp.new", {"name": "x" * 256})
                self.assertEqual("request_too_large", caught.exception.code)
            connect.assert_not_called()

    def test_shared_client_serializes_concurrent_mutations(self):
        def delayed(req):
            time.sleep(0.06)
            return {"id": req["id"], "ok": True, "result": req["params"]["params"]["name"]}
        with NativeServer(delayed) as server:
            client = ControlClient(server.port, 2)
            barrier = threading.Barrier(3)
            results, errors = [], []
            def call(name):
                barrier.wait(timeout=2)
                try:
                    results.append(client.command("comp.new", {"name": name}))
                except Exception as exc:
                    errors.append(exc)
            threads = [threading.Thread(target=call, args=(name,)) for name in ("A", "B")]
            for thread in threads:
                thread.start()
            barrier.wait(timeout=2)
            for thread in threads:
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
            self.assertEqual([], errors)
            self.assertEqual(["A", "B"], sorted(results))
        self.assertEqual(1, server.max_active)
        self.assertEqual(2, len(server.requests))
        self.assertEqual(2, len({request["id"] for request in server.requests}))

    def test_strict_json_and_client_constructor_validate_inputs(self):
        self.assertEqual({"a": [1, 2.5, None]}, strict_json('{"a":[1,2.5,null]}'))
        for value in ('NaN', 'Infinity', '-Infinity', '1e400', '{"a":1,"a":2}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                strict_json(value)
        for port in (0, -1, 65536, True, "9877", 9877.0):
            with self.subTest(port=port), self.assertRaises(ValueError):
                ControlClient(port)
        for timeout in (0, -1, True, float("inf"), float("nan"), "65"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                ControlClient(timeout=timeout)


if __name__ == "__main__":
    unittest.main()
