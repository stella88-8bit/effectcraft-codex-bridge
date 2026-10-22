"""Input-contract regressions for the standalone client, without a live editor."""
import argparse
from contextlib import redirect_stderr
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "api_client.py"
SPEC = importlib.util.spec_from_file_location("effectcraft_example_client", EXAMPLE)
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)


class ExampleClientInputTests(unittest.TestCase):
    def test_json_preserves_nested_values(self):
        value = '{"n":-0.25,"data":[{"v":1e2}],"flag":true,"empty":null}'
        self.assertEqual(client.json_object(value), {
            "n": -0.25, "data": [{"v": 100.0}], "flag": True, "empty": None,
        })

    def test_duplicate_keys_at_every_object_depth_are_rejected(self):
        for value in ['{"a":1,"a":2}', '{"child":{"a":1,"a":2}}',
                      '{"items":[{"a":1,"a":2}]}']:
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                client.json_object(value)

    def test_nonfinite_json_numbers_are_rejected_recursively(self):
        for value in ['{"x":1e400}', '{"x":-1e400}', '{"x":[{"v":1e400}]}',
                      '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}']:
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                client.json_object(value)

    def test_only_json_objects_are_accepted(self):
        for value in ["[]", "1", '"text"', "null", "true", "not-json"]:
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                client.json_object(value)

    def test_invalid_cli_json_is_rejected_before_client_creation(self):
        for value in ['{"x":1,"x":2}', '{"nested":[1e400]}']:
            with self.subTest(value=value), patch.object(client, "Client") as constructor:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                    client.main(["--token-file", "unused", "command", "comp.new", "--params", value])
                self.assertEqual(raised.exception.code, 2)
                constructor.assert_not_called()

    def test_negative_finite_comp_time_is_preserved(self):
        arguments = client.parser().parse_args([
            "--token-file", "unused", "render", "--time", "-2.75", "--out", "unused.png",
        ])
        self.assertEqual(arguments.time, -2.75)
        self.assertEqual(client.finite_time("0"), 0.0)

    def test_nonfinite_times_are_rejected(self):
        for value in ["nan", "inf", "-inf", "1e400", "invalid"]:
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                client.finite_time(value)

    def test_timeout_must_be_positive_and_finite(self):
        self.assertEqual(client.positive_timeout("0.25"), 0.25)
        for value in ["0", "-0", "-1", "nan", "inf", "-inf", "1e400"]:
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                client.positive_timeout(value)

    def test_invalid_cli_timeout_is_rejected_before_client_creation(self):
        for value in ["0", "-1", "nan", "inf", "1e400"]:
            with self.subTest(value=value), patch.object(client, "Client") as constructor:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                    client.main(["--token-file", "unused", "--timeout", value, "state"])
                self.assertEqual(raised.exception.code, 2)
                constructor.assert_not_called()

    def test_programmatic_client_validates_timeout_before_io(self):
        for value in [0, -1, float("nan"), float("inf")]:
            with self.subTest(value=value), patch.object(client, "read_token") as read_token:
                with self.assertRaises(argparse.ArgumentTypeError):
                    client.Client(Path("unused"), timeout=value)
                read_token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
