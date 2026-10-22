from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from .runtime import ROOT, VERSION, find_app, find_cli, state_directory, token_file, ensure_app, native_environment, cli_capabilities
from .transport import ControlClient, ControlError, strict_json

def output(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))

def parser():
    p = argparse.ArgumentParser(description="EffectCraft native MCP launcher and local HTTP API")
    commands = p.add_subparsers(dest="action", required=True)
    for name in ["doctor", "api", "mcp", "install-codex", "call", "demo"]:
        s = commands.add_parser(name)
        s.add_argument("--port", type=int, default=9877, help="EffectCraft loopback control port")
        s.add_argument("--cli", help="Official effectcraft-cli executable")
        s.add_argument("--app", help="EffectCraft executable or macOS .app")
        s.add_argument("--state-dir", help="Private local runtime directory (default: .state)")
        s.add_argument("--timeout", type=float, default=65)
        s.add_argument("--no-launch", action="store_true", help="Require an already-running controlled app")
        if name == "doctor":
            s.add_argument("--json", action="store_true", help="Output is always JSON")
        if name == "api":
            s.add_argument("--api-port", type=int, default=8767)
        if name == "mcp":
            s.add_argument("--headless", action="store_true")
            s.add_argument("--project", help="Initial project for headless mode only")
        if name == "install-codex":
            s.add_argument("--name", default="effectcraft-codex")
        if name == "call":
            s.add_argument("--method", required=True, help="Native control method, e.g. engine.execute")
            s.add_argument("--params", default="{}", help="JSON object, never evaluated as code")
        if name == "demo":
            s.add_argument("--out", required=True, help="New output directory; existing files are not overwritten")
    return p

def require_version(capabilities):
    if capabilities.get("version") != VERSION:
        raise ValueError(f"This integration is tested with EffectCraft {VERSION}; found {capabilities.get('version')!r}")

def launch(args, client):
    ready = ensure_app(client, app=args.app, state=args.state_dir, no_launch=args.no_launch)
    require_version(ready["capabilities"])
    return ready

def demo(client, out):
    destination = Path(out).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=False)
    name = "Codex Bridge " + datetime.now(timezone.utc).strftime("%Y%m%d %H%M%S")
    before = client.command("project.summary")
    created = client.command("comp.new", {"name": name, "width": 960, "height": 540, "frameRate": 30, "duration": 3})
    client.command("layer.newSolid", {"name": "Background", "color": "#151515", "width": 960, "height": 540})
    layer = client.command("layer.newText", {"name": "Connected", "text": "CODEX + EFFECTCRAFT", "size": 64, "fill": "#e7ff51"})["layer"]
    client.command("prop.set", {"layer": layer, "path": "transform/position", "value": [480,270]})
    client.command("prop.addKey", {"layer": layer, "path": "transform/opacity", "time": 0, "timeBase": "comp", "value": 0})
    client.command("prop.addKey", {"layer": layer, "path": "transform/opacity", "time": 1, "timeBase": "comp", "value": 100})
    client.command("time.set", {"time": 1.5})
    frame = client.request("render.frame", {"comp": name, "time": 1.5, "max_side": 960, "base64": True})
    png = base64.b64decode(frame["png"], validate=True)
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Upstream did not return a PNG")
    (destination / "connected.png").write_bytes(png)
    client.command("file.saveCopy", {"path": str(destination / "connected.ecproj")})
    result = {"comp": name, "created": created, "png": str(destination / "connected.png"), "project_copy": str(destination / "connected.ecproj"), "width": frame["width"], "height": frame["height"], "existing_project_preserved": True}
    (destination / "verification.json").write_text(json.dumps({"before": before, "result": result}, ensure_ascii=False, indent=2))
    return result

def main(argv=None):
    args = parser().parse_args(argv)
    try:
        client = ControlClient(args.port, args.timeout)
        if args.action == "doctor":
            result = {"python": sys.version.split()[0], "control": {"host": "127.0.0.1", "port": args.port}}
            for key, finder, value in [("cli",find_cli,args.cli),("app",find_app,args.app)]:
                try:
                    binary = finder(value)
                    result[key] = str(binary)
                    if key == "cli":
                        result["engine"] = cli_capabilities(binary)
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    result[key + "_error"] = str(exc)
            try:
                result["live"] = client.command("app.capabilities")
            except ControlError as exc:
                result["live_error"] = exc.as_dict()
            output(result)
            return 0 if "cli" in result and "app" in result and result.get("engine",{}).get("version") == VERSION else 1
        state = state_directory(args.state_dir)
        if args.action == "mcp":
            cli = find_cli(args.cli)
            require_version(cli_capabilities(cli))
            native = [str(cli), "mcp"]
            if args.headless:
                native += ["--autosave"]
                if args.project:
                    project = Path(args.project).expanduser().resolve(strict=True)
                    native += ["--project", str(project)]
                else:
                    native += ["--empty"]
            else:
                if args.project:
                    raise ValueError("--project is only for --headless. Live mode never replaces the current project at startup.")
                launch(args, client)
                native += ["--bridge", str(args.port)]
            os.execvpe(str(cli), native, native_environment(state))
        elif args.action == "install-codex":
            # Codex merges its own config; never rewrite the rest of the user's TOML.
            codex = shutil.which("codex")
            if not codex:
                raise ValueError("codex CLI not found on PATH")
            require_version(cli_capabilities(find_cli(args.cli)))
            command = [codex, "mcp", "add", args.name, "--", sys.executable, str(ROOT / "bridge.py"), "mcp", "--port", str(args.port), "--state-dir", str(state), "--timeout", str(args.timeout)]
            if args.no_launch:
                command += ["--no-launch"]
            if args.cli:
                command += ["--cli", str(find_cli(args.cli))]
            if args.app:
                command += ["--app", str(find_app(args.app))]
            subprocess.run(command, check=True)
            output({"configured": args.name, "reload_required": True, "next_step": "Restart this MCP server in Codex/ChatGPT settings, or open a new chat to load its tools."})
        elif args.action == "api":
            from .api import make_server
            path = token_file(state)
            server = make_server(client, path.read_text().strip(), args.api_port)
            try:
                ready = launch(args, client)
                output({"url": f"http://127.0.0.1:{server.server_address[1]}", "token_file": str(path), "app_launched": ready["launched"], "native_control_port": args.port})
                sys.stdout.flush()
                server.serve_forever()
            finally:
                server.server_close()
        elif args.action == "call":
            params = strict_json(args.params)
            if not isinstance(params, dict):
                raise ValueError("--params must be a JSON object")
            launch(args, client)
            output({"ok": True, "result": client.request(args.method, params)})
        elif args.action == "demo":
            launch(args, client)
            output({"ok": True, "result": demo(client, args.out)})
        return 0
    except KeyboardInterrupt:
        return 0
    except ControlError as exc:
        print(json.dumps({"ok": False, "error": exc.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": {"code": "setup_error", "message": str(exc), "outcome_unknown": False}}, ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
