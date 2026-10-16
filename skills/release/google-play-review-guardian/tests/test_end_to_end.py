"""End-to-end: disposable Flutter project, report generation + schema, CLI exit codes,
offline mode, no-SDK mode, secret redaction, read-only guarantee, cache, skill metadata."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import urllib.error
from pathlib import Path
from unittest import mock

import jsonschema

from helpers import F, RULES, SKILL_DIR, TempDirCase, load_schema, no_tools, sev
import common
import fixtures as fx
import guardian
import inspect_project

SECRETS = ("SuperSecretValue123", "AnotherSecret456", "SECRETKEYSTORE")


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
            h.update(str(p.stat().st_mtime_ns).encode())
    return h.hexdigest()


class CLIBase(TempDirCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
             mock.patch("guardian.discover_tools", side_effect=no_tools):
            try:
                code = guardian.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def report(self, out_dir: Path) -> dict:
        r = json.loads((out_dir / "google-play-audit-report.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(load_schema()).validate(r)
        return r


class TestFlutterProject(CLIBase):
    def test_gradle_groovy_and_kts(self):
        for kts in (False, True):
            with self.subTest(kts=kts):
                root = fx.make_flutter_project(self.tmp / f"p{kts}", kts=kts, target_sdk="35", debuggable=True, minify=False)
                g = root / ("android/app/build.gradle.kts" if kts else "android/app/build.gradle")
                parsed = inspect_project.parse_gradle(g)
                self.assertEqual((parsed["target_sdk"], parsed["min_sdk"]), (35, 24))
                self.assertTrue(parsed["release_debuggable"])
                self.assertFalse(parsed["release_minify"])
                self.assertEqual(parsed["application_id"], "com.example.app")

    def test_pubspec(self):
        root = fx.make_flutter_project(self.tmp / "p")
        pub = inspect_project.parse_pubspec(root)
        self.assertEqual(pub["version"], "2.1.0+45")
        self.assertIn("firebase_analytics", pub["dependencies"])
        self.assertEqual(pub["locked"]["in_app_purchase"]["version"], "3.2.0")
        self.assertEqual(pub["lock_sdks"]["flutter"], ">=3.24.0")

    def test_full_audit_project_only(self):
        root = fx.make_flutter_project(self.tmp / "proj", target_sdk="35", debuggable=True)
        before = tree_digest(root)
        out = self.tmp / "out"
        code, stdout, _ = self.run_cli("audit", "--full", str(root), "--output-dir", str(out), "--as-of", "2026-10-16")
        self.assertEqual(code, 2)
        r = self.report(out)
        fs = r["findings"]
        rules = {f["rule_id"]: set() for f in fs}
        for f in fs:
            rules[f["rule_id"]].add(f["severity"])
        self.assertIn("BLOCKER", rules["TECH-DEBUGGABLE"])            # debuggable true in release
        self.assertIn("BLOCKER", rules["TECH-TARGET-API-LEVEL"])      # literal 35 < 36 (deterministic)
        self.assertIn("WARNING", rules["POL-BACKGROUND-LOCATION"])    # presence -> warning, never blocker
        self.assertIn("WARNING", rules["SEC-CLEARTEXT"])
        self.assertIn("NOT_VERIFIED", rules["TECH-MERGED-MANIFEST"])
        self.assertIn("WARNING", rules["PRIV-ACCOUNT-DELETION"])      # signUp found, no deletion
        self.assertIn("NOT_VERIFIED", rules["POL-CLOSED-TESTING"])
        # secrets: key.properties present, never printed
        blob = json.dumps(r) + (out / "google-play-audit-report.md").read_text() + stdout
        for s in SECRETS:
            self.assertNotIn(s, blob)
        self.assertIn("android/key.properties", blob)
        self.assertEqual(before, tree_digest(root))  # read-only
        self.assertEqual(r["exit_code"], 2)

    def test_gate_with_artifacts_ready_path(self):
        root = fx.make_flutter_project(self.tmp / "proj", key_properties=False)
        perms = ["android.permission.INTERNET"]
        aab = fx.make_aab(self.tmp / "app.aab", fx.manifest_tree(permissions=perms), libs={"arm64-v8a/libx.so": fx.make_elf()})
        decl = self.tmp / "decl.json"
        decl.write_text(json.dumps({
            "account": {"type": "organization"}, "account_creation": False,
            "privacy_policy_url": "https://example.com/privacy",
            "data_safety": {"data_types": ["App activity", "Device or other IDs", "Purchase history"]},
            "store_listing": {"title": "Example", "short_description": "Short", "full_description": "Long"},
            "manual_confirmations": {k: "verificado no Console em 2026-10-16" for k in (
                "TECH-BUNDLETOOL-VALIDATE", "TECH-16KB-ZIP-ALIGN", "TECH-PLAY-APP-SIGNING", "TECH-SIGNATURE-VERIFY",
                "TECH-VERSION-CODE-INCREMENT", "TECH-MERGED-MANIFEST", "PRIV-DATA-SAFETY-FORM", "PRIV-PRIVACY-POLICY",
                "POL-PLAY-BILLING", "POL-CONTENT-RATING", "POL-TARGET-AUDIENCE", "POL-ADS-DECLARATION", "POL-APP-ACCESS",
                "POL-PERMISSIONS-DECLARATION", "POL-STORE-LISTING", "PRIV-SDK-DATA-DISCLOSURE")}}))
        # remove background location and cleartext from the source manifest
        (root / "android/app/src/main/AndroidManifest.xml").write_text(
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application android:label="Demo"/></manifest>')
        (root / "lib/main.dart").write_text("void main() {}\n")
        out = self.tmp / "out"
        code, stdout, _ = self.run_cli("gate", "--release", str(root), "--aab", str(aab), "--output-dir", str(out),
                                       "--declarations", str(decl), "--json", "--as-of", "2026-10-16")
        summary = json.loads(stdout)
        r = self.report(out)
        bad = [f for f in r["findings"] if f["severity"] in ("BLOCKER", "CRITICAL", "WARNING")]
        self.assertEqual(bad, [], bad)
        self.assertEqual(summary["status"], "READY")
        self.assertEqual(code, 0)
        self.assertIn("não garante aprovação", r["status_disclaimer"])

    def test_quick_mode_not_ready_and_offline(self):
        root = fx.make_flutter_project(self.tmp / "proj")
        out = self.tmp / "out"
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            code, stdout, _ = self.run_cli("audit", "--quick", str(root), "--output-dir", str(out), "--json")
        self.assertIn(code, (1, 2))
        r = self.report(out)
        self.assertEqual(r["mode"], "quick")
        self.assertTrue(any("Sem Java" in l or "desativad" in l or "indisponível" in l for l in r["limitations"]))


class TestCLI(CLIBase):
    def test_usage_errors_exit_3(self):
        self.assertEqual(self.run_cli()[0], 3)
        self.assertEqual(self.run_cli("audit", "--quick")[0], 3)
        self.assertEqual(self.run_cli("audit", "--aab", str(self.tmp / "missing.aab"))[0], 3)
        self.assertEqual(self.run_cli("gate")[0], 3)  # --release required
        self.assertEqual(self.run_cli("audit", "--quick", "--full", ".")[0], 3)

    def test_output_dir_inside_project_refused(self):
        root = fx.make_flutter_project(self.tmp / "proj")
        code, _, err = self.run_cli("audit", "--quick", str(root), "--output-dir", str(root / "out"))
        self.assertEqual(code, 3)
        self.assertFalse((root / "out").exists())

    def test_aab_artifact_audit_and_cache(self):
        aab = fx.make_aab(self.tmp / "app.aab", fx.manifest_tree(target_sdk=35), libs={"arm64-v8a/l.so": fx.make_elf(0x1000)})
        out = self.tmp / "out"
        code, stdout, _ = self.run_cli("audit", "--aab", str(aab), "--output-dir", str(out), "--json", "--as-of", "2026-10-16")
        self.assertEqual(code, 2)
        s = json.loads(stdout)
        self.assertEqual(s["status"], "BLOCKED")
        r = self.report(out)
        blockers = {f["rule_id"] for f in r["findings"] if f["severity"] == "BLOCKER"}
        self.assertTrue({"TECH-TARGET-API-LEVEL", "TECH-16KB-PAGE-SIZE"} <= blockers, blockers)
        md = (out / "google-play-audit-report.md").read_text()
        for label in ("ANDROID BUILD", "TARGET SDK", "16 KB PAGE COMPATIBILITY", "PERMISSIONS", "DATA SAFETY",
                      "GOOGLE PLAY BILLING", "PLAY CONSOLE METADATA", "FINAL STATUS: BLOCKED", "FINDINGS:", "RECOMMENDED ACTIONS:"):
            self.assertIn(label, md)
        # second run hits the artifact cache
        self.run_cli("audit", "--aab", str(aab), "--output-dir", str(out), "--json", "--as-of", "2026-10-16")
        r2 = self.report(out)
        self.assertGreaterEqual(r2["cache"]["hits"], 1)
        self.assertTrue(r2["inventory"]["artifacts"][0]["from_cache"])

    def test_apk_only_warns_aab_required(self):
        apk = fx.make_apk(self.tmp / "app.apk")
        out = self.tmp / "out"
        code, _, _ = self.run_cli("audit", "--apk", str(apk), "--output-dir", str(out))
        r = self.report(out)
        self.assertIn("WARNING", {f["severity"] for f in r["findings"] if f["rule_id"] == "TECH-AAB-REQUIRED"})
        self.assertEqual(code, 1)

    def test_rules_list(self):
        code, out, _ = self.run_cli("rules", "--list", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)), len(RULES))

    def test_check_sources_offline_and_network_errors(self):
        out = self.tmp / "o"
        code, stdout, _ = self.run_cli("rules", "--check-sources", "--offline", "--output-dir", str(out), "--json")
        res = json.loads(stdout)
        self.assertEqual(res["fetched"], 0)
        self.assertTrue(res["offline"])
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no network")):
            code, stdout, _ = self.run_cli("rules", "--check-sources", "--output-dir", str(out), "--json", "--ttl-hours", "0")
        res = json.loads(stdout)
        self.assertEqual(res["ok"], 0)
        self.assertTrue(all("URLError" in e for e in res["errors"].values()))

    def test_drift_detection(self):
        page = "Starting August 31, 2027: New apps and app updates must target Android 17 (API level 37) or higher"
        self.assertTrue(guardian.detect_target_api_drift(page, RULES))
        ok = "New apps and app updates must target Android 16 (API level 36) or higher"
        self.assertEqual(guardian.detect_target_api_drift(ok, RULES), [])

    def test_module_failure_is_isolated(self):
        aab = fx.make_aab(self.tmp / "app.aab")
        out = self.tmp / "out"
        with mock.patch("validate_signing.validate_signing", side_effect=RuntimeError("boom")):
            code, _, _ = self.run_cli("audit", "--aab", str(aab), "--output-dir", str(out), "--no-cache")
        r = self.report(out)
        self.assertTrue(any(f["title"] == "Módulo de assinatura falhou" for f in r["findings"]))
        self.assertIn(code, (1, 2))


class TestRedaction(TempDirCase):
    def test_redact(self):
        self.assertNotIn("hunter2secret", common.redact('storePassword "hunter2secret"'))
        self.assertNotIn("AIzaSyA1234567890abcdefghijklmnopqrstuv", common.redact("key AIzaSyA1234567890abcdefghijklmnopqrstuv"))
        self.assertEqual(common.redact("keyPassword keystoreProperties['keyPassword']"), "keyPassword keystoreProperties['keyPassword']")

    def test_hardcoded_secret_scan_redacts(self):
        root = fx.make_flutter_project(self.tmp / "p", extra_dart='const k = "AIzaSyA1234567890abcdefghijklmnopqrstuv";\n')
        sig = inspect_project.scan_sources(root)
        self.assertEqual(len(sig["secrets"]), 1)
        self.assertNotIn("1234567890abcdefghijklmnop", json.dumps(sig))


class TestSkillMetadata(TempDirCase):
    def test_frontmatter(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        self.assertIsNotNone(m)
        fm = m.group(1)
        name = re.search(r"(?m)^name:\s*(.+)$", fm).group(1).strip()
        desc = re.search(r"(?m)^description:\s*(.+)$", fm).group(1).strip().strip('"')
        self.assertEqual(name, "google-play-review-guardian")
        self.assertLessEqual(len(desc), 60)
        self.assertTrue(desc.endswith("."))
        for key in ("version: 0.1.0", "license: MIT", "category: release", "appstore-review-guardian"):
            self.assertIn(key, fm)
        for h in ("# Google Play Review Guardian Skill", "## When to Use", "## Prerequisites", "## How to Run",
                  "## Quick Reference", "## Procedure", "## Pitfalls", "## Verification"):
            self.assertIn(h, text)

    def test_referenced_files_exist(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for rel in set(re.findall(r"`((?:scripts|references|rules|schemas)/[A-Za-z0-9_.\-/]+)`", text)):
            self.assertTrue((SKILL_DIR / rel).exists(), rel)

    def test_scripts_stdlib_only(self):
        allowed_third_party = {"cryptography"}  # optional, guarded import in validate_play_console
        for py in (SKILL_DIR / "scripts").glob("*.py"):
            for mod in re.findall(r"(?m)^\s*(?:from|import)\s+([A-Za-z_][\w]*)", py.read_text()):
                if mod in allowed_third_party:
                    continue
                local = (SKILL_DIR / "scripts" / f"{mod}.py").exists()
                self.assertTrue(local or mod in __import__("sys").stdlib_module_names or mod == "__future__", f"{py.name}: {mod}")
