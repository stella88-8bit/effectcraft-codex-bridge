"""Filesystem and launcher contracts; no real application is opened or closed."""
import contextlib
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

from effectcraft_codex import runtime
from effectcraft_codex import __main__ as cli
from effectcraft_codex.transport import ControlClient, ControlError


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="effectcraft-runtime-test-")
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def binary(self, name="effectcraft-cli"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o700)
        return path

    def test_explicit_cli_and_environment_precedence(self):
        first = self.binary("first")
        second = self.binary("second")
        with patch.dict(os.environ, {"EFFECTCRAFT_CLI": str(first)}):
            self.assertEqual(first.resolve(), runtime.find_cli())
            self.assertEqual(second.resolve(), runtime.find_cli(second))
        with self.assertRaises(ValueError):
            runtime.find_cli(self.root / "missing")
        first.chmod(0o600)
        if os.name != "nt":
            with self.assertRaises(ValueError):
                runtime.find_cli(first)

    def test_cli_prefers_local_runtime_then_path(self):
        local = self.binary(".runtime/bin/effectcraft-cli")
        path = self.binary("path-cli")
        with patch.object(runtime, "ROOT", self.root), patch.object(runtime.shutil, "which", return_value=str(path)):
            self.assertEqual(local.resolve(), runtime.find_cli())
            local.unlink()
            self.assertEqual(path.resolve(), runtime.find_cli())
        with patch.object(runtime, "ROOT", self.root), patch.object(runtime.shutil, "which", return_value=None):
            with self.assertRaises(ValueError):
                runtime.find_cli()

    def test_mac_bundle_uses_declared_executable(self):
        bundle = self.root / "EffectCraft.app"
        contents = bundle / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "EffectCraft"}))
        executable = self.binary("EffectCraft.app/Contents/MacOS/EffectCraft")
        self.assertEqual(executable.resolve(), runtime.find_app(bundle))
        with patch.dict(os.environ, {"EFFECTCRAFT_APP": str(bundle)}):
            self.assertEqual(executable.resolve(), runtime.find_app())
        self.assertEqual(executable.resolve(), runtime.find_app(executable))

    def test_token_created_private_and_reused_without_rotation(self):
        state = runtime.state_directory(self.root / "state")
        path = runtime.token_file(state)
        first = path.read_text()
        self.assertGreaterEqual(len(first.strip()), 32)
        self.assertEqual(path, runtime.token_file(state))
        self.assertEqual(first, path.read_text())
        if os.name != "nt":
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual(0o700, stat.S_IMODE(state.stat().st_mode))

    def test_token_refuses_symlink_and_preserves_target(self):
        target = self.root / "target"
        target.write_text("private-original\n")
        state = runtime.state_directory(self.root / "state")
        try:
            (state / "api-token").symlink_to(target)
        except (NotImplementedError, OSError):
            self.skipTest("Platform does not permit symlink creation")
        with self.assertRaises(ValueError):
            runtime.token_file(state)
        self.assertEqual("private-original\n", target.read_text())

    def test_existing_token_rejects_public_permissions_and_bad_values(self):
        state = runtime.state_directory(self.root / "state")
        path = state / "api-token"
        path.write_text("a" * 40)
        if os.name != "nt":
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                runtime.token_file(state)
        path.chmod(0o600)
        for value in ("short", "a" * 32 + " b", "a" * 32 + "\nb"):
            path.write_text(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.token_file(state)

    def test_existing_controlled_session_is_attached_without_process_launch(self):
        client = Mock(port=9877)
        client.command.return_value = {"version": "0.13.0"}
        with patch.object(runtime.subprocess, "Popen") as popen, patch.object(runtime, "find_app") as find:
            result = runtime.ensure_app(client, state=self.root)
        self.assertFalse(result["launched"])
        client.command.assert_called_once_with("app.capabilities")
        popen.assert_not_called()
        find.assert_not_called()
        self.assertEqual([], list(self.root.iterdir()))

    def test_no_launch_and_uncertain_failure_never_spawn_or_fallback(self):
        for error, no_launch in [
            (ControlError("not_connected", "offline"), True),
            (ControlError("outcome_unknown", "lost", outcome_unknown=True), False),
            (ControlError("upstream_error", "rejected"), False),
        ]:
            client = Mock(port=9877)
            client.command.side_effect = error
            with self.subTest(error=error.code), patch.object(runtime.subprocess, "Popen") as popen:
                with self.assertRaises(ControlError) as caught:
                    runtime.ensure_app(client, state=self.root, no_launch=no_launch)
                self.assertIs(error, caught.exception)
                popen.assert_not_called()

    def test_new_automation_session_uses_separate_config_and_safe_argv(self):
        binary = self.binary("EffectCraft")
        client = Mock(port=10077)
        client.command.side_effect = ControlError("not_connected", "offline")
        ready_client = Mock()
        ready_client.command.return_value = {"version": "0.13.0"}
        process = Mock(pid=765432)
        process.poll.return_value = None
        state = self.root / "isolated"
        with patch.object(runtime, "find_app", return_value=binary), patch.object(runtime, "ControlClient", return_value=ready_client), patch.object(runtime.subprocess, "Popen", return_value=process) as popen:
            result = runtime.ensure_app(client, state=state)
        self.assertTrue(result["launched"])
        args, kwargs = popen.call_args
        self.assertEqual([str(binary), "--control", "10077"], args[0])
        self.assertEqual(str(state.resolve() / "effectcraft-config"), kwargs["env"]["EFFECTCRAFT_CONFIG_DIR"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertNotIn("shell", kwargs)
        process.kill.assert_not_called()
        process.terminate.assert_not_called()
        stored = json.loads((state / "app-instance.json").read_text())
        self.assertEqual(765432, stored["pid"])
        self.assertEqual(10077, stored["port"])

    def test_probe_failure_after_launch_leaves_app_running(self):
        client = Mock(port=10077)
        client.command.side_effect = ControlError("not_connected", "offline")
        probe_client = Mock()
        probe_client.command.side_effect = ControlError("outcome_unknown", "reply lost", outcome_unknown=True)
        process = Mock(pid=765432)
        process.poll.return_value = None
        with patch.object(runtime, "find_app", return_value=self.binary()), patch.object(runtime, "ControlClient", return_value=probe_client), patch.object(runtime.subprocess, "Popen", return_value=process):
            with self.assertRaises(ControlError):
                runtime.ensure_app(client, state=self.root / "state")
        process.kill.assert_not_called()
        process.terminate.assert_not_called()

    def test_cli_version_probe_is_an_empty_headless_query(self):
        binary = self.binary()
        with patch.object(runtime.subprocess, "run", return_value=Mock(stdout='{"version":"0.13.0"}')) as run:
            self.assertEqual({"version": "0.13.0"}, runtime.cli_capabilities(binary))
        args, kwargs = run.call_args
        self.assertEqual([str(binary), "exec", "app.capabilities", "{}", "--empty", "--json"], args[0])
        self.assertTrue(kwargs["check"])
        self.assertEqual(20, kwargs["timeout"])
        self.assertNotIn("shell", kwargs)


class MainLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="effectcraft-main-test-")
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def invoke(self, argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(argv + ["--state-dir", str(self.root)])

    def test_live_mcp_executes_native_bridge_and_does_not_reset_project(self):
        binary = self.root / "effectcraft-cli"
        with patch.object(cli, "find_cli", return_value=binary), patch.object(cli, "cli_capabilities", return_value={"version": "0.13.0"}), patch.object(cli, "launch", return_value={"launched": False}) as launch, patch.object(cli.os, "execvpe") as execute:
            self.assertEqual(0, self.invoke(["mcp", "--port", "10077", "--no-launch"]))
        args = execute.call_args.args
        self.assertEqual(str(binary), args[0])
        self.assertEqual([str(binary), "mcp", "--bridge", "10077"], args[1])
        self.assertNotIn("--empty", args[1])
        self.assertNotIn("--project", args[1])
        self.assertEqual(1, launch.call_count)

    def test_live_mcp_failure_has_no_headless_fallback(self):
        with patch.object(cli, "find_cli", return_value=self.root / "cli"), patch.object(cli, "cli_capabilities", return_value={"version": "0.13.0"}), patch.object(cli, "launch", side_effect=ControlError("not_connected", "not started")), patch.object(cli.os, "execvpe") as execute:
            self.assertEqual(2, self.invoke(["mcp", "--no-launch"]))
        execute.assert_not_called()

    def test_live_mcp_refuses_initial_project_before_launching(self):
        with patch.object(cli, "find_cli", return_value=self.root / "cli"), patch.object(cli, "cli_capabilities", return_value={"version": "0.13.0"}), patch.object(cli, "launch") as launch, patch.object(cli.os, "execvpe") as execute:
            self.assertEqual(2, self.invoke(["mcp", "--project", "must-not-open.ecproj"]))
        launch.assert_not_called()
        execute.assert_not_called()

    def test_headless_is_explicit_durable_and_never_launches_gui(self):
        project = self.root / "saved.ecproj"
        project.write_text("fixture")
        binary = self.root / "cli"
        with patch.object(cli, "find_cli", return_value=binary), patch.object(cli, "cli_capabilities", return_value={"version": "0.13.0"}), patch.object(cli, "launch") as launch, patch.object(cli.os, "execvpe") as execute:
            self.assertEqual(0, self.invoke(["mcp", "--headless", "--project", str(project)]))
        self.assertEqual([str(binary), "mcp", "--autosave", "--project", str(project.resolve())], execute.call_args.args[1])
        launch.assert_not_called()

    def test_wrong_native_version_never_starts_gui_or_mcp(self):
        with patch.object(cli, "find_cli", return_value=self.root / "cli"), patch.object(cli, "cli_capabilities", return_value={"version": "0.12.0"}), patch.object(cli, "launch") as launch, patch.object(cli.os, "execvpe") as execute:
            self.assertEqual(2, self.invoke(["mcp"]))
        launch.assert_not_called()
        execute.assert_not_called()

    def test_install_codex_preserves_no_launch_and_timeout_preferences(self):
        with patch.object(cli.shutil, "which", return_value="/test/codex"), patch.object(cli, "find_cli", return_value=self.root / "cli"), patch.object(cli, "cli_capabilities", return_value={"version": "0.13.0"}), patch.object(cli.subprocess, "run") as run:
            self.assertEqual(0, self.invoke(["install-codex", "--name", "test-effectcraft", "--no-launch", "--timeout", "75"]))
        command = run.call_args.args[0]
        self.assertEqual(["/test/codex", "mcp", "add", "test-effectcraft", "--"], command[:5])
        self.assertIn("--no-launch", command)
        self.assertEqual("75.0", command[command.index("--timeout") + 1])
        self.assertTrue(run.call_args.kwargs["check"])


if __name__ == "__main__":
    unittest.main()
