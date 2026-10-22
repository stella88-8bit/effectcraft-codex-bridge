"""Bounded, single-attempt client for EffectCraft's official control channel."""
from __future__ import annotations
import json
import math
import socket
import threading
import uuid

MAX_REQUEST = 4 * 1024 * 1024
MAX_RESPONSE = 64 * 1024 * 1024

class ControlError(Exception):
    def __init__(self, code: str, message: str, *, outcome_unknown: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.outcome_unknown = outcome_unknown

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "outcome_unknown": self.outcome_unknown}

def strict_json(data):
    def reject(value):
        raise ValueError("Non-finite JSON number: " + value)
    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("JSON number overflow")
        return number
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(data, parse_constant=reject, parse_float=finite, object_pairs_hook=unique)

class ControlClient:
    def __init__(self, port: int = 9877, timeout: float = 65.0):
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Control port must be an integer from 1 to 65535")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Timeout must be a positive finite number")
        self.port, self.timeout = port, timeout
        self._lock = threading.Lock()

    def command(self, command: str, params: dict | None = None):
        if not isinstance(command, str) or not command.strip():
            raise ControlError("invalid_request", "command must be a nonempty string")
        if params is not None and not isinstance(params, dict):
            raise ControlError("invalid_request", "params must be a JSON object")
        return self.request("engine.execute", {"command": command, "params": {} if params is None else params})

    def request(self, method: str, params: dict | None = None):
        if not isinstance(method, str) or not method.strip():
            raise ControlError("invalid_request", "method must be a nonempty string")
        if params is not None and not isinstance(params, dict):
            raise ControlError("invalid_request", "params must be a JSON object")
        request_id = uuid.uuid4().hex
        try:
            wire = (json.dumps({"id": request_id, "method": method, "params": {} if params is None else params}, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        except (ValueError, TypeError, RecursionError) as exc:
            raise ControlError("invalid_request", "Request is not finite JSON") from exc
        if len(wire) > MAX_REQUEST:
            raise ControlError("request_too_large", "Control request exceeds 4 MiB")
        with self._lock:
            try:
                connection = socket.create_connection(("127.0.0.1", self.port), self.timeout)
            except OSError as exc:
                raise ControlError("not_connected", f"EffectCraft control is unavailable on 127.0.0.1:{self.port}") from exc
            with connection:
                try:
                    # A failed send can be partial: never retry a possibly applied edit.
                    connection.sendall(wire)
                    with connection.makefile("rb") as stream:
                        reply = stream.readline(MAX_RESPONSE + 1)
                    if not reply.endswith(b"\n") or len(reply) > MAX_RESPONSE:
                        raise ValueError("Missing, truncated or oversized control reply")
                    result = strict_json(reply)
                    if not isinstance(result, dict) or result.get("id") != request_id or type(result.get("ok")) is not bool:
                        raise ValueError("Control reply id or envelope does not match")
                except (OSError, ValueError, UnicodeError, RecursionError) as exc:
                    raise ControlError("outcome_unknown", "The reply was lost or invalid. Inspect the project before explicitly retrying; the action may already have run.", outcome_unknown=True) from exc
            if not result["ok"]:
                message = str(result.get("error", "EffectCraft rejected the request"))
                if message.strip().lower() == "timeout":
                    raise ControlError("upstream_timeout", "The UI did not reply before its deadline. The queued action may still run; inspect before retrying.", outcome_unknown=True)
                raise ControlError("upstream_error", message)
            return result.get("result")
