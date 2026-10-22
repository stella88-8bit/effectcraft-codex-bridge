"""Find upstream executables and start a separate, recoverable live session."""
from __future__ import annotations
import json
import os
from pathlib import Path
import plistlib
import secrets
import shutil
import subprocess
import time
from .transport import ControlClient, ControlError

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.13.0"

def executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)

def find_cli(explicit=None) -> Path:
    if explicit or os.environ.get("EFFECTCRAFT_CLI"):
        value = Path(explicit or os.environ["EFFECTCRAFT_CLI"]).expanduser().resolve()
        if not executable(value):
            raise ValueError("EFFECTCRAFT_CLI/--cli is not an executable file")
        return value
    suffix = ".exe" if os.name == "nt" else ""
    candidates = [ROOT / ".runtime/bin" / ("effectcraft-cli" + suffix)]
    found = shutil.which("effectcraft-cli")
    if found:
        candidates.append(Path(found))
    if os.name == "nt":
        candidates.append(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "EffectCraft/effectcraft-cli.exe")
    for path in candidates:
        if executable(path):
            return path.resolve()
    raise ValueError("Official effectcraft-cli not found. Set EFFECTCRAFT_CLI or run scripts/fetch_cli.py on macOS.")

def find_app(explicit=None) -> Path:
    value = explicit or os.environ.get("EFFECTCRAFT_APP")
    if value:
        candidates = [Path(value).expanduser()]
    else:
        candidates = [Path("/Applications/EffectCraft.app"), Path.home() / "Applications/EffectCraft.app"]
        found = shutil.which("effectcraft")
        if found:
            candidates.append(Path(found))
        if os.name == "nt":
            candidates.append(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "EffectCraft/effectcraft.exe")
    for path in candidates:
        if path.suffix.lower() == ".app":
            info = path / "Contents/Info.plist"
            if not info.is_file():
                continue
            data = plistlib.loads(info.read_bytes())
            path = path / "Contents/MacOS" / data["CFBundleExecutable"]
        if executable(path):
            return path.resolve()
    raise ValueError("EffectCraft desktop app not found. Install it or set EFFECTCRAFT_APP/--app.")

def state_directory(value=None) -> Path:
    path = Path(value or ROOT / ".state").expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path

def token_file(state: Path) -> Path:
    path = state / "api-token"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Token path must be a regular file, not a symlink")
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise ValueError("Token file must be private (chmod 600)")
        token = path.read_text().strip()
        if len(token) < 32 or any(c.isspace() for c in token):
            raise ValueError("Invalid existing API token")
        return path
    with os.fdopen(fd, "w") as stream:
        stream.write(secrets.token_urlsafe(32) + "\n")
    return path

def probe(client: ControlClient):
    result = client.command("app.capabilities")
    if not isinstance(result, dict) or not result.get("version"):
        raise ValueError("Port did not identify an EffectCraft application")
    return result

def ensure_app(client: ControlClient, *, app=None, state=None, no_launch=False):
    try:
        return {"launched": False, "capabilities": probe(client)}
    except ControlError as exc:
        if exc.code != "not_connected" or no_launch:
            raise
    state = state_directory(state)
    binary = find_app(app)
    config = state / "effectcraft-config"
    config.mkdir(exist_ok=True, mode=0o700)
    env = dict(os.environ)
    env["EFFECTCRAFT_CONFIG_DIR"] = str(config)
    with (state / "effectcraft.log").open("ab") as log:
        process = subprocess.Popen([str(binary), "--control", str(client.port)], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValueError(f"EffectCraft exited; see {state / 'effectcraft.log'}")
        try:
            result = probe(ControlClient(client.port, 2.0))
            (state / "app-instance.json").write_text(json.dumps({"pid": process.pid, "port": client.port, "binary": str(binary), "config": str(config)}, indent=2) + "\n")
            return {"launched": True, "capabilities": result}
        except ControlError as exc:
            if exc.code != "not_connected":
                raise
        time.sleep(0.25)
    raise ValueError(f"Control port did not become ready; inspect {state / 'effectcraft.log'}. The app was not killed.")

def native_environment(state: Path) -> dict:
    env = dict(os.environ)
    env["EFFECTCRAFT_CONFIG_DIR"] = str(state / "effectcraft-config")
    return env

def cli_capabilities(cli: Path):
    result = subprocess.run([str(cli), "exec", "app.capabilities", "{}", "--empty", "--json"], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(result.stdout)
