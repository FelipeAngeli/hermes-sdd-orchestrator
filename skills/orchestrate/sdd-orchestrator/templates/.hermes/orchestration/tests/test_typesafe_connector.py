"""Behavioral tests for the optional TypeSafe/Jev connector."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime" / "typesafe_connector.py"


def load_connector() -> Any:
    spec = importlib.util.spec_from_file_location("sdd_typesafe_connector", RUNTIME)
    if spec is None or spec.loader is None:
        raise AssertionError("TypeSafe connector is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TypeSafeConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_umask = os.umask(0o077)
        self.previous_tempdir = tempfile.tempdir
        tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve())

    def tearDown(self) -> None:
        tempfile.tempdir = self.previous_tempdir
        os.umask(self.previous_umask)

    def test_preflight_without_api_key_fails_before_network_without_disclosure(self) -> None:
        self.assertTrue(RUNTIME.is_file(), "TypeSafe connector is not shipped")
        with tempfile.TemporaryDirectory(prefix="sdd-typesafe-preflight-") as temp:
            env_file = Path(temp) / ".env"
            result = subprocess.run(
                [sys.executable, str(RUNTIME), "preflight", "--env-file", str(env_file), "--json"],
                text=True,
                capture_output=True,
                timeout=20,
                env={"PATH": ""},
            )

        self.assertEqual(2, result.returncode)
        report = json.loads(result.stdout)
        self.assertEqual("BLOCKED", report["status"])
        self.assertEqual("TYPESAFE_API_KEY_MISSING", report["reason"])
        self.assertNotIn("authorization", result.stdout.casefold())
        self.assertEqual("", result.stderr)

    def test_preflight_prefers_process_environment_and_never_discloses_the_key(self) -> None:
        sentinel = "fixture_process_value"
        with tempfile.TemporaryDirectory(prefix="sdd-typesafe-preflight-") as temp:
            env_file = Path(temp) / ".env"
            env_file.write_text("TYPESAFE_API_KEY=fixture_file_value\n", encoding="utf-8")
            env = {"PATH": "/usr/bin:/bin", "TYPESAFE_API_KEY": sentinel}
            result = subprocess.run(
                [sys.executable, str(RUNTIME), "preflight", "--env-file", str(env_file), "--json"],
                text=True,
                capture_output=True,
                timeout=20,
                env=env,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({"status": "READY", "source": "environment"}, json.loads(result.stdout))
        self.assertNotIn(sentinel, result.stdout + result.stderr)
        self.assertNotIn("fixture_file_value", result.stdout + result.stderr)

    def test_preflight_rejects_whitespace_or_control_characters_in_api_key(self) -> None:
        for invalid in ("bad key", "bad\tkey", " fixture_key_value "):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory(prefix="sdd-typesafe-key-") as temp:
                env_file = Path(temp) / ".env"
                env_file.write_text(f"TYPESAFE_API_KEY={invalid}\n", encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(RUNTIME), "preflight", "--env-file", str(env_file), "--json"],
                    text=True,
                    capture_output=True,
                    timeout=20,
                    env={"PATH": ""},
                )

            self.assertEqual(2, result.returncode)
            self.assertEqual(
                {"status": "BLOCKED", "reason": "TYPESAFE_API_KEY_INVALID"},
                json.loads(result.stdout),
            )
            self.assertNotIn(invalid, result.stdout + result.stderr)

    def test_preflight_rejects_an_invalid_or_symlinked_env_file_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-typesafe-env-invalid-") as temp:
            root = Path(temp)
            invalid = root / "invalid.env"
            invalid.write_bytes(b"\xff\xfe")
            external = root / "external.env"
            external.write_text("TYPESAFE_API_KEY=fixture_only\n", encoding="utf-8")
            symlink = root / "symlink.env"
            symlink.symlink_to(external)
            external_directory = root / "external"
            external_directory.mkdir()
            nested = external_directory / ".env"
            nested.write_text("TYPESAFE_API_KEY=fixture_only\n", encoding="utf-8")
            directory_symlink = root / "linked-directory"
            directory_symlink.symlink_to(external_directory, target_is_directory=True)
            env_files = [invalid, symlink, directory_symlink / ".env"]
            if hasattr(os, "mkfifo"):
                fifo = root / "credential.pipe"
                os.mkfifo(fifo, 0o600)
                env_files.append(fifo)

            for env_file in env_files:
                with self.subTest(env_file=env_file.name):
                    result = subprocess.run(
                        [sys.executable, str(RUNTIME), "preflight", "--env-file", str(env_file), "--json"],
                        text=True,
                        capture_output=True,
                        timeout=20,
                        env={"PATH": ""},
                    )
                    self.assertEqual(2, result.returncode)
                    self.assertEqual(
                        {"status": "BLOCKED", "reason": "TYPESAFE_ENV_INVALID"},
                        json.loads(result.stdout),
                    )
                    self.assertEqual("", result.stderr)

    @unittest.skipUnless(os.name == "posix", "permission-mode test requires POSIX")
    def test_preflight_rejects_a_group_or_world_readable_env_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-typesafe-env-mode-") as temp:
            env_file = Path(temp) / ".env"
            env_file.write_text("TYPESAFE_API_KEY=fixture_only\n", encoding="utf-8")
            env_file.chmod(0o644)
            result = subprocess.run(
                [sys.executable, str(RUNTIME), "preflight", "--env-file", str(env_file), "--json"],
                text=True,
                capture_output=True,
                timeout=20,
                env={"PATH": ""},
            )

        self.assertEqual(2, result.returncode)
        self.assertEqual(
            {"status": "BLOCKED", "reason": "TYPESAFE_ENV_INVALID"},
            json.loads(result.stdout),
        )
        self.assertEqual("", result.stderr)

    def test_evaluate_posts_typed_questions_to_system_one_with_default_jev_model(self) -> None:
        connector = load_connector()
        received: dict[str, Any] = {}
        response_body = json.dumps({
            "model": "jev-1.13.0",
            "answers": {"urgent": {"type": "noul", "noul": 0.95}},
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }).encode("utf-8")

        class Response:
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def read(self, amount: int = -1) -> bytes:
                return response_body

        def urlopen(request: Any, timeout: float) -> Response:
            received.update({
                "url": request.full_url,
                "authorization": request.get_header("Authorization"),
                "content_type": request.get_header("Content-type"),
                "body": json.loads(request.data),
                "timeout": timeout,
            })
            return Response()

        with tempfile.TemporaryDirectory(prefix="sdd-typesafe-evaluate-") as temp:
            root = Path(temp)
            env_file = root / ".env"
            env_file.write_text("TYPESAFE_API_KEY=fixture_key_value\n", encoding="utf-8")
            payload = root / "request.json"
            payload.write_text(json.dumps({
                "state": "Deploy is blocked",
                "questions": {"urgent": {"type": "noul", "instructions": "Is this urgent?"}},
            }), encoding="utf-8")
            stdout = io.StringIO()
            with (
                mock.patch.object(connector, "_open_request", side_effect=urlopen),
                mock.patch.object(sys, "argv", [
                    str(RUNTIME), "evaluate", "--input", str(payload),
                    "--env-file", str(env_file), "--json",
                ]),
                mock.patch.object(connector.os, "environ", {}),
                contextlib.redirect_stdout(stdout),
            ):
                returncode = connector.main()

        self.assertEqual(0, returncode)
        self.assertEqual("https://api.typesafe.ai/v1/systemone", received["url"])
        self.assertEqual("Bearer fixture_key_value", received["authorization"])
        self.assertEqual("application/json", received["content_type"])
        self.assertEqual("jev-latest", received["body"]["model"])
        self.assertEqual("Deploy is blocked", received["body"]["state"])
        report = json.loads(stdout.getvalue())
        self.assertEqual("OK", report["status"])
        self.assertEqual("jev-1.13.0", report["result"]["model"])
        self.assertNotIn("fixture_key_value", stdout.getvalue())

    def test_evaluate_does_not_follow_redirects_with_the_authorization_header(self) -> None:
        connector = load_connector()
        redirected_requests: list[str] = []

        class TargetHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
                redirected_requests.append(self.headers.get("Authorization", ""))
                self.send_response(200)
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                redirected_requests.append(self.headers.get("Authorization", ""))
                self.send_response(200)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_port}/capture")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        threads = [
            threading.Thread(target=target.serve_forever, daemon=True),
            threading.Thread(target=redirect.serve_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            with self.assertRaises(connector.urllib.error.HTTPError) as raised:
                connector._evaluate(
                    f"http://127.0.0.1:{redirect.server_port}/v1/systemone",
                    "fixture_key_value",
                    {"state": "state", "model": "jev-latest", "questions": {"q": {"type": "noul"}}},
                    2.0,
                )
        finally:
            redirect.shutdown()
            target.shutdown()
            redirect.server_close()
            target.server_close()
            for thread in threads:
                thread.join(timeout=5)

        self.assertEqual(302, raised.exception.code)
        raised.exception.close()
        self.assertEqual([], redirected_requests)

    def test_evaluate_normalizes_documented_http_failures_without_echoing_response_bodies(self) -> None:
        expected = {
            401: "TYPESAFE_AUTHENTICATION_FAILED",
            422: "TYPESAFE_REQUEST_REJECTED",
            429: "TYPESAFE_RATE_LIMITED",
            529: "TYPESAFE_OVERLOADED",
        }

        for status, reason in expected.items():
            with self.subTest(status=status):
                connector = load_connector()
                with tempfile.TemporaryDirectory(prefix="sdd-typesafe-http-") as temp:
                    root = Path(temp)
                    (root / ".env").write_text("TYPESAFE_API_KEY=fixture_key_value\n", encoding="utf-8")
                    payload = root / "request.json"
                    payload.write_text(json.dumps({
                        "state": "state",
                        "questions": {"check": {"type": "noul", "instructions": "Check?"}},
                    }), encoding="utf-8")
                    error = connector.urllib.error.HTTPError(
                        connector.DEFAULT_ENDPOINT, status, "failure", {}, io.BytesIO(b"remote-sensitive-body")
                    )
                    stdout = io.StringIO()
                    with (
                        mock.patch.object(connector, "_open_request", side_effect=error),
                        mock.patch.object(sys, "argv", [
                            str(RUNTIME), "evaluate", "--input", str(payload),
                            "--env-file", str(root / ".env"), "--json",
                        ]),
                        mock.patch.object(connector.os, "environ", {}),
                        contextlib.redirect_stdout(stdout),
                    ):
                        returncode = connector.main()

                self.assertEqual(3, returncode)
                self.assertEqual(
                    {"status": "ERROR", "reason": reason, "http_status": status},
                    json.loads(stdout.getvalue()),
                )
                self.assertNotIn("remote-sensitive-body", stdout.getvalue())
                self.assertNotIn("fixture_key_value", stdout.getvalue())
                self.assertTrue(error.fp.closed)

    def test_evaluate_rejects_invalid_local_payload_before_network(self) -> None:
        connector = load_connector()
        with tempfile.TemporaryDirectory(prefix="sdd-typesafe-input-") as temp:
            root = Path(temp)
            (root / ".env").write_text("TYPESAFE_API_KEY=fixture_key_value\n", encoding="utf-8")
            payload = root / "request.json"
            payload.write_text('{"state":"missing questions"}', encoding="utf-8")
            stdout = io.StringIO()
            with (
                mock.patch.object(connector, "_open_request") as urlopen,
                mock.patch.object(sys, "argv", [
                    str(RUNTIME), "evaluate", "--input", str(payload),
                    "--env-file", str(root / ".env"), "--json",
                ]),
                mock.patch.object(connector.os, "environ", {}),
                contextlib.redirect_stdout(stdout),
            ):
                returncode = connector.main()

        self.assertEqual(2, returncode)
        self.assertEqual(
            {"status": "BLOCKED", "reason": "TYPESAFE_INPUT_INVALID"},
            json.loads(stdout.getvalue()),
        )
        urlopen.assert_not_called()

    def test_evaluate_rejects_non_finite_json_and_lone_surrogates_before_network(self) -> None:
        connector = load_connector()
        invalid_payloads = (
            '{"state":NaN,"questions":{"q":{"type":"noul","instructions":"Check?"}}}',
            '{"state":"\\ud800","questions":{"q":{"type":"noul","instructions":"Check?"}}}',
        )
        for invalid_payload in invalid_payloads:
            with self.subTest(payload=invalid_payload), tempfile.TemporaryDirectory(prefix="sdd-typesafe-json-") as temp:
                root = Path(temp)
                (root / ".env").write_text("TYPESAFE_API_KEY=fixture_key_value\n", encoding="utf-8")
                payload = root / "request.json"
                payload.write_text(invalid_payload, encoding="utf-8")
                stdout = io.StringIO()
                with (
                    mock.patch.object(connector, "_open_request") as urlopen,
                    mock.patch.object(sys, "argv", [
                        str(RUNTIME), "evaluate", "--input", str(payload),
                        "--env-file", str(root / ".env"), "--json",
                    ]),
                    mock.patch.object(connector.os, "environ", {}),
                    contextlib.redirect_stdout(stdout),
                ):
                    returncode = connector.main()

                self.assertEqual(2, returncode)
                self.assertEqual(
                    {"status": "BLOCKED", "reason": "TYPESAFE_INPUT_INVALID"},
                    json.loads(stdout.getvalue()),
                )
                urlopen.assert_not_called()

    def test_evaluate_rejects_invalid_timeout_values_before_network(self) -> None:
        connector = load_connector()
        for timeout in ("abc", "0", "-1", "nan", "inf", "301"):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory(prefix="sdd-typesafe-timeout-") as temp:
                root = Path(temp)
                (root / ".env").write_text("TYPESAFE_API_KEY=fixture_key_value\n", encoding="utf-8")
                payload = root / "request.json"
                payload.write_text(json.dumps({
                    "state": "state",
                    "questions": {"check": {"type": "noul", "instructions": "Check?"}},
                }), encoding="utf-8")
                stdout = io.StringIO()
                with (
                    mock.patch.object(connector, "_open_request") as urlopen,
                    mock.patch.object(sys, "argv", [
                        str(RUNTIME), "evaluate", "--input", str(payload),
                        "--env-file", str(root / ".env"), "--timeout", timeout, "--json",
                    ]),
                    mock.patch.object(connector.os, "environ", {}),
                    contextlib.redirect_stdout(stdout),
                ):
                    returncode = connector.main()

                self.assertEqual(2, returncode)
                self.assertEqual(
                    {"status": "BLOCKED", "reason": "TYPESAFE_TIMEOUT_INVALID"},
                    json.loads(stdout.getvalue()),
                )
                urlopen.assert_not_called()

    def test_evaluate_rejects_deeply_nested_json_before_network(self) -> None:
        connector = load_connector()
        with tempfile.TemporaryDirectory(prefix="sdd-typesafe-depth-") as temp:
            root = Path(temp)
            (root / ".env").write_text("TYPESAFE_API_KEY=fixture_key_value\n", encoding="utf-8")
            payload = root / "request.json"
            nested = "[" * 2000 + "0" + "]" * 2000
            payload.write_text(
                '{"state":' + nested + ',"questions":{"q":{"type":"noul","instructions":"Check?"}}}',
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with (
                mock.patch.object(connector, "_open_request") as open_request,
                mock.patch.object(sys, "argv", [
                    str(RUNTIME), "evaluate", "--input", str(payload),
                    "--env-file", str(root / ".env"), "--json",
                ]),
                mock.patch.object(connector.os, "environ", {}),
                contextlib.redirect_stdout(stdout),
            ):
                returncode = connector.main()

        self.assertEqual(2, returncode)
        self.assertEqual(
            {"status": "BLOCKED", "reason": "TYPESAFE_INPUT_INVALID"},
            json.loads(stdout.getvalue()),
        )
        open_request.assert_not_called()

    def test_evaluate_rejects_oversized_input_before_network(self) -> None:
        connector = load_connector()
        with tempfile.TemporaryDirectory(prefix="sdd-typesafe-size-") as temp:
            root = Path(temp)
            (root / ".env").write_text("TYPESAFE_API_KEY=fixture_key_value\n", encoding="utf-8")
            payload = root / "request.json"
            payload.write_bytes(b" " * (connector.MAX_INPUT_BYTES + 1))
            stdout = io.StringIO()
            with (
                mock.patch.object(connector, "_open_request") as open_request,
                mock.patch.object(sys, "argv", [
                    str(RUNTIME), "evaluate", "--input", str(payload),
                    "--env-file", str(root / ".env"), "--json",
                ]),
                mock.patch.object(connector.os, "environ", {}),
                contextlib.redirect_stdout(stdout),
            ):
                returncode = connector.main()

        self.assertEqual(2, returncode)
        self.assertEqual(
            {"status": "BLOCKED", "reason": "TYPESAFE_INPUT_INVALID"},
            json.loads(stdout.getvalue()),
        )
        open_request.assert_not_called()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO test requires POSIX")
    def test_evaluate_rejects_fifo_input_without_blocking(self) -> None:
        connector = load_connector()
        with tempfile.TemporaryDirectory(prefix="sdd-typesafe-input-fifo-") as temp:
            root = Path(temp)
            (root / ".env").write_text("TYPESAFE_API_KEY=fixture_key_value\n", encoding="utf-8")
            payload = root / "request.pipe"
            os.mkfifo(payload, 0o600)
            stdout = io.StringIO()
            with (
                mock.patch.object(connector, "_open_request") as open_request,
                mock.patch.object(sys, "argv", [
                    str(RUNTIME), "evaluate", "--input", str(payload),
                    "--env-file", str(root / ".env"), "--json",
                ]),
                mock.patch.object(connector.os, "environ", {}),
                contextlib.redirect_stdout(stdout),
            ):
                returncode = connector.main()

        self.assertEqual(2, returncode)
        self.assertEqual(
            {"status": "BLOCKED", "reason": "TYPESAFE_INPUT_INVALID"},
            json.loads(stdout.getvalue()),
        )
        open_request.assert_not_called()

    def test_evaluate_normalizes_timeout_transport_and_invalid_response(self) -> None:
        connector = load_connector()

        class Response:
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def read(self, amount: int = -1) -> bytes:
                return b"not-json"

        scenarios = (
            (TimeoutError("sensitive timeout detail"), "TYPESAFE_TIMEOUT"),
            (connector.urllib.error.URLError("sensitive transport detail"), "TYPESAFE_TRANSPORT_ERROR"),
            (Response(), "TYPESAFE_RESPONSE_INVALID"),
        )
        for side_effect, reason in scenarios:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory(prefix="sdd-typesafe-failure-") as temp:
                root = Path(temp)
                (root / ".env").write_text("TYPESAFE_API_KEY=fixture_key_value\n", encoding="utf-8")
                payload = root / "request.json"
                payload.write_text(json.dumps({
                    "state": "state",
                    "questions": {"check": {"type": "noul", "instructions": "Check?"}},
                }), encoding="utf-8")
                stdout = io.StringIO()
                patcher = (
                    mock.patch.object(connector, "_open_request", return_value=side_effect)
                    if reason == "TYPESAFE_RESPONSE_INVALID"
                    else mock.patch.object(connector, "_open_request", side_effect=side_effect)
                )
                with (
                    patcher,
                    mock.patch.object(sys, "argv", [
                        str(RUNTIME), "evaluate", "--input", str(payload),
                        "--env-file", str(root / ".env"), "--json",
                    ]),
                    mock.patch.object(connector.os, "environ", {}),
                    contextlib.redirect_stdout(stdout),
                ):
                    returncode = connector.main()

                self.assertEqual(3, returncode)
                self.assertEqual(
                    {"status": "ERROR", "reason": reason},
                    json.loads(stdout.getvalue()),
                )
                self.assertNotIn("sensitive", stdout.getvalue())
                self.assertNotIn("fixture_key_value", stdout.getvalue())

    def test_evaluate_normalizes_truncated_or_reset_response_reads(self) -> None:
        connector = load_connector()

        class Response:
            def __init__(self, failure: BaseException) -> None:
                self.failure = failure

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def read(self, amount: int = -1) -> bytes:
                raise self.failure

        scenarios = (
            (connector.http.client.IncompleteRead(b"partial", 10), "TYPESAFE_RESPONSE_INVALID"),
            (ConnectionResetError("sensitive reset detail"), "TYPESAFE_TRANSPORT_ERROR"),
        )
        for failure, reason in scenarios:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory(prefix="sdd-typesafe-read-") as temp:
                root = Path(temp)
                (root / ".env").write_text("TYPESAFE_API_KEY=fixture_key_value\n", encoding="utf-8")
                payload = root / "request.json"
                payload.write_text(json.dumps({
                    "state": "state",
                    "questions": {"check": {"type": "noul", "instructions": "Check?"}},
                }), encoding="utf-8")
                stdout = io.StringIO()
                with (
                    mock.patch.object(connector, "_open_request", return_value=Response(failure)),
                    mock.patch.object(sys, "argv", [
                        str(RUNTIME), "evaluate", "--input", str(payload),
                        "--env-file", str(root / ".env"), "--json",
                    ]),
                    mock.patch.object(connector.os, "environ", {}),
                    contextlib.redirect_stdout(stdout),
                ):
                    returncode = connector.main()

                self.assertEqual(3, returncode)
                self.assertEqual({"status": "ERROR", "reason": reason}, json.loads(stdout.getvalue()))
                self.assertNotIn("sensitive", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
