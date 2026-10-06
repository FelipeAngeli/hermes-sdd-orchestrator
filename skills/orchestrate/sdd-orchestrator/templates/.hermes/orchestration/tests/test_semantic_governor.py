"""Automatic, cached Jev governance for semantic SDD decisions."""
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
SCRIPT = RUNTIME / "semantic_governor.py"


def load_governor() -> Any:
    spec = importlib.util.spec_from_file_location("sdd_semantic_governor", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("semantic governor is not importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def request() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ticket": "APP-1",
        "state": {
            "request": "Change the shared payment contract",
            "evidence": ["src/payments/api.py is imported by three modules"],
        },
        "questions": {
            "risk": {
                "type": "choice",
                "instructions": "Classify the smallest safe SDD risk path.",
                "criteria": {
                    "LOW": "isolated; no contract, shared state, or external effect",
                    "MEDIUM": "one module; existing contracts unchanged",
                    "HIGH": "shared contract, persistence, authorization, or money",
                    "CRITICAL": "credentials, migrations, production data, or irreversible effect",
                },
            },
            "needs_security_review": {
                "type": "noul",
                "instructions": "Does a pending decision require the security reviewer?",
            },
        },
    }


def response(*, risk_confidence: float = 0.82, security: float = 0.12) -> dict[str, Any]:
    return {
        "status": "OK",
        "result": {
            "model": "jev-1.13.0",
            "answers": {
                "risk": {
                    "type": "choice",
                    "choice": "HIGH",
                    "probabilities": {"LOW": 0.02, "MEDIUM": 0.16, "HIGH": 0.82, "CRITICAL": 0.0},
                    "confidence": risk_confidence,
                },
                "needs_security_review": {"type": "noul", "noul": security},
            },
            "usage": {"input_tokens": 30, "output_tokens": 8},
        },
        "provider": "jev-ai",
        "billing": {"credits_charged": "1"},
    }


def project_setup(typesafe_answer: str) -> str:
    return f'''# SDD Project Setup

```yaml
schema_version: 1
status: PENDING
answers:
  issue_tracker: UNRESOLVED
  obsidian: UNRESOLVED
  typesafe_ai: {typesafe_answer}
  project_tools: UNRESOLVED
```
'''


class SemanticGovernorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_tempdir = tempfile.tempdir
        tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve())

    def tearDown(self) -> None:
        tempfile.tempdir = self.previous_tempdir

    def test_one_batched_live_call_accepts_only_decisions_at_or_above_point_seven(self) -> None:
        governor = load_governor()
        calls: list[dict[str, Any]] = []

        report = governor.decide(request(), lambda payload: calls.append(payload) or response())

        self.assertEqual(1, len(calls))
        self.assertEqual(set(request()["questions"]), set(calls[0]["questions"]))
        self.assertEqual("DECIDED", report["status"])
        self.assertEqual("LIVE_JEV", report["provenance"])
        self.assertEqual("HIGH", report["decisions"]["risk"]["value"])
        self.assertEqual(False, report["decisions"]["needs_security_review"]["value"])
        self.assertEqual(0.88, report["decisions"]["needs_security_review"]["confidence"])
        self.assertEqual(2, report["summary"]["decided"])
        self.assertEqual(0, report["summary"]["review"])

    def test_confidence_below_point_seven_never_invents_a_decision(self) -> None:
        governor = load_governor()

        report = governor.decide(request(), lambda payload: response(risk_confidence=0.699999))

        self.assertEqual("REVIEW", report["status"])
        self.assertIsNone(report["decisions"]["risk"]["value"])
        self.assertEqual("BELOW_THRESHOLD", report["decisions"]["risk"]["reason"])
        self.assertEqual(1, report["summary"]["decided"])
        self.assertEqual(1, report["summary"]["review"])

    def test_exact_threshold_is_accepted(self) -> None:
        governor = load_governor()
        report = governor.decide(request(), lambda payload: response(risk_confidence=0.70, security=0.30))
        self.assertEqual("DECIDED", report["status"])
        self.assertEqual("HIGH", report["decisions"]["risk"]["value"])
        self.assertEqual(False, report["decisions"]["needs_security_review"]["value"])

    def test_malformed_or_failed_remote_result_routes_every_question_to_review(self) -> None:
        governor = load_governor()
        for remote in (
            {"status": "ERROR", "reason": "TYPESAFE_TIMEOUT", "outcome": "UNCERTAIN"},
            {"status": "OK", "result": {"model": "jev", "answers": {}, "usage": {}}},
        ):
            with self.subTest(remote=remote):
                report = governor.decide(request(), lambda payload, remote=remote: remote)
                self.assertEqual("REVIEW", report["status"])
                self.assertEqual(0, report["summary"]["decided"])
                self.assertEqual(2, report["summary"]["review"])

    def test_cache_reuses_the_same_semantic_fingerprint_without_a_paid_call(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cache-") as temp:
            cache = Path(temp) / "cache.json"
            first = governor.decide(request(), lambda payload: response(), cache_path=cache)
            second = governor.decide(
                json.loads(json.dumps(request(), sort_keys=True)),
                lambda payload: self.fail("cache miss caused a second paid call"),
                cache_path=cache,
            )

        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual("CACHE", second["provenance"])
        self.assertEqual(first["decisions"], second["decisions"])

    def test_cache_persists_in_flight_tombstone_before_evaluation_then_replaces_it(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-precall-") as temp:
            cache = Path(temp) / "cache.json"
            events: list[str] = []
            original_write = governor._write_cache_at

            def write(
                parent: int, name: str, value: dict[str, Any], **kwargs: Any
            ) -> None:
                entry = value["entries"][governor.fingerprint(request())]
                events.append(f"write:{entry['reason'] if 'reason' in entry else entry['status']}")
                original_write(parent, name, value, **kwargs)

            def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
                persisted = json.loads(cache.read_text(encoding="utf-8"))
                entry = persisted["entries"][governor.fingerprint(request())]
                self.assertEqual("JEV_GOVERNANCE_IN_FLIGHT_UNCERTAIN", entry["reason"])
                events.append("evaluate")
                return response()

            with mock.patch.object(governor, "_write_cache_at", side_effect=write):
                report = governor.decide(request(), evaluate, cache_path=cache)

            persisted = json.loads(cache.read_text(encoding="utf-8"))

        self.assertEqual(
            ["write:JEV_GOVERNANCE_IN_FLIGHT_UNCERTAIN", "evaluate", "write:DECIDED"],
            events,
        )
        self.assertEqual("DECIDED", report["status"])
        self.assertEqual("DECIDED", persisted["entries"][report["fingerprint"]]["status"])

    def test_cache_fingerprint_changes_with_material_state_or_questions(self) -> None:
        governor = load_governor()
        base = governor.fingerprint(request(), provider="typesafe")
        changed_state = request()
        changed_state["state"]["evidence"].append("new evidence")
        changed_question = request()
        changed_question["questions"]["risk"]["criteria"]["HIGH"] = "changed meaning"
        self.assertNotEqual(base, governor.fingerprint(changed_state, provider="typesafe"))
        self.assertNotEqual(base, governor.fingerprint(changed_question, provider="typesafe"))
        self.assertNotEqual(base, governor.fingerprint(request(), provider="jev-ai"))

    def test_cache_tombstone_prevents_retrying_failed_or_uncertain_requests(self) -> None:
        governor = load_governor()
        failures = (
            {"status": "ERROR", "reason": "TYPESAFE_TIMEOUT", "outcome": "UNCERTAIN"},
            {"status": "ERROR", "reason": "JEV_GOVERNANCE_CONNECTOR_INVALID"},
            {"status": "ERROR", "reason": "UNEXPECTED_PROVIDER_FAILURE"},
        )
        for remote in failures:
            with self.subTest(remote=remote), tempfile.TemporaryDirectory(prefix="sdd-jev-failed-cache-") as temp:
                cache = Path(temp) / "cache.json"
                first = governor.decide(request(), lambda payload, remote=remote: remote, cache_path=cache)
                second = governor.decide(
                    request(),
                    lambda payload: self.fail("a failed fingerprint was billed twice"),
                    cache_path=cache,
                )

                self.assertEqual("REVIEW", first["status"])
                self.assertEqual("CACHE", second["provenance"])
                self.assertEqual(first["reason"], second["reason"])

    def test_windows_fails_closed_before_project_state_or_network_access(self) -> None:
        governor = load_governor()
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(governor, "_platform_name", return_value="nt", create=True),
            mock.patch.object(
                governor,
                "_load_project_setup_consent",
                side_effect=AssertionError("Windows platform refusal read project state"),
            ),
            mock.patch.object(
                governor.subprocess,
                "run",
                side_effect=AssertionError("Windows platform refusal reached network boundary"),
            ),
            mock.patch.object(sys, "argv", [
                str(SCRIPT), "decide", "--input", "request.json", "--json",
            ]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            returncode = governor.main()

        self.assertEqual(2, returncode)
        self.assertEqual(
            {"status": "BLOCKED", "reason": "JEV_GOVERNANCE_PLATFORM_UNSUPPORTED"},
            json.loads(stdout.getvalue()),
        )
        self.assertEqual("", stderr.getvalue())

    def test_concurrent_same_fingerprint_makes_only_one_paid_call(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cache-lock-") as temp:
            cache = Path(temp) / "cache.json"
            start = threading.Barrier(3)
            calls = 0
            calls_lock = threading.Lock()
            reports: list[dict[str, Any]] = []
            errors: list[BaseException] = []

            def evaluator(payload: dict[str, Any]) -> dict[str, Any]:
                nonlocal calls
                with calls_lock:
                    calls += 1
                time.sleep(0.15)
                return response()

            def invoke() -> None:
                try:
                    start.wait()
                    reports.append(governor.decide(request(), evaluator, cache_path=cache))
                except BaseException as error:
                    errors.append(error)

            threads = [threading.Thread(target=invoke) for _ in range(2)]
            for thread in threads:
                thread.start()
            start.wait()
            for thread in threads:
                thread.join(timeout=5)

            self.assertFalse(errors, [(repr(error), repr(error.__cause__)) for error in errors])
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(1, calls)
            self.assertEqual({"LIVE_JEV", "CACHE"}, {report["provenance"] for report in reports})

    def test_concurrent_distinct_fingerprints_preserve_both_cache_entries(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cache-merge-") as temp:
            cache = Path(temp) / "cache.json"
            start = threading.Barrier(3)
            errors: list[BaseException] = []
            requests = [request(), request()]
            requests[1]["ticket"] = "APP-2"

            def invoke(value: dict[str, Any]) -> None:
                try:
                    start.wait()
                    governor.decide(
                        value,
                        lambda payload: time.sleep(0.1) or response(),
                        cache_path=cache,
                    )
                except BaseException as error:
                    errors.append(error)

            threads = [threading.Thread(target=invoke, args=(value,)) for value in requests]
            for thread in threads:
                thread.start()
            start.wait()
            for thread in threads:
                thread.join(timeout=5)

            self.assertFalse(errors)
            for value in requests:
                cached = governor.decide(
                    value,
                    lambda payload: self.fail("concurrent write lost a cache entry"),
                    cache_path=cache,
                )
                self.assertEqual("CACHE", cached["provenance"])

    @unittest.skipUnless(os.name == "posix", "retained directory descriptors require POSIX")
    def test_parent_replacement_during_evaluation_stays_in_the_locked_directory(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cache-parent-race-") as temp:
            root = Path(temp)
            active = root / "active"
            retained = root / "retained"
            active.mkdir()
            cache = active / "cache.json"

            def evaluator(payload: dict[str, Any]) -> dict[str, Any]:
                active.rename(retained)
                active.mkdir()
                return response()

            report = governor.decide(request(), evaluator, cache_path=cache)

            self.assertEqual("DECIDED", report["status"])
            self.assertFalse((active / "cache.json").exists())
            persisted = json.loads((retained / "cache.json").read_text(encoding="utf-8"))
            entry = persisted["entries"][report["fingerprint"]]
            self.assertEqual("DECIDED", entry["status"])
            self.assertTrue((retained / ".cache.json.lock").is_file())
            self.assertFalse((active / ".cache.json.lock").exists())

    @unittest.skipUnless(os.name == "posix", "cache locks require POSIX")
    def test_cache_lock_is_owner_only_and_symlinks_are_refused(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-lock-mode-") as temp:
            root = Path(temp)
            cache = root / "cache.json"
            governor.decide(request(), lambda payload: response(), cache_path=cache)
            lock_path = root / ".cache.json.lock"
            metadata = lock_path.stat()
            self.assertEqual(0, stat.S_IMODE(metadata.st_mode) & 0o077)
            self.assertEqual(os.getuid(), metadata.st_uid)

            lock_path.chmod(0o400)
            cached = governor.decide(
                request(),
                lambda payload: self.fail("read-only private lock caused a paid call"),
                cache_path=cache,
            )
            self.assertEqual("CACHE", cached["provenance"])

            lock_path.unlink()
            target = root / "target"
            target.write_text("not a lock", encoding="utf-8")
            lock_path.symlink_to(target)
            with self.assertRaisesRegex(governor.GovernanceError, "JEV_GOVERNANCE_CACHE_LOCK_FAILED"):
                governor.decide(
                    request(),
                    lambda payload: self.fail("unsafe lock reached evaluator"),
                    cache_path=cache,
                )

    def test_malformed_cache_entry_is_refused_without_evaluation(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cache-invalid-") as temp:
            cache = Path(temp) / "cache.json"
            digest = governor.fingerprint(request())
            cache.write_text(json.dumps({
                "schema_version": 1,
                "entries": {digest: {"status": "DECIDED", "decisions": {}}},
            }), encoding="utf-8")
            with self.assertRaisesRegex(governor.GovernanceError, "JEV_GOVERNANCE_CACHE_INVALID"):
                governor.decide(
                    request(),
                    lambda payload: self.fail("invalid cache entry reached evaluator"),
                    cache_path=cache,
                )

    def test_invalid_request_is_refused_before_evaluation(self) -> None:
        governor = load_governor()
        invalid = request()
        invalid["questions"]["risk"]["type"] = "score"
        with self.assertRaisesRegex(governor.GovernanceError, "JEV_GOVERNANCE_INPUT_INVALID"):
            governor.decide(invalid, lambda payload: self.fail("invalid request reached evaluator"))

    def test_cli_blocks_false_missing_and_legacy_consent_before_preflight_or_network(self) -> None:
        governor = load_governor()
        answers = {
            "false": '{"install":true,"automatic_semantic_governance":false}',
            "missing": "UNRESOLVED",
            "legacy": '{"install":true}',
        }
        for label, answer in answers.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(prefix="sdd-jev-consent-") as temp:
                root = Path(temp)
                input_path = root / "request.json"
                setup_path = root / "PROJECT_SETUP.md"
                cache_path = root / "cache.json"
                input_path.write_text(json.dumps(request()), encoding="utf-8")
                setup_path.write_text(project_setup(answer), encoding="utf-8")
                stdout, stderr = io.StringIO(), io.StringIO()
                with (
                    mock.patch.object(
                        governor.subprocess,
                        "run",
                        side_effect=AssertionError("consent failure reached connector preflight or network"),
                    ),
                    mock.patch.object(sys, "argv", [
                        str(SCRIPT), "decide", "--input", str(input_path),
                        "--project-setup", str(setup_path), "--cache", str(cache_path), "--json",
                    ]),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    returncode = governor.main()

                self.assertEqual(2, returncode)
                self.assertEqual(
                    {"status": "BLOCKED", "reason": "JEV_GOVERNANCE_CONSENT_REQUIRED"},
                    json.loads(stdout.getvalue()),
                )
                self.assertEqual("", stderr.getvalue())
                self.assertFalse(cache_path.exists())

    def test_cli_process_blocks_disabled_consent_without_creating_cache(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-jev-consent-e2e-") as temp:
            root = Path(temp)
            input_path = root / "request.json"
            setup_path = root / "PROJECT_SETUP.md"
            cache_path = root / "cache.json"
            input_path.write_text(json.dumps(request()), encoding="utf-8")
            setup_path.write_text(
                project_setup('{"install":true,"automatic_semantic_governance":false}'),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "decide",
                    "--input",
                    str(input_path),
                    "--project-setup",
                    str(setup_path),
                    "--cache",
                    str(cache_path),
                    "--json",
                ],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

        self.assertEqual(2, completed.returncode)
        self.assertEqual(
            {"status": "BLOCKED", "reason": "JEV_GOVERNANCE_CONSENT_REQUIRED"},
            json.loads(completed.stdout),
        )
        self.assertEqual("", completed.stderr)
        self.assertFalse(cache_path.exists())

    def test_cli_requires_ready_local_preflight_immediately_before_evaluation(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-authorized-") as temp:
            root = Path(temp)
            input_path = root / "request.json"
            setup_path = root / "PROJECT_SETUP.md"
            input_path.write_text(json.dumps(request()), encoding="utf-8")
            setup_path.write_text(
                project_setup('{"install":true,"automatic_semantic_governance":true}'),
                encoding="utf-8",
            )
            commands: list[list[str]] = []

            def connector(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                action = command[2]
                if action == "preflight":
                    return subprocess.CompletedProcess(command, 0, json.dumps({"status": "READY"}), "")
                self.fail(f"unexpected connector action: {action}")

            class EvaluationProcess:
                returncode = 0

                def communicate(self, *, input: bytes, timeout: float) -> tuple[bytes, bytes]:
                    self.assert_preflight()
                    return json.dumps(response()).encode(), b""

                def assert_preflight(self) -> None:
                    self_outer.assertEqual("preflight", commands[-2][2])

            self_outer = self

            def start_connector(command: list[str], **kwargs: Any) -> EvaluationProcess:
                commands.append(command)
                self.assertEqual("evaluate", command[2])
                return EvaluationProcess()

            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(governor.subprocess, "run", side_effect=connector),
                mock.patch.object(governor.subprocess, "Popen", side_effect=start_connector),
                mock.patch.object(sys, "argv", [
                    str(SCRIPT), "decide", "--input", str(input_path),
                    "--project-setup", str(setup_path), "--cache", str(root / "cache.json"),
                    "--progress", str(root / "missing-progress.json"), "--json",
                ]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                returncode = governor.main()

        self.assertEqual(0, returncode)
        self.assertEqual(["preflight", "evaluate"], [command[2] for command in commands])
        self.assertEqual("DECIDED", json.loads(stdout.getvalue())["status"])

    def test_cli_closes_progress_when_connector_process_does_not_start(self) -> None:
        governor = load_governor()
        progress_spec = importlib.util.spec_from_file_location(
            "sdd_terminal_progress_for_start_failure", RUNTIME / "terminal_progress.py"
        )
        assert progress_spec is not None and progress_spec.loader is not None
        progress_module = importlib.util.module_from_spec(progress_spec)
        sys.modules[progress_spec.name] = progress_module
        progress_spec.loader.exec_module(progress_module)
        with tempfile.TemporaryDirectory(prefix="sdd-jev-start-failure-") as temp:
            root = Path(temp)
            input_path = root / "request.json"
            setup_path = root / "PROJECT_SETUP.md"
            progress_path = root / "progress.json"
            input_path.write_text(json.dumps(request()), encoding="utf-8")
            setup_path.write_text(
                project_setup('{"install":true,"automatic_semantic_governance":true}'),
                encoding="utf-8",
            )
            progress_module.save_progress(
                progress_path,
                progress_module.new_progress("openai-codex", "PLAN"),
            )
            ready = subprocess.CompletedProcess(
                ["connector", "preflight"], 0, json.dumps({"status": "READY"}), ""
            )
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(governor.subprocess, "run", return_value=ready),
                mock.patch.object(
                    governor.subprocess,
                    "Popen",
                    side_effect=OSError("fixture process construction failure"),
                ),
                mock.patch.object(sys, "argv", [
                    str(SCRIPT), "decide", "--input", str(input_path),
                    "--project-setup", str(setup_path), "--cache", str(root / "cache.json"),
                    "--progress", str(progress_path), "--json",
                ]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                returncode = governor.main()
            saved = progress_module.load_progress(progress_path)

        self.assertEqual(2, returncode)
        self.assertEqual(
            "JEV_GOVERNANCE_EVALUATION_NOT_STARTED",
            json.loads(stdout.getvalue())["reason"],
        )
        self.assertFalse(saved["jev"]["active"])
        self.assertEqual("BLOCKED", saved["jev"]["status"])

    @unittest.skipUnless(os.name == "posix", "descriptor-anchored paths require POSIX")
    def test_project_setup_rejects_a_symlinked_parent(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-setup-path-") as temp:
            root = Path(temp)
            actual = root / "actual"
            actual.mkdir()
            (actual / "PROJECT_SETUP.md").write_text(
                project_setup('{"install":true,"automatic_semantic_governance":true}'),
                encoding="utf-8",
            )
            linked = root / "linked"
            linked.symlink_to(actual, target_is_directory=True)

            with self.assertRaisesRegex(
                governor.GovernanceError, "JEV_GOVERNANCE_PROJECT_SETUP_INVALID"
            ):
                governor._load_project_setup_consent(linked / "PROJECT_SETUP.md")

    def test_project_setup_rejects_duplicate_consent_keys(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-setup-json-") as temp:
            setup_path = Path(temp) / "PROJECT_SETUP.md"
            setup_path.write_text(
                project_setup(
                    '{"install":false,"install":true,"automatic_semantic_governance":true}'
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                governor.GovernanceError, "JEV_GOVERNANCE_CONSENT_REQUIRED"
            ):
                governor._load_project_setup_consent(setup_path)

    def test_project_setup_requires_literal_boolean_consent_values(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-setup-types-") as temp:
            setup_path = Path(temp) / "PROJECT_SETUP.md"
            setup_path.write_text(
                project_setup('{"install":1,"automatic_semantic_governance":1}'),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                governor.GovernanceError, "JEV_GOVERNANCE_CONSENT_REQUIRED"
            ):
                governor._load_project_setup_consent(setup_path)

    def test_project_setup_ignores_consent_text_outside_the_yaml_record(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-setup-fence-") as temp:
            setup_path = Path(temp) / "PROJECT_SETUP.md"
            setup_path.write_text(
                '  typesafe_ai: {"install":true,"automatic_semantic_governance":true}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                governor.GovernanceError, "JEV_GOVERNANCE_PROJECT_SETUP_INVALID"
            ):
                governor._load_project_setup_consent(setup_path)

    def test_project_setup_requires_consent_inside_answers_section(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-setup-answers-") as temp:
            setup_path = Path(temp) / "PROJECT_SETUP.md"
            setup_path.write_text(
                '''# SDD Project Setup
```yaml
schema_version: 1
  typesafe_ai: {"install":true,"automatic_semantic_governance":true}
answers:
  issue_tracker: none
```
''',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                governor.GovernanceError, "JEV_GOVERNANCE_CONSENT_REQUIRED"
            ):
                governor._load_project_setup_consent(setup_path)

    @unittest.skipUnless(os.name == "posix", "descriptor-anchored paths require POSIX")
    def test_request_rejects_a_symlinked_parent_directory(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-input-path-") as temp:
            root = Path(temp)
            actual = root / "actual"
            actual.mkdir()
            input_path = actual / "request.json"
            input_path.write_text(json.dumps(request()), encoding="utf-8")
            linked = root / "linked"
            linked.symlink_to(actual, target_is_directory=True)

            with self.assertRaisesRegex(governor.GovernanceError, "JEV_GOVERNANCE_INPUT_INVALID"):
                governor._load_request(linked / "request.json")

    @unittest.skipUnless(os.name == "posix", "descriptor-anchored paths require POSIX")
    def test_cache_rejects_a_symlinked_parent_before_evaluation(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cache-path-") as temp:
            root = Path(temp)
            actual = root / "actual"
            actual.mkdir()
            linked = root / "linked"
            linked.symlink_to(actual, target_is_directory=True)

            with self.assertRaisesRegex(governor.GovernanceError, "JEV_GOVERNANCE_CACHE_INVALID"):
                governor.decide(
                    request(),
                    lambda payload: self.fail("unsafe cache path reached evaluator"),
                    cache_path=linked / "cache.json",
                )

    @unittest.skipUnless(os.name == "posix", "cache permissions require POSIX")
    def test_cache_rejects_group_or_world_permissions_before_evaluation(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cache-mode-") as temp:
            cache = Path(temp) / "cache.json"
            cache.write_text(json.dumps({"schema_version": 1, "entries": {}}), encoding="utf-8")
            cache.chmod(0o640)

            with self.assertRaisesRegex(governor.GovernanceError, "JEV_GOVERNANCE_CACHE_INVALID"):
                governor.decide(
                    request(),
                    lambda payload: self.fail("public cache reached evaluator"),
                    cache_path=cache,
                )

    @unittest.skipUnless(os.name == "posix", "cache permissions require POSIX")
    def test_cache_rejects_owner_execute_permission_before_evaluation(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cache-mode-") as temp:
            cache = Path(temp) / "cache.json"
            cache.write_text(json.dumps({"schema_version": 1, "entries": {}}), encoding="utf-8")
            cache.chmod(0o700)

            with self.assertRaisesRegex(governor.GovernanceError, "JEV_GOVERNANCE_CACHE_INVALID"):
                governor.decide(
                    request(),
                    lambda payload: self.fail("executable cache reached evaluator"),
                    cache_path=cache,
                )

    @unittest.skipUnless(os.name == "posix", "descriptor-anchored paths require POSIX")
    def test_cache_write_rejects_a_symlinked_parent_directory(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cache-write-path-") as temp:
            root = Path(temp)
            actual = root / "actual"
            actual.mkdir()
            linked = root / "linked"
            linked.symlink_to(actual, target_is_directory=True)

            with self.assertRaisesRegex(governor.GovernanceError, "JEV_GOVERNANCE_CACHE_INVALID"):
                governor._write_cache(linked / "cache.json", {"schema_version": 1, "entries": {}})

    def test_cache_read_errors_are_normalized_before_evaluation(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cache-read-error-") as temp:
            cache = Path(temp) / "cache.json"
            with mock.patch.object(governor, "_read_cache_at", side_effect=OSError("fixture read failure")):
                with self.assertRaisesRegex(governor.GovernanceError, "JEV_GOVERNANCE_CACHE_READ_FAILED"):
                    governor.decide(
                        request(),
                        lambda payload: self.fail("cache read failure reached evaluator"),
                        cache_path=cache,
                    )

    def test_pre_call_persistence_failure_blocks_without_evaluation(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-precall-write-error-") as temp:
            cache = Path(temp) / "cache.json"
            with mock.patch.object(governor, "_write_cache_at", side_effect=OSError("fixture write failure")):
                with self.assertRaisesRegex(
                    governor.GovernanceError, "JEV_GOVERNANCE_CACHE_WRITE_FAILED"
                ):
                    governor.decide(
                        request(),
                        lambda payload: self.fail("pre-call write failure reached evaluator"),
                        cache_path=cache,
                    )

    def test_preflight_failure_before_tombstone_can_recover_without_poison(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-prepare-failure-") as temp:
            cache = Path(temp) / "cache.json"
            evaluations = 0

            def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
                nonlocal evaluations
                evaluations += 1
                return response()

            def unavailable() -> None:
                raise governor.GovernanceError("JEV_GOVERNANCE_PREFLIGHT_FAILED")

            with self.assertRaisesRegex(
                governor.GovernanceError, "JEV_GOVERNANCE_PREFLIGHT_FAILED"
            ):
                governor.decide(
                    request(), evaluate, cache_path=cache, prepare=unavailable
                )

            recovered = governor.decide(
                request(), evaluate, cache_path=cache, prepare=lambda: None
            )

        self.assertEqual(1, evaluations)
        self.assertEqual("DECIDED", recovered["status"])
        self.assertEqual("LIVE_JEV", recovered["provenance"])

    def test_proven_pre_network_connector_failures_do_not_poison_fingerprint(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-process-start-") as temp:
            root = Path(temp)
            cache = root / "cache.json"
            evaluator = governor._connector_evaluator(
                "typesafe", root / ".env", "5", root / "unused"
            )
            with mock.patch.object(
                subprocess, "Popen", side_effect=OSError("fixture spawn")
            ), self.assertRaisesRegex(
                governor.GovernanceError, "JEV_GOVERNANCE_EVALUATION_NOT_STARTED"
            ):
                governor.decide(request(), evaluator, cache_path=cache)

            recovered = governor.decide(
                request(), lambda payload: response(), cache_path=cache
            )
            self.assertEqual("DECIDED", recovered["status"])
            self.assertEqual("LIVE_JEV", recovered["provenance"])

    def test_missing_state_is_rejected_before_cache_or_evaluation(self) -> None:
        governor = load_governor()
        invalid = request()
        del invalid["state"]
        with tempfile.TemporaryDirectory(prefix="sdd-jev-missing-state-") as temp:
            cache = Path(temp) / "cache.json"
            with self.assertRaisesRegex(
                governor.GovernanceError, "JEV_GOVERNANCE_INPUT_INVALID"
            ):
                governor.decide(
                    invalid,
                    lambda payload: self.fail("invalid request reached evaluator"),
                    cache_path=cache,
                )
            self.assertFalse(cache.exists())

    def test_question_ids_with_terminal_controls_are_rejected_before_output(self) -> None:
        governor = load_governor()
        for question_id in ("forged\nline", "osc\x1b]0;owned\x07", "delete\x7f"):
            with self.subTest(question_id=repr(question_id)):
                invalid = request()
                question = invalid["questions"].pop("risk")
                invalid["questions"][question_id] = question
                with self.assertRaisesRegex(
                    governor.GovernanceError, "JEV_GOVERNANCE_INPUT_INVALID"
                ):
                    governor.decide(
                        invalid,
                        lambda payload: self.fail("unsafe ID reached evaluator"),
                    )

    def test_invalid_remote_model_is_reviewed_before_progress_or_cache(self) -> None:
        governor = load_governor()
        for model in ("", "x" * 241, "csi\x9b31m", "line\u2028break", "bidi\u202eforged"):
            with self.subTest(model=repr(model)):
                remote = response()
                remote["result"]["model"] = model
                report = governor.decide(request(), lambda payload: remote)
                self.assertEqual("REVIEW", report["status"])
                self.assertEqual("JEV_GOVERNANCE_RESPONSE_INVALID", report["reason"])
                self.assertNotIn("model", report)

    @unittest.skipUnless(os.name == "posix", "descriptor-anchored cache requires POSIX")
    def test_cache_writer_evicts_oldest_entries_until_encoded_file_fits(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cache-size-") as temp:
            root = Path(temp)
            parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            cache = {
                "schema_version": 1,
                "entries": {
                    f"entry-{index}": {"blob": "x" * (900 * 1024)}
                    for index in range(6)
                },
            }
            try:
                governor._write_cache_at(parent, "cache.json", cache)
            finally:
                os.close(parent)
            encoded = (root / "cache.json").read_bytes()

        self.assertLessEqual(len(encoded), governor.MAX_INPUT_BYTES)
        self.assertNotIn("entry-0", cache["entries"])
        self.assertIn("entry-5", cache["entries"])

    @unittest.skipUnless(os.name == "posix", "descriptor-anchored cache requires POSIX")
    def test_cache_reload_preserves_age_order_for_eviction(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cache-order-") as temp:
            root = Path(temp)
            parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                initial = {
                    "schema_version": 1,
                    "entries": {
                        "z-oldest": {"value": 1},
                        "a-newer": {"value": 2},
                        "m-newest": {"value": 3},
                    },
                }
                with mock.patch.object(governor, "MAX_CACHE_ENTRIES", 3):
                    governor._write_cache_at(parent, "cache.json", initial)
                    reloaded = json.loads((root / "cache.json").read_text())
                    reloaded["entries"]["b-latest"] = {"value": 4}
                    governor._write_cache_at(parent, "cache.json", reloaded)
            finally:
                os.close(parent)

        self.assertEqual(
            ["a-newer", "m-newest", "b-latest"],
            list(reloaded["entries"]),
        )

    def test_cli_receipt_never_emits_controls_from_fallback_phase(self) -> None:
        governor = load_governor()
        value = request()
        unsafe_phase = "PLAN\x1b]0;owned\x07\nFORGED"
        value["state"]["stage"] = {"current": unsafe_phase}
        with tempfile.TemporaryDirectory(prefix="sdd-jev-safe-receipt-") as temp:
            root = Path(temp)
            input_path = root / "request.json"
            setup_path = root / "PROJECT_SETUP.md"
            input_path.write_text(json.dumps(value), encoding="utf-8")
            setup_path.write_text(
                project_setup('{"install":true,"automatic_semantic_governance":true}'),
                encoding="utf-8",
            )
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(governor, "_connector_prepare"),
                mock.patch.object(
                    governor,
                    "_connector_evaluator",
                    return_value=lambda payload: response(),
                ),
                mock.patch.object(sys, "argv", [
                    str(SCRIPT), "decide", "--input", str(input_path),
                    "--project-setup", str(setup_path), "--cache", str(root / "cache.json"),
                    "--progress", str(root / "missing-progress.json"), "--json",
                ]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                returncode = governor.main()

        self.assertEqual(0, returncode)
        self.assertEqual("DECIDED", json.loads(stdout.getvalue())["status"])
        self.assertNotIn(unsafe_phase, stderr.getvalue())
        self.assertNotIn("\x1b", stderr.getvalue())
        self.assertNotIn("FORGED", stderr.getvalue())
        self.assertIn("fase: não informada", stderr.getvalue())

    def test_connector_evaluator_streams_payload_without_a_request_path(self) -> None:
        governor = load_governor()
        payload = {"state": request()["state"], "questions": request()["questions"]}

        class Process:
            returncode = 0

            def communicate(self, *, input: bytes, timeout: float) -> tuple[bytes, bytes]:
                self.input = input
                self.timeout = timeout
                return json.dumps(response()).encode(), b""

        process = Process()
        with mock.patch.object(subprocess, "Popen", return_value=process) as opened:
            result = governor._connector_evaluator(
                "typesafe", Path("fixture.env"), "5", Path("unused")
            )(payload)

        command = opened.call_args.args[0]
        self.assertIn("--input-stdin", command)
        self.assertNotIn("--input", command)
        self.assertEqual(governor._canonical(payload), process.input)
        self.assertEqual(payload, json.loads(process.input))
        self.assertEqual("OK", result["status"])

    def test_post_start_communication_failure_keeps_retry_suppression(self) -> None:
        governor = load_governor()

        class Process:
            returncode = 1

            def communicate(self, *, input: bytes, timeout: float) -> tuple[bytes, bytes]:
                raise OSError("fixture communicate failure after start")

            def kill(self) -> None:
                pass

            def wait(self) -> None:
                pass

        with tempfile.TemporaryDirectory(prefix="sdd-jev-post-start-") as temp:
            root = Path(temp)
            cache = root / "cache.json"
            with mock.patch.object(subprocess, "Popen", return_value=Process()):
                first = governor.decide(
                    request(),
                    governor._connector_evaluator(
                        "typesafe", root / ".env", "5", root / "unused"
                    ),
                    cache_path=cache,
                )
            evaluations = 0

            def should_not_run(payload: dict[str, Any]) -> dict[str, Any]:
                nonlocal evaluations
                evaluations += 1
                return response()

            second = governor.decide(request(), should_not_run, cache_path=cache)

        self.assertEqual("REVIEW", first["status"])
        self.assertEqual("CACHE", second["provenance"])
        self.assertEqual(0, evaluations)

    def test_final_persistence_failure_returns_review_receipt_and_retry_uses_tombstone(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-final-write-error-") as temp:
            cache = Path(temp) / "cache.json"
            original_write = governor._write_cache_at
            writes = 0

            def fail_final_write(
                parent: int, name: str, value: dict[str, Any], **kwargs: Any
            ) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("fixture final write failure")
                original_write(parent, name, value, **kwargs)

            with mock.patch.object(governor, "_write_cache_at", side_effect=fail_final_write):
                report = governor.decide(request(), lambda payload: response(), cache_path=cache)
            retry = governor.decide(
                request(),
                lambda payload: self.fail("durable pre-call tombstone was ignored"),
                cache_path=cache,
            )

        self.assertEqual("REVIEW", report["status"])
        self.assertEqual("JEV_GOVERNANCE_FINAL_PERSISTENCE_FAILED", report["reason"])
        self.assertEqual(
            {
                "network_call": "COMPLETED",
                "result_status": "DECIDED",
                "reason": "JEV_GOVERNANCE_CACHE_WRITE_FAILED",
            },
            report["receipt"],
        )
        self.assertEqual("CACHE", retry["provenance"])
        self.assertEqual("JEV_GOVERNANCE_IN_FLIGHT_UNCERTAIN", retry["reason"])

    def test_post_replace_directory_sync_failure_keeps_visible_final_result_consistent(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-post-replace-fsync-") as temp:
            cache = Path(temp) / "cache.json"
            original_fsync = governor.os.fsync
            calls = 0

            def fail_final_directory_sync(descriptor: int) -> None:
                nonlocal calls
                calls += 1
                if calls == 4:
                    raise OSError("fixture post-replace directory fsync failure")
                original_fsync(descriptor)

            with mock.patch.object(governor.os, "fsync", side_effect=fail_final_directory_sync):
                decided = governor.decide(
                    request(), lambda payload: response(), cache_path=cache
                )
            retried = governor.decide(
                request(),
                lambda payload: self.fail("visible final cache retried evaluator"),
                cache_path=cache,
            )

        self.assertEqual("DECIDED", decided["status"])
        self.assertEqual("CACHE", retried["provenance"])
        self.assertEqual("DECIDED", retried["status"])

    def test_cli_announces_paid_use_when_cache_persistence_fails(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cli-write-error-") as temp:
            root = Path(temp)
            input_path = root / "request.json"
            setup_path = root / "PROJECT_SETUP.md"
            input_path.write_text(json.dumps(request()), encoding="utf-8")
            setup_path.write_text(
                project_setup('{"install":true,"automatic_semantic_governance":true}'),
                encoding="utf-8",
            )
            original_write = governor._write_cache_at
            writes = 0

            def fail_final_write(
                parent: int, name: str, value: dict[str, Any], **kwargs: Any
            ) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("fixture final write failure")
                original_write(parent, name, value, **kwargs)

            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(governor, "_connector_prepare"),
                mock.patch.object(governor, "_connector_evaluator", return_value=lambda payload: response()),
                mock.patch.object(governor, "_write_cache_at", side_effect=fail_final_write),
                mock.patch.object(sys, "argv", [
                    str(SCRIPT), "decide", "--input", str(input_path),
                    "--project-setup", str(setup_path), "--cache", str(root / "cache.json"),
                    "--progress", str(root / "missing-progress.json"), "--json",
                ]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                returncode = governor.main()

        report = json.loads(stdout.getvalue())
        self.assertEqual(2, returncode)
        self.assertEqual("REVIEW", report["status"])
        self.assertEqual("JEV_GOVERNANCE_CACHE_WRITE_FAILED", report["receipt"]["reason"])
        self.assertIn("=== JEV USADO ===", stderr.getvalue())

    def test_cli_rejects_non_numeric_timeout_with_stable_blocked_json(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-timeout-") as temp:
            input_path = Path(temp) / "request.json"
            setup_path = Path(temp) / "PROJECT_SETUP.md"
            input_path.write_text(json.dumps(request()), encoding="utf-8")
            setup_path.write_text(
                project_setup('{"install":true,"automatic_semantic_governance":true}'),
                encoding="utf-8",
            )
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(sys, "argv", [
                    str(SCRIPT), "decide", "--input", str(input_path),
                    "--project-setup", str(setup_path), "--timeout", "not-a-number", "--json",
                ]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                returncode = governor.main()

        self.assertEqual(2, returncode)
        self.assertEqual(
            {"status": "BLOCKED", "reason": "JEV_GOVERNANCE_TIMEOUT_INVALID"},
            json.loads(stdout.getvalue()),
        )
        self.assertEqual("", stderr.getvalue())

    def test_cli_announces_every_live_use_and_keeps_json_on_stdout(self) -> None:
        governor = load_governor()
        progress_spec = importlib.util.spec_from_file_location(
            "sdd_terminal_progress_for_governor", RUNTIME / "terminal_progress.py"
        )
        assert progress_spec is not None and progress_spec.loader is not None
        progress_module = importlib.util.module_from_spec(progress_spec)
        sys.modules[progress_spec.name] = progress_module
        progress_spec.loader.exec_module(progress_module)
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cli-") as temp:
            root = Path(temp)
            input_path = root / "request.json"
            setup_path = root / "PROJECT_SETUP.md"
            cache_path = root / "cache.json"
            progress_path = root / "progress.json"
            input_path.write_text(json.dumps(request()), encoding="utf-8")
            setup_path.write_text(
                project_setup('{"install":true,"automatic_semantic_governance":true}'),
                encoding="utf-8",
            )
            progress_module.save_progress(
                progress_path,
                progress_module.new_progress("openai-codex", "PLAN"),
            )
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(governor, "_connector_prepare"),
                mock.patch.object(
                    governor,
                    "_connector_evaluator",
                    return_value=lambda payload: response(),
                ),
                mock.patch.object(sys, "argv", [
                    str(SCRIPT), "decide", "--input", str(input_path),
                    "--project-setup", str(setup_path), "--cache", str(cache_path),
                    "--progress", str(progress_path), "--json",
                ]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                returncode = governor.main()
            saved_progress = progress_module.load_progress(progress_path)

        self.assertEqual(0, returncode)
        self.assertEqual("DECIDED", json.loads(stdout.getvalue())["status"])
        announcement = stderr.getvalue()
        self.assertIn("=== JEV USADO ===", announcement)
        self.assertIn("script: runtime/semantic_governor.py decide", announcement)
        self.assertIn("provider: typesafe", announcement)
        self.assertIn("fase: PLAN", announcement)
        self.assertIn("atuação: classificação semântica", announcement)
        self.assertIn("decisões: risk, needs_security_review", announcement)
        self.assertIn("tempo:", announcement)
        self.assertIn("arquivos avaliados: 0", announcement)
        self.assertIn("decididos vs revisar: 2 vs 0", announcement)
        self.assertIn("artefato: fingerprint ", announcement)
        self.assertFalse(saved_progress["jev"]["active"])
        self.assertEqual("typesafe", saved_progress["jev"]["provider"])
        self.assertEqual("DECIDED", saved_progress["jev"]["status"])
        self.assertEqual(["risk", "needs_security_review"], saved_progress["jev"]["questions"])

    def test_cli_cache_hit_makes_no_call_and_prints_no_live_use_announcement(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cli-cache-") as temp:
            root = Path(temp)
            input_path = root / "request.json"
            setup_path = root / "PROJECT_SETUP.md"
            cache_path = root / "cache.json"
            input_path.write_text(json.dumps(request()), encoding="utf-8")
            setup_path.write_text(
                project_setup('{"install":true,"automatic_semantic_governance":true}'),
                encoding="utf-8",
            )
            governor.decide(request(), lambda payload: response(), cache_path=cache_path)
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(
                    governor,
                    "_connector_evaluator",
                    side_effect=AssertionError("cache hit created an evaluator"),
                ),
                mock.patch.object(sys, "argv", [
                    str(SCRIPT), "decide", "--input", str(input_path),
                    "--project-setup", str(setup_path), "--cache", str(cache_path), "--json",
                ]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                returncode = governor.main()

        self.assertEqual(0, returncode)
        self.assertEqual("CACHE", json.loads(stdout.getvalue())["provenance"])
        self.assertEqual("", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
