"""Regression tests for PR #52 review findings: git hardening, heuristic PASS,
ZIP total budget, private output permissions and synthetic examples."""
from __future__ import annotations

import io
import dataclasses
import json
import os
import shutil
import stat
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from helpers import F, SKILL_DIR, TempDirCase, no_tools
import common
import fixtures as fx
import guardian
import inspect_project
import validate_signing

GIT = shutil.which("git")
HARDENING = ("core.fsmonitor=false", "core.hooksPath=/dev/null", "core.untrackedCache=false", "protocol.allow=never")


# ---------------------------------------------------------------- 1. git hardening
class TestGitHardening(TempDirCase):
    def test_git_argv_has_hardening_flags_and_double_dash(self):
        argv = common.git_cmd(Path("/repo"), "ls-files", "--error-unmatch", pathspecs=["android/key.properties"])
        self.assertEqual(argv[0], "git")
        for opt in HARDENING:
            i = argv.index(opt)
            self.assertEqual(argv[i - 1], "-c")
        self.assertIn("--", argv)
        self.assertEqual(argv[argv.index("--") + 1:], ["android/key.properties"])
        # hardening must come before the subcommand
        self.assertLess(argv.index("core.fsmonitor=false"), argv.index("ls-files"))

    def test_git_env_is_minimal(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": "/evil", "AWS_SECRET_ACCESS_KEY": "x", "GIT_CONFIG_PARAMETERS": "'core.fsmonitor'='x'"}):
            env = common.git_env()
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")
        self.assertIn("PATH", env)
        for leaked in ("GIT_DIR", "AWS_SECRET_ACCESS_KEY", "GIT_CONFIG_PARAMETERS"):
            self.assertNotIn(leaked, env)

    def test_every_git_call_goes_through_run_git(self):
        calls = []

        def fake(cmd, timeout=120, env=None):
            calls.append((cmd, env))
            return common.ToolResult(True, 0, "/x\n", "")
        root = self.tmp / "p"
        (root / "android").mkdir(parents=True)
        kp = root / "android/key.properties"
        kp.write_text("x")
        with mock.patch("common.run_tool", side_effect=fake), \
             mock.patch("subprocess.run", side_effect=AssertionError("raw subprocess.run used")):
            inspect_project.git_status_for([kp], root)
        self.assertEqual(len(calls), 3)
        for cmd, env in calls:
            self.assertIn("core.fsmonitor=false", cmd)
            self.assertIsNotNone(env)
            self.assertEqual(env.get("GIT_CONFIG_NOSYSTEM"), "1")
        self.assertTrue(all("--" in c for c, _ in calls[1:]))

    def test_no_raw_git_subprocess_in_scripts(self):
        for py in (SKILL_DIR / "scripts").glob("*.py"):
            text = py.read_text(encoding="utf-8")
            self.assertFalse('subprocess.run(["git"' in text, py.name)

    def test_exclude_and_attribute_files_are_neutralised(self):
        argv = common.git_cmd(Path("/repo"), "check-ignore", "-q", pathspecs=["x"])
        for opt in ("core.excludesFile=/dev/null", "core.attributesFile=/dev/null"):
            self.assertEqual(argv[argv.index(opt) - 1], "-c")

    def test_a_hanging_git_stops_after_the_first_timeout_and_is_reported_as_unknown(self):
        root = self.tmp / "p"
        (root / "android").mkdir(parents=True)
        paths = []
        for i in range(5):
            p = root / "android" / f"m{i}" / "key.properties"
            p.parent.mkdir()
            p.write_text("x")
            paths.append(p)
        calls = []

        def fake(cmd, timeout=120, env=None):
            calls.append(cmd)
            if "rev-parse" in cmd:
                return common.ToolResult(True, 0, str(root) + "\n", "")
            return common.ToolResult(False, None, error="timeout")

        with mock.patch("common.run_tool", side_effect=fake):
            st = inspect_project.git_status_for(paths, root)
        self.assertEqual(2, len(calls), "rev-parse + the first command that timed out; nothing after it")
        self.assertTrue(all(v == {"unknown": True} for v in st.values()))
        self.assertEqual(len(paths), len(st))
        fs = validate_signing.project_signing_hygiene([str(p.relative_to(root)) for p in paths], st, F)
        self.assertTrue(all(f.severity == common.NOT_VERIFIED for f in fs))
        self.assertTrue(all("indeterminado" in f.evidence for f in fs))
        self.assertFalse(any("fora de um repositório" in f.evidence for f in fs))

    def test_sensitive_path_count_is_capped(self):
        root = self.tmp / "p"
        root.mkdir()
        paths = [root / f"k{i}.jks" for i in range(inspect_project.MAX_GIT_PATHS + 7)]
        for p in paths:
            p.write_text("x")

        def fake(cmd, timeout=120, env=None):
            return common.ToolResult(True, 0, str(root) + "\n", "") if "rev-parse" in cmd else common.ToolResult(True, 0)

        with mock.patch("common.run_tool", side_effect=fake):
            st = inspect_project.git_status_for(paths, root)
        self.assertEqual(inspect_project.MAX_GIT_PATHS, sum(1 for v in st.values() if v.get("in_repo")))
        self.assertEqual(7, sum(1 for v in st.values() if v == {"unknown": True}))

    @unittest.skipUnless(GIT, "git not installed")
    def test_malicious_fsmonitor_is_not_executed(self):
        repo = self.tmp / "repo"
        (repo / "android/app").mkdir(parents=True)
        marker = self.tmp / "PWNED"
        hook = self.tmp / "evil.sh"
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        hook.chmod(0o700)
        env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(self.tmp), "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": os.devnull}
        g = lambda *a: subprocess.run([GIT, "-C", str(repo), *a], env=env, check=True, capture_output=True)  # noqa: E731
        g("init", "-q")
        (repo / "android/key.properties").write_text("storePassword=x\n")
        (repo / "android/app/upload.jks").write_bytes(b"\xfe\xed")
        (repo / ".gitignore").write_text("android/app/upload.jks\n")
        g("add", "android/key.properties", ".gitignore")
        g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        g("config", "core.fsmonitor", str(hook))
        g("config", "core.hooksPath", str(self.tmp))
        st = inspect_project.git_status_for([repo / "android/key.properties", repo / "android/app/upload.jks"], repo)
        self.assertFalse(marker.exists(), "core.fsmonitor command was executed")
        # hardening must not break detection
        self.assertEqual(st["android/key.properties"], {"in_repo": True, "tracked": True, "ignored": False})
        self.assertEqual(st["android/app/upload.jks"], {"in_repo": True, "tracked": False, "ignored": True})


# ---------------------------------------------------------------- 2. heuristic PASS
class TestNoHeuristicPass(TempDirCase):
    def test_factory_downgrades_heuristic_and_manual_pass(self):
        for method in ("heuristic", "manual"):
            with self.subTest(method=method):
                f = F("TECH-AGP-16KB", common.PASS, "page_size_16kb", "AGP ok", method=method, confidence="high")
                self.assertEqual(f.severity, common.INFO)
                self.assertIn("confirm", f.title.lower())

    def test_heuristic_pass_on_mandatory_rule_stays_pending(self):
        f = F("TECH-TARGET-API-LEVEL", common.PASS, "target_sdk", "Target ok", method="heuristic", confidence="medium")
        self.assertEqual(f.severity, common.NOT_VERIFIED)
        self.assertEqual(common.final_status([f, F("TECH-DEBUGGABLE", common.PASS, "android_build", "ok")]), common.NEEDS_REVIEW)

    def test_heuristic_and_manual_not_applicable_never_hide_a_rule(self):
        cases = (("POL-PLAY-BILLING", "billing", "heuristic"), ("PRIV-ACCOUNT-DELETION", "data_safety", "manual"),
                 ("POL-CLOSED-TESTING", "testing", "manual"))
        for rule, section, method in cases:
            with self.subTest(rule=rule, method=method):
                f = F(rule, common.NOT_APPLICABLE, section, "n/a", method=method, confidence="medium")
                self.assertEqual(f.severity, common.NOT_VERIFIED)
                self.assertNotEqual(common.final_status([f, F("TECH-DEBUGGABLE", common.PASS, "android_build", "ok")]),
                                    common.READY)
        # Deterministic N/A (e.g. no native libraries in the artifact) is still N/A.
        det = F("TECH-16KB-PAGE-SIZE", common.NOT_APPLICABLE, "page_size_16kb", "no .so")
        self.assertEqual(det.severity, common.NOT_APPLICABLE)
        # Every construction path goes through the guard (cache reload included).
        self.assertEqual(dataclasses.replace(det, verification_method="heuristic").severity, common.NOT_VERIFIED)

    def test_heuristic_blocker_or_critical_is_capped_at_warning(self):
        for sev_in in (common.BLOCKER, common.CRITICAL):
            for method, confidence in (("heuristic", "high"), ("heuristic", "medium"), ("manual", "high"),
                                       ("deterministic", "low")):
                with self.subTest(sev=sev_in, method=method, confidence=confidence):
                    f = F("POL-PLAY-BILLING", sev_in, "billing", "x", method=method, confidence=confidence)
                    self.assertEqual(f.severity, common.WARNING)
                    direct = common.Finding(rule_id="POL-PLAY-BILLING", severity=sev_in, section="billing", title="x",
                                            component="-", evidence="-", verification_method=method,
                                            confidence=confidence, recommendation="-")
                    self.assertEqual(direct.severity, common.WARNING)
        self.assertEqual(F("TECH-DEBUGGABLE", common.BLOCKER, "android_build", "x").severity, common.BLOCKER)

    def test_finding_direct_construction_also_guarded(self):
        f = common.Finding(rule_id="X", severity=common.PASS, section="android_build", title="t", component="-",
                           evidence="-", verification_method="heuristic", confidence="high", recommendation="-")
        self.assertEqual(f.severity, common.INFO)

    def test_deterministic_pass_kept(self):
        f = F("TECH-DEBUGGABLE", common.PASS, "android_build", "ok", method="deterministic")
        self.assertEqual(f.severity, common.PASS)

    def test_ready_never_from_heuristic_only(self):
        fs = [F("TECH-AGP-16KB", common.PASS, "page_size_16kb", "AGP", method="heuristic")]
        self.assertNotEqual(common.final_status(fs), common.READY)
        self.assertNotEqual(common.section_status(fs), common.PASS)

    def test_end_to_end_report_has_no_non_deterministic_pass(self):
        root = fx.make_flutter_project(self.tmp / "proj", key_properties=False)
        (root / "android/app/build.gradle").write_text(
            (root / "android/app/build.gradle").read_text().replace("compileSdkVersion 36", "compileSdkVersion 36\n    ndkVersion '28.0.13004108'"))
        aab = root / "build/app/outputs/bundle/release/app-release.aab"
        aab.parent.mkdir(parents=True)
        fx.make_aab(aab)
        out = self.tmp / "out"
        with mock.patch("guardian.discover_tools", side_effect=no_tools), \
             mock.patch("sys.stdout", new=io.StringIO()):
            guardian.main(["gate", "--release", str(root), "--output-dir", str(out), "--no-cache", "--json"])
        r = json.loads((out / "google-play-audit-report.json").read_text(encoding="utf-8"))
        agp = [f for f in r["findings"] if f["rule_id"] == "TECH-AGP-16KB"]
        self.assertTrue(agp, "fixture must exercise the AGP/NDK heuristic")
        bad = [(f["rule_id"], f["verification_method"]) for f in r["findings"]
               if f["severity"] == "PASS" and f["verification_method"] != "deterministic"]
        self.assertEqual(bad, [])


# ---------------------------------------------------------------- 3. ZIP total budget
class TestZipBudget(TempDirCase):
    def _many_small(self, n=30, size=100 * 1024) -> Path:
        p = self.tmp / "many.zip"
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            for i in range(n):
                z.writestr(f"f{i}.bin", b"\0" * size)
        return p

    def test_aggregate_ratio_flags_many_entries_below_per_entry_threshold(self):
        p = self._many_small()
        scan = common.scan_zip(p, ratio_min_size=1024 * 1024)  # each entry (100 KiB) below the per-entry threshold
        self.assertIn("zip_bomb", {i.kind for i in scan.issues})
        self.assertFalse(scan.crc_checked)

    def test_streamed_bytes_budget_enforced(self):
        p = self._many_small(n=5, size=100 * 1024)
        scan = common.scan_zip(p, max_total=10 ** 12, max_ratio=10 ** 9, max_streamed=250 * 1024)
        self.assertIn("zip_bomb", {i.kind for i in scan.issues})

    def test_read_member_shared_budget_across_reads(self):
        p = self._many_small(n=3, size=100 * 1024)
        budget = common.ZipBudget(150 * 1024)
        with zipfile.ZipFile(p) as zf:
            infos = zf.infolist()
            self.assertEqual(len(common.read_member(zf, infos[0], budget=budget)), 100 * 1024)
            with self.assertRaises(common.ZipBudgetExceeded):
                common.read_member(zf, infos[1], budget=budget)

    def test_extract_member_safe_consumes_shared_budget(self):
        p = self._many_small(n=2, size=100 * 1024)
        budget = common.ZipBudget(150 * 1024)
        out = self.tmp / "x"
        out.mkdir()
        with zipfile.ZipFile(p) as zf:
            a, b = zf.infolist()
            common.extract_member_safe(zf, a, out, 0, budget=budget)
            with self.assertRaises(common.ZipBudgetExceeded):
                common.extract_member_safe(zf, b, out, 1, budget=budget)

    def test_native_library_scan_uses_archive_budget(self):
        import validate_native_libraries as vnl
        apk = fx.make_apk(self.tmp / "a.apk", libs={"arm64-v8a/libx.so": fx.make_elf(), "arm64-v8a/liby.so": fx.make_elf()})
        with mock.patch.object(common, "MAX_STREAMED", 10):
            res = vnl.validate_native_libraries(apk, "apk", no_tools(), F, 36, use_tools=False)
        self.assertIn("SEC-ARCHIVE-SAFETY", {f.rule_id for f in res["findings"]})

    def test_signature_reader_uses_archive_budget(self):
        import validate_signing as vs
        apk = fx.make_apk(self.tmp / "s.apk")
        with mock.patch.object(common, "MAX_STREAMED", 10):
            out = vs.read_signatures(apk)
        self.assertTrue(any("orçamento" in e for e in out["errors"]), out["errors"])

    def test_normal_archive_still_ok(self):
        p = fx.make_aab(self.tmp / "a.aab")
        scan = common.scan_zip(p)
        self.assertTrue(scan.ok)
        self.assertTrue(scan.crc_checked)
        self.assertNotIn("zip_bomb", {i.kind for i in scan.issues})


# ---------------------------------------------------------------- 4. private output
@unittest.skipIf(os.name == "nt", "POSIX permissions only")
class TestPrivateOutput(TempDirCase):
    def _mode(self, p: Path) -> int:
        return stat.S_IMODE(p.stat().st_mode)

    def test_default_output_dir_is_private_and_fresh(self):
        a = guardian.default_output_dir("x")
        b = guardian.default_output_dir("x")
        try:
            self.assertNotEqual(a, b)
            self.assertEqual(self._mode(a), 0o700)
        finally:
            shutil.rmtree(a, ignore_errors=True)
            shutil.rmtree(b, ignore_errors=True)

    def test_created_output_dir_and_files_are_private(self):
        root = fx.make_flutter_project(self.tmp / "proj", key_properties=False)
        out = self.tmp / "new" / "out"
        old = os.umask(0o022)
        try:
            with mock.patch("guardian.discover_tools", side_effect=no_tools), mock.patch("sys.stdout", new=io.StringIO()):
                guardian.main(["audit", "--full", str(root), "--output-dir", str(out), "--json"])
        finally:
            os.umask(old)
        self.assertEqual(self._mode(out), 0o700)
        for name in ("google-play-audit-report.json", "google-play-audit-report.md"):
            self.assertEqual(self._mode(out / name), 0o600, name)
        cache = out / ".guardian-cache"
        self.assertEqual(self._mode(cache), 0o700)
        files = [p for p in cache.rglob("*") if p.is_file()]
        self.assertTrue(files)
        for p in files:
            self.assertEqual(self._mode(p), 0o600, p.name)
        for d in [p for p in cache.rglob("*") if p.is_dir()]:
            self.assertEqual(self._mode(d), 0o700, d.name)


# ---------------------------------------------------------------- 5. no private data in the skill
class TestNoPrivateData(unittest.TestCase):
    def test_skill_has_no_personal_data(self):
        needles = ("/Users/", "mently", "Imperatriz", "Mently")
        hits = []
        for p in SKILL_DIR.rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts or p.name == Path(__file__).name:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            hits += [f"{p.relative_to(SKILL_DIR)}: {n}" for n in needles if n in text]
        self.assertEqual(hits, [])

    def test_example_report_is_synthetic_and_valid(self):
        import jsonschema
        r = json.loads((SKILL_DIR / "examples/google-play-audit-report.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(json.loads((SKILL_DIR / "schemas/audit-report.schema.json").read_text())).validate(r)
        self.assertEqual(r["app"]["package"], "com.example.app")
        bad = [f["rule_id"] for f in r["findings"] if f["severity"] == "PASS" and f["verification_method"] != "deterministic"]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
