"""Automatic, cached Jev governance for semantic SDD decisions."""
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import os
import stat
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

    def test_automatic_governance_fails_closed_on_non_posix_platforms(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-unsupported-platform-") as temp:
            with self.assertRaisesRegex(
                governor.GovernanceError,
                "JEV_GOVERNANCE_PLATFORM_UNSUPPORTED",
            ):
                governor.decide(
                    request(),
                    lambda payload: self.fail("unsupported platform reached evaluator"),
                    cache_path=Path(temp) / "cache.json",
                    platform="nt",
                )

    def test_host_without_fchmod_can_write_cache_and_connector_request(self) -> None:
        governor = load_governor()
        completed = mock.Mock(stdout=json.dumps(response()), stderr="")
        with tempfile.TemporaryDirectory(prefix="sdd-jev-no-fchmod-") as temp:
            root = Path(temp)
            cache = root / "cache.json"
            evaluator = governor._connector_evaluator(
                "typesafe",
                root / ".env",
                "30",
                root,
            )
            with (
                mock.patch.object(governor.os, "fchmod", None),
                mock.patch.object(governor.subprocess, "run", return_value=completed),
            ):
                first = governor.decide(request(), lambda payload: response(), cache_path=cache)
                second = governor.decide(
                    request(),
                    lambda payload: self.fail("cache written without fchmod was not reused"),
                    cache_path=cache,
                )
                connected = evaluator({"state": {}, "questions": request()["questions"]})

        self.assertEqual("LIVE_JEV", first["provenance"])
        self.assertEqual("CACHE", second["provenance"])
        self.assertEqual("OK", connected["status"])


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

    def test_terminal_receipt_fields_reject_control_characters(self) -> None:
        governor = load_governor()
        invalid = request()
        invalid["questions"]["risk\nINJECTED"] = invalid["questions"].pop("risk")
        with self.assertRaisesRegex(governor.GovernanceError, "JEV_GOVERNANCE_INPUT_INVALID"):
            governor.decide(invalid, lambda payload: self.fail("unsafe ID reached evaluator"))

        self.assertEqual(
            "não informada",
            governor._phase_from_state({"stage": {"current": "PLAN\u001b[2J"}}),
        )

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
            with mock.patch.object(governor, "_read_cache", side_effect=OSError("fixture read failure")):
                with self.assertRaisesRegex(governor.GovernanceError, "JEV_GOVERNANCE_CACHE_READ_FAILED"):
                    governor.decide(
                        request(),
                        lambda payload: self.fail("cache read failure reached evaluator"),
                        cache_path=cache,
                    )

    def test_paid_result_survives_cache_write_error_with_structured_warning(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cache-write-error-") as temp:
            cache = Path(temp) / "cache.json"
            with mock.patch.object(governor, "_write_cache", side_effect=OSError("fixture write failure")):
                report = governor.decide(request(), lambda payload: response(), cache_path=cache)

        self.assertEqual("DECIDED", report["status"])
        self.assertEqual("LIVE_JEV", report["provenance"])
        self.assertEqual(
            {"status": "ERROR", "reason": "JEV_GOVERNANCE_CACHE_WRITE_FAILED"},
            report["cache"],
        )

    def test_cli_announces_paid_use_when_cache_persistence_fails(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-cli-write-error-") as temp:
            root = Path(temp)
            input_path = root / "request.json"
            input_path.write_text(json.dumps(request()), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(governor, "_connector_evaluator", return_value=lambda payload: response()),
                mock.patch.object(governor, "_write_cache", side_effect=OSError("fixture write failure")),
                mock.patch.object(sys, "argv", [
                    str(SCRIPT), "decide", "--input", str(input_path),
                    "--cache", str(root / "cache.json"),
                    "--progress", str(root / "missing-progress.json"), "--json",
                ]),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                returncode = governor.main()

        report = json.loads(stdout.getvalue())
        self.assertEqual(0, returncode)
        self.assertEqual("DECIDED", report["status"])
        self.assertEqual("JEV_GOVERNANCE_CACHE_WRITE_FAILED", report["cache"]["reason"])
        self.assertIn("=== JEV USADO ===", stderr.getvalue())

    def test_cli_rejects_non_numeric_timeout_with_stable_blocked_json(self) -> None:
        governor = load_governor()
        with tempfile.TemporaryDirectory(prefix="sdd-jev-timeout-") as temp:
            input_path = Path(temp) / "request.json"
            input_path.write_text(json.dumps(request()), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(sys, "argv", [
                    str(SCRIPT), "decide", "--input", str(input_path),
                    "--timeout", "not-a-number", "--json",
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
            cache_path = root / "cache.json"
            progress_path = root / "progress.json"
            input_path.write_text(json.dumps(request()), encoding="utf-8")
            progress_module.save_progress(
                progress_path,
                progress_module.new_progress("openai-codex", "PLAN"),
            )
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(
                    governor,
                    "_connector_evaluator",
                    return_value=lambda payload: response(),
                ),
                mock.patch.object(sys, "argv", [
                    str(SCRIPT), "decide", "--input", str(input_path),
                    "--cache", str(cache_path), "--progress", str(progress_path), "--json",
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
            cache_path = root / "cache.json"
            input_path.write_text(json.dumps(request()), encoding="utf-8")
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
                    "--cache", str(cache_path), "--json",
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
