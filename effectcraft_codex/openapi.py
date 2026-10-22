"""OpenAPI contract for the loopback EffectCraft control API."""
from copy import deepcopy

MAX_BODY = 1_048_576
MAX_SIDE = 8192
NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$"


def build_openapi(port: int = 8767) -> dict:
    """Return a fresh JSON-serializable OpenAPI 3.1 document."""
    result = {
        "openapi": "3.1.0",
        "info": {
            "title": "EffectCraft Codex Bridge",
            "version": "1.0.0",
            "description": (
                "Local API for EffectCraft 0.13.0. Requires a bearer token on every route. "
                "Binds only to 127.0.0.1. Browser Origin requests are refused. "
                "The token grants the native editor's authority, including file and script "
                "operations; this API is not a file sandbox. Requests are never retried. "
                "An outcome_unknown error means execution may have occurred: inspect state "
                "before deciding whether to repeat an operation."
            ),
        },
        "servers": [{"url": f"http://127.0.0.1:{port}"}],
        "security": [{"bearerAuth": []}],
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "schemas": {
                "Success": {
                    "type": "object", "required": ["ok", "result"],
                    "properties": {"ok": {"const": True}, "result": {}},
                    "additionalProperties": False,
                },
                "Error": {
                    "type": "object", "required": ["ok", "error"],
                    "properties": {
                        "ok": {"const": False},
                        "error": {
                            "type": "object", "required": ["code", "message", "outcome_unknown"],
                            "properties": {"code": {"type": "string"}, "message": {"type": "string"}, "outcome_unknown": {"type": "boolean"}},
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
        "paths": {},
    }
    errors = {
        "400": "Invalid JSON, duplicate JSON keys, unknown fields, unsupported query or invalid types/ranges.",
        "401": "Missing or invalid bearer token.",
        "403": "Origin header or invalid/duplicate Host header.",
        "404": "Unknown route.",
        "405": "Unsupported HTTP method.",
        "408": "Incoming request timed out before dispatch.",
        "413": f"JSON body exceeds {MAX_BODY} bytes.",
        "415": "Content-Type must be application/json encoded as UTF-8.",
        "422": "EffectCraft rejected the command or method.",
        "431": "Request headers are too large.",
        "502": "Upstream connection/protocol failure; inspect outcome_unknown.",
        "503": "EffectCraft control channel is unavailable; request was not dispatched.",
        "504": "EffectCraft response timeout; execution may have occurred.",
    }
    responses = {"200": {"description": "Unmodified native result wrapped in a success envelope.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Success"}}}}}
    for status, description in errors.items():
        responses[status] = {"description": description, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}

    def route(path, verb, operation, description, schema=None, parameters=None):
        entry = {"operationId": operation, "summary": description, "responses": deepcopy(responses)}
        if schema is not None:
            entry["requestBody"] = {"required": True, "description": f"UTF-8 JSON object, at most {MAX_BODY} bytes; duplicate keys and non-finite numbers are rejected. Content-Length is required. Transfer-Encoding is forbidden.", "content": {"application/json": {"schema": schema}}}
        if parameters:
            entry["parameters"] = parameters
        result["paths"][path] = {verb: entry}

    route("/health", "get", "health", "Probe editor.state through engine.execute on the live control channel.")
    route("/v1/state", "get", "editorState", "Execute editor.state through engine.execute.")
    route("/v1/commands", "get", "listCommands", "Execute command.list through engine.execute with schemas=true.", parameters=[{"name": "filter", "in": "query", "required": False, "schema": {"type": "string", "maxLength": 256}, "description": "Optional native command-name filter; a duplicate filter is rejected."}])
    route("/v1/ui", "get", "inspectUi", "Read ui.inspect.")
    route("/v1/openapi.json", "get", "openapiDocument", "Read this OpenAPI document; result contains the document.")
    params = {"type": "object", "description": "Native parameters. All nested numbers must be finite."}
    for path, key, operation in [("/v1/command", "command", "executeCommand"), ("/v1/rpc", "method", "invokeMethod")]:
        route(path, "post", operation, "Execute one native command." if key == "command" else "Invoke one native control-channel method.", {"type": "object", "required": [key], "properties": {key: {"type": "string", "pattern": NAME_PATTERN, "maxLength": 128}, "params": params}, "additionalProperties": False})
    route("/v1/render-frame", "post", "renderFrame", "Invoke render.frame with base64=true. Returns native PNG base64 result; no filesystem paths accepted.", {"type": "object", "properties": {"comp": {"oneOf": [{"type": "integer"}, {"type": "string", "minLength": 1, "maxLength": 256}], "description": "Composition integer ID or name."}, "time": {"type": "number", "description": "Finite composition time in seconds; booleans are rejected."}, "max_side": {"type": "integer", "minimum": 0, "maximum": MAX_SIDE, "description": "Maximum image dimension; 0 means full resolution. Booleans are rejected."}}, "additionalProperties": False})
    return result
