"""OpenCode credential hygiene: nothing a tool reads reaches the stored raw stream or a tail.

The raw stream next to the final file keeps only the ``step_start``/``text``/``step_finish``/
``error`` events, so a ``tool_use`` part (its ``state.output``, ``input`` and ``metadata``)
never lands on disk; ``redaction.redact`` masks OpenCode ``auth.json``-shaped values, also
JSON-escaped inside a JSONL line, without touching ordinary output; the model probe's
stderr tail is redacted; and the worker permission denies the ``read`` and ``bash`` tools
on OpenCode's ``auth.json`` and ``opencode.db*``. Every credential here is synthetic and
assembled at run time.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

import opencode_executor  # noqa: E402
import opencode_transport  # noqa: E402
from redaction import redact  # noqa: E402


def synthetic(label: str) -> str:
    return label + "Q7" * 12


class RawStreamFilterTests(unittest.TestCase):
    def test_only_step_text_finish_and_error_events_are_kept(self) -> None:
        secret = synthetic("tool-output-")
        rows = [{"type": "step_start", "part": {"type": "step-start"}},
                {"type": "tool_use", "part": {"tool": "read", "state": {"input": {"filePath": "/x"}, "output": secret, "metadata": {"preview": secret}}}},
                {"type": "reasoning", "part": {"text": secret}},
                {"type": "text", "part": {"text": "{\"executor_result\": {}}"}},
                {"type": "step_finish", "part": {"reason": "stop", "tokens": {"input": 1}}},
                {"type": "error", "error": {"name": "ProviderAuthError"}}]
        stdout = ("\n".join(json.dumps(row) for row in rows) + "\nnot json " + secret + "\n").encode("utf-8")
        kept = opencode_transport.raw_stream(stdout)
        self.assertNotIn(secret, kept)
        self.assertEqual(["step_start", "text", "step_finish", "error"], [json.loads(line)["type"] for line in kept.splitlines()])

    def test_the_filtered_stream_is_still_classified_the_same(self) -> None:
        fixture = Path(__file__).resolve().parent / "fixtures" / "opencode" / "run_format_json_1.18.34.jsonl"
        self.assertEqual(opencode_transport.classify(fixture.read_bytes(), 0)[0],
                         opencode_transport.classify(opencode_transport.raw_stream(fixture.read_bytes()).encode("utf-8"), 0)[0])
        self.assertNotIn("tool_use", opencode_transport.raw_stream(fixture.read_bytes()))


class AuthShapedRedactionTests(unittest.TestCase):
    def test_auth_json_values_are_masked(self) -> None:
        api, refresh, access = synthetic("ocg"), synthetic("rt"), synthetic("at")
        auth = json.dumps({"opencode-go": {"type": "api", "key": api}, "openai": {"type": "oauth", "refresh": refresh, "access": access, "expires": 17}})
        masked = redact(auth)
        for value in (api, refresh, access):
            self.assertNotIn(value, masked)
        self.assertIn('"expires": 17', masked)

    def test_the_json_escaped_form_inside_a_jsonl_line_is_masked(self) -> None:
        api, refresh = synthetic("ocg"), synthetic("rt")
        line = json.dumps({"type": "tool_use", "part": {"state": {"output": json.dumps({"key": api, "refresh": refresh})}}})
        masked = redact(line)
        self.assertNotIn(api, masked)
        self.assertNotIn(refresh, masked)
        self.assertEqual(dict, type(json.loads(masked)))

    def test_ordinary_key_and_access_values_survive(self) -> None:
        for text in ('{"key": "src/feature.py"}', '{"key": "name"}', '{"access": "read"}', '{"key": "a value with spaces in it"}',
                     '{"key": "tests.test_feature.FeatureTests"}', "Ran 3 tests in 0.01s\nOK"):
            with self.subTest(text=text):
                self.assertEqual(text, redact(text))


class ProbeAndPermissionTests(unittest.TestCase):
    def test_the_probe_stderr_tail_is_redacted(self) -> None:
        secret = synthetic("ocg")

        def run_quiet(argv, timeout, **kwargs):  # noqa: ANN001
            return subprocess.CompletedProcess(argv, 1, "", json.dumps({"key": secret}))

        import tempfile

        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".local" / "share" / "opencode").mkdir(parents=True)
            probe = opencode_executor.model_probe(binary="opencode", model="p/m", probe_timeout=5, tail_characters=500,
                                                  worker_env={"HOME": home, "PATH": "/usr/bin"}, run_quiet=run_quiet)
        self.assertNotIn(secret, probe["probe_stderr_tail"])

    def test_read_and_bash_are_denied_on_the_credential_store(self) -> None:
        for writing in (True, False):
            value = opencode_executor.permission([], writing)
            self.assertEqual({"*": "allow", "*opencode/auth.json": "deny", "*opencode/opencode.db*": "deny"}, value["read"])
            self.assertEqual({"*": "allow", "*auth.json*": "deny", "*opencode.db*": "deny"}, value["bash"])
            self.assertEqual("deny", value["external_directory"]["*"])
            self.assertNotIn('"ask"', json.dumps(value))

    def test_build_passes_the_data_directory_denies_to_the_worker(self) -> None:
        built = opencode_executor.build(binary="opencode", settings={"model": "p/m"}, repository=Path("/r"), schema_file=Path("/s.json"),
                                        read_dirs=[], writing=True, base_env={"HOME": "/home/u", "PATH": "/usr/bin"}, final=Path("/rt/T.result.json"))
        permission = json.loads(built["env"]["OPENCODE_PERMISSION"])
        self.assertEqual("deny", permission["read"]["*opencode/auth.json"])


if __name__ == "__main__":
    unittest.main()
