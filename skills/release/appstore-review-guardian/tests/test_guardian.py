"""Unit tests for appstore-review-guardian (stdlib unittest, offline, no real apps)."""
from __future__ import annotations

import io
import json
import plistlib
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))
sys.path.insert(0, str(HERE))

import check_sources  # noqa: E402
import common  # noqa: E402
import fixtures as fx  # noqa: E402
import generate_report  # noqa: E402
import guardian  # noqa: E402
import inspect_asc  # noqa: E402
import inspect_dependencies  # noqa: E402
import inspect_ipa  # noqa: E402
import inspect_project  # noqa: E402
import macho  # noqa: E402
import validate_entitlements  # noqa: E402
import validate_plist  # noqa: E402
import validate_privacy  # noqa: E402
import validate_signing  # noqa: E402
from common import BLOCKER, CRITICAL, INFO, NOT_APPLICABLE, NOT_VERIFIED, PASS, WARNING, Finding  # noqa: E402

try:
    import jsonschema  # noqa: F401
    HAVE_JSONSCHEMA = True
except ImportError:
    HAVE_JSONSCHEMA = False


def by_rule(findings, rule_id):
    return [f for f in findings if f.rule_id == rule_id]


def sevs(findings, rule_id):
    return {f.severity for f in by_rule(findings, rule_id)}


def no_tools():
    """Simulate Linux / no Xcode: every external tool missing."""
    return mock.patch("shutil.which", return_value=None)


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="asrg-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# --------------------------------------------------------------------------- skill format / rules
class TestSkillFormat(unittest.TestCase):
    def test_frontmatter(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        self.assertIsNotNone(m)
        fm = m.group(1)
        desc = re.search(r"^description:\s*(.+)$", fm, re.M).group(1).strip().strip("\"'")
        self.assertLessEqual(len(desc), 60)
        self.assertTrue(desc.endswith("."))
        self.assertRegex(fm, r"(?m)^name:\s*appstore-review-guardian$")
        self.assertRegex(fm, r"(?m)^version:\s*0\.1\.0$")
        self.assertIn("category: release", fm)
        self.assertIn("google-play-review-guardian", fm)
        order = ["# App Store Review Guardian Skill", "## When to Use", "## Prerequisites", "## How to Run",
                 "## Quick Reference", "## Procedure", "## Pitfalls", "## Verification"]
        pos = [text.find(h) for h in order]
        self.assertTrue(all(p >= 0 for p in pos), pos)
        self.assertEqual(pos, sorted(pos))

    def test_references_exist(self):
        for name in ("apple-review-guidelines", "privacy-compliance", "ios-release-requirements", "flutter-ios-guidelines",
                     "app-store-connect-checklist", "rejection-catalog", "sources", "limitations"):
            self.assertTrue((SKILL / "references" / f"{name}.md").is_file(), name)


class TestRuleBase(unittest.TestCase):
    REQUIRED = {"id", "name", "description", "category", "guideline", "official_url", "consulted_at", "effective_from",
                "applies_when", "severity_if_violated", "verification_method", "requirement_level"}

    def test_rules_have_required_fields(self):
        rb = common.RuleBase()
        self.assertGreater(len(rb.rules), 50)
        for r in rb.rules.values():
            self.assertTrue(self.REQUIRED <= set(r), r["id"])
            self.assertIn(r["verification_method"], common.METHODS)
            self.assertIn(r["requirement_level"], ("mandatory", "recommended"))
            self.assertIn(r["severity_if_violated"], common.SEVERITIES)
            self.assertTrue(r["official_url"].startswith("https://"))
            self.assertRegex(r["consulted_at"], r"^\d{4}-\d{2}-\d{2}$")

    def test_recommended_rules_never_blocking(self):
        for r in common.RuleBase().rules.values():
            if r["requirement_level"] == "recommended":
                self.assertNotIn(r["severity_if_violated"], (BLOCKER, CRITICAL), r["id"])

    def test_reference_data_from_apple(self):
        ref = common.RuleBase().reference
        self.assertEqual(set(ref["required_reason_categories"]), {
            "NSPrivacyAccessedAPICategoryFileTimestamp", "NSPrivacyAccessedAPICategorySystemBootTime",
            "NSPrivacyAccessedAPICategoryDiskSpace", "NSPrivacyAccessedAPICategoryActiveKeyboards",
            "NSPrivacyAccessedAPICategoryUserDefaults"})
        self.assertIn("CA92.1", ref["required_reason_categories"]["NSPrivacyAccessedAPICategoryUserDefaults"])
        self.assertEqual(ref["upload_minimums"]["min_ios_deployment_target"], "13.0")
        self.assertIn("NSCameraUsageDescription", ref["usage_description_keys"])
        self.assertIn("Flutter", ref["sdks_requiring_manifest"])


# --------------------------------------------------------------------------- finding semantics
class TestSeverityClassification(unittest.TestCase):
    def test_heuristic_blocker_downgraded(self):
        d = Finding("PRIV-RRAPI-002", BLOCKER, "app", "símbolo encontrado").to_dict(1)
        self.assertEqual(d["severity"], WARNING)
        self.assertTrue(d["notes"])

    def test_recommended_rule_capped(self):
        d = Finding("PLIST-ENC-001", BLOCKER, "app", "x").to_dict(1)
        self.assertEqual(d["severity"], WARNING)

    def test_pass_without_evidence_becomes_not_verified(self):
        d = Finding("PLIST-ID-001", PASS, "app", "  ").to_dict(1)
        self.assertEqual(d["severity"], NOT_VERIFIED)

    def test_deterministic_blocker_kept(self):
        d = Finding("ENT-GTA-001", BLOCKER, "app", "get-task-allow=true").to_dict(1)
        self.assertEqual(d["severity"], BLOCKER)
        self.assertEqual(d["consulted_at"], "2026-10-16")
        self.assertTrue(d["official_url"].startswith("https://developer.apple.com"))

    def test_invalid_severity_rejected(self):
        with self.assertRaises(ValueError):
            Finding("PLIST-ID-001", "FATAL", "app", "x").to_dict(1)

    def test_redaction(self):
        d = Finding("PLIST-ID-001", WARNING, "app", "token=abcd1234efgh5678ijkl and AIza" + "B" * 35).to_dict(1)
        self.assertNotIn("abcd1234efgh5678ijkl", d["evidence"])
        self.assertIn("[REDACTED]", d["evidence"])
        self.assertNotIn("BBBBBBBBBBBB", d["evidence"])

    def test_gate_decisions(self):
        def mk(sev, rule="PLIST-ID-001"):
            return Finding(rule, sev, "app", "ev").to_dict(1)
        self.assertEqual(generate_report.decide([mk(PASS)], "full", True)[0], "READY")
        self.assertEqual(generate_report.decide([mk(PASS), mk(BLOCKER)], "full", True)[0], "BLOCKED")
        self.assertEqual(generate_report.decide([mk(WARNING)], "full", True)[0], "NEEDS_REVIEW")
        self.assertEqual(generate_report.decide([mk(NOT_VERIFIED)], "full", True)[0], "NEEDS_REVIEW")
        self.assertEqual(generate_report.decide([mk(PASS)], "gate", False)[0], "NEEDS_REVIEW")
        # no deterministic PASS at all: nothing was actually verified, so never READY
        self.assertEqual(generate_report.decide([mk(INFO), mk(NOT_APPLICABLE)], "full", True)[0], "NEEDS_REVIEW")
        self.assertEqual(generate_report.decide([], "full", True)[0], "NEEDS_REVIEW")
        self.assertEqual(generate_report.decide([mk(PASS), mk(INFO), mk(NOT_APPLICABLE)], "full", True)[0], "READY")
        # heuristic CRITICAL (e.g. 4.8) never blocks by itself
        crit_h = Finding("REV-LOGIN-001", CRITICAL, "project", "ev", verification_method="deterministic").to_dict(1)
        crit_h["verification_method"] = "heuristic"
        self.assertEqual(generate_report.decide([crit_h], "full", True)[0], "NEEDS_REVIEW")


# --------------------------------------------------------------------------- Info.plist
class TestInfoPlist(TmpCase):
    def test_valid_bundle_plist(self):
        f, facts = validate_plist.validate_info_plist(fx.info_plist(), "bundle")
        bad = [x for x in f if x.severity in (BLOCKER, CRITICAL, WARNING)]
        self.assertEqual(bad, [], [(x.rule_id, x.evidence) for x in bad])
        self.assertEqual(sevs(f, "BUILD-SDK-001"), {PASS})
        self.assertEqual(sevs(f, "PLIST-MINOS-001"), {PASS})
        self.assertEqual(facts["bundle_id"], "com.example.app")

    def test_missing_encryption_key_warning(self):
        f, _ = validate_plist.validate_info_plist(fx.info_plist(ITSAppUsesNonExemptEncryption=None), "bundle")
        self.assertEqual(sevs(f, "PLIST-ENC-001"), {WARNING})

    def test_ats_arbitrary_loads(self):
        f, _ = validate_plist.validate_info_plist(fx.info_plist(NSAppTransportSecurity={"NSAllowsArbitraryLoads": True}), "bundle")
        self.assertEqual(sevs(f, "PLIST-ATS-001"), {WARNING})

    def test_empty_and_generic_usage_descriptions(self):
        f, _ = validate_plist.validate_info_plist(fx.info_plist(NSMicrophoneUsageDescription="",
                                                               NSPhotoLibraryUsageDescription="Allow this app to access your photos"), "bundle")
        self.assertIn(CRITICAL, sevs(f, "PRIV-USAGE-001"))
        self.assertEqual(sevs(f, "PRIV-USAGE-002"), {WARNING})

    def test_unknown_usage_key(self):
        f, _ = validate_plist.validate_info_plist(fx.info_plist(NSCamraUsageDescription="typo"), "bundle")
        self.assertEqual(sevs(f, "PRIV-USAGE-004"), {WARNING})

    def test_old_toolchain_and_min_os_blocked(self):
        f, _ = validate_plist.validate_info_plist(fx.info_plist(DTXcode="1620", DTSDKName="iphoneos18.2", DTPlatformVersion="18.2",
                                                               MinimumOSVersion="12.0"), "bundle")
        self.assertEqual(sevs(f, "BUILD-SDK-001"), {BLOCKER})
        self.assertEqual(sevs(f, "PLIST-MINOS-001"), {BLOCKER})

    def test_missing_dt_keys_not_verified(self):
        f, _ = validate_plist.validate_info_plist(fx.info_plist(DTXcode=None, DTSDKName=None), "bundle")
        self.assertEqual(sevs(f, "BUILD-SDK-001"), {NOT_VERIFIED})

    def test_bad_versions(self):
        f, _ = validate_plist.validate_info_plist(fx.info_plist(CFBundleShortVersionString="1.2", CFBundleVersion="42a"), "bundle")
        self.assertEqual(sevs(f, "PLIST-VER-001"), {CRITICAL})
        self.assertEqual(sevs(f, "PLIST-VER-002"), {BLOCKER})

    def test_unresolved_variable_bundle_vs_project(self):
        pl = fx.info_plist(CFBundleIdentifier="$(PRODUCT_BUNDLE_IDENTIFIER)")
        self.assertEqual(sevs(validate_plist.validate_info_plist(pl, "bundle")[0], "PLIST-ID-001"), {BLOCKER})
        self.assertEqual(sevs(validate_plist.validate_info_plist(pl, "project")[0], "PLIST-ID-001"), {NOT_VERIFIED})
        f, _ = validate_plist.validate_info_plist(pl, "project", resolved={"CFBundleIdentifier": "com.x.y"})
        self.assertEqual(sevs(f, "PLIST-ID-001"), {PASS})

    def test_invalid_background_mode(self):
        f, _ = validate_plist.validate_info_plist(fx.info_plist(UIBackgroundModes=["remote-notification", "always-on"]), "bundle")
        self.assertEqual(sevs(f, "PLIST-BG-001"), {WARNING})

    def test_binary_and_corrupt_plist_files(self):
        p = self.tmp / "Info.plist"
        p.write_bytes(plistlib.dumps(fx.info_plist(), fmt=plistlib.FMT_BINARY))
        f, facts = validate_plist.validate_file(p)
        self.assertEqual(sevs(f, "PLIST-PARSE-001"), {PASS})
        self.assertEqual(facts["bundle_id"], "com.example.app")
        p.write_bytes(b"<plist><dict><key>x</key>")
        f, _ = validate_plist.validate_file(p)
        self.assertEqual(sevs(f, "PLIST-PARSE-001"), {BLOCKER})


# --------------------------------------------------------------------------- privacy manifest
class TestPrivacyManifest(unittest.TestCase):
    def run_pm(self, d):
        return validate_privacy.validate_manifest_bytes(plistlib.dumps(d), "app", "PrivacyInfo.xcprivacy")

    def test_valid(self):
        f, facts = self.run_pm(fx.privacy_manifest())
        self.assertTrue(facts["valid"])
        self.assertEqual({x.severity for x in f}, {PASS})

    def test_tracking_true_without_domains(self):
        f, facts = self.run_pm(fx.privacy_manifest(NSPrivacyTracking=True, NSPrivacyTrackingDomains=[]))
        self.assertEqual(sevs(f, "PRIV-TRACK-001"), {BLOCKER})
        self.assertFalse(facts["valid"])

    def test_tracking_wrong_type_and_bad_domain(self):
        f, _ = self.run_pm(fx.privacy_manifest(NSPrivacyTracking="YES", NSPrivacyTrackingDomains=["https://ads.example.com/x"]))
        ev = by_rule(f, "PRIV-TRACK-001")[0].evidence
        self.assertIn("Boolean", ev)
        self.assertIn("mal formados", ev)

    def test_invalid_category_and_reason(self):
        f, _ = self.run_pm(fx.privacy_manifest(NSPrivacyAccessedAPITypes=[
            {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryUserDefaults", "NSPrivacyAccessedAPITypeReasons": ["DDA9.1"]},
            {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryCamera", "NSPrivacyAccessedAPITypeReasons": ["X"]},
            {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryDiskSpace", "NSPrivacyAccessedAPITypeReasons": []}]))
        ev = by_rule(f, "PRIV-RRAPI-001")[0].evidence
        self.assertEqual(sevs(f, "PRIV-RRAPI-001"), {BLOCKER})
        self.assertIn("DDA9.1", ev)
        self.assertIn("NSPrivacyAccessedAPICategoryCamera", ev)
        self.assertIn("vazio", ev)

    def test_invalid_collected_data(self):
        f, _ = self.run_pm(fx.privacy_manifest(NSPrivacyCollectedDataTypes=[{"NSPrivacyCollectedDataType": "NSPrivacyCollectedDataTypeShoeSize",
                                                                              "NSPrivacyCollectedDataTypeLinked": "yes",
                                                                              "NSPrivacyCollectedDataTypePurposes": ["Fun"]}]))
        self.assertEqual(sevs(f, "PRIV-DATA-001"), {BLOCKER})

    def test_unknown_top_key_and_malformed(self):
        f, _ = self.run_pm({"NSPrivacyTracking": False, "NSPrivacyFoo": 1})
        self.assertIn(BLOCKER, sevs(f, "PRIV-MANIFEST-002"))
        f, facts = validate_privacy.validate_manifest_bytes(b"not a plist", "app", "x")
        self.assertEqual(sevs(f, "PRIV-MANIFEST-002"), {BLOCKER})
        self.assertFalse(facts["valid"])


# --------------------------------------------------------------------------- entitlements
class TestEntitlements(unittest.TestCase):
    def test_get_task_allow_bundle_blocker(self):
        f, _ = validate_entitlements.validate_entitlements({"get-task-allow": True, "aps-environment": "production"}, "bundle", "app", "x")
        self.assertEqual(sevs(f, "ENT-GTA-001"), {BLOCKER})

    def test_aps_development_bundle_critical_project_warning(self):
        f, _ = validate_entitlements.validate_entitlements({"aps-environment": "development"}, "bundle", "app", "x")
        self.assertEqual(sevs(f, "ENT-APS-001"), {CRITICAL})
        f, _ = validate_entitlements.validate_entitlements({"aps-environment": "development"}, "project", "project", "x")
        self.assertEqual(sevs(f, "ENT-APS-001"), {WARNING})

    def test_profile_allowlist(self):
        f, _ = validate_entitlements.validate_entitlements({"com.apple.developer.healthkit": True, "aps-environment": "production"},
                                                           "bundle", "app", "x", profile_ents={"aps-environment": "production"})
        self.assertEqual(sevs(f, "ENT-PROFILE-001"), {BLOCKER})

    def test_push_without_aps(self):
        f, _ = validate_entitlements.validate_entitlements({}, "project", "project", "x", info_plist={"UIBackgroundModes": ["remote-notification"]})
        self.assertEqual(sevs(f, "REV-PUSH-001"), {WARNING})

    def test_associated_domains_offline_not_verified_and_aasa_mock(self):
        ents = {"com.apple.developer.associated-domains": ["applinks:example.com"]}
        f, _ = validate_entitlements.validate_entitlements(ents, "project", "project", "x")
        self.assertEqual(sevs(f, "ENT-ASSOC-001"), {NOT_VERIFIED})
        public = lambda h, p, **kw: [(2, 1, 6, "", ("93.184.215.14", p))]  # noqa: E731 - no real DNS in tests
        ok = validate_entitlements.check_aasa(["applinks:example.com"], lambda u, ip: (200, b'{"applinks":{"details":[]}}'), resolver=public)
        self.assertEqual({x.severity for x in ok}, {PASS})
        missing = validate_entitlements.check_aasa(["applinks:example.com"], lambda u, ip: (404, b""), resolver=public)
        self.assertEqual({x.severity for x in missing}, {WARNING})

        def boom(u, ip):
            raise OSError("offline")
        off = validate_entitlements.check_aasa(["applinks:example.com"], boom, resolver=public)
        self.assertEqual({x.severity for x in off}, {NOT_VERIFIED})


# --------------------------------------------------------------------------- Mach-O
class TestMachO(TmpCase):
    def test_parse_and_signature(self):
        p = self.tmp / "bin"
        p.write_bytes(fx.macho(signed=True, entitlements={"get-task-allow": True}, cms_text=b"Apple Development: Dev (X)\x00",
                               dylibs=["/System/Library/Frameworks/AppTrackingTransparency.framework/AppTrackingTransparency"]))
        mo = macho.parse(p)
        sl = mo.preferred()
        self.assertEqual(mo.archs(), ["arm64"])
        self.assertEqual(sl.platform, "ios")
        self.assertEqual(sl.minos, "15.0.0")
        self.assertTrue(sl.dylibs[0].endswith("AppTrackingTransparency"))
        sig = macho.read_signature(p, sl)
        self.assertEqual(sig["entitlements"], {"get-task-allow": True})
        self.assertEqual(macho.certificate_names(sig["cms"]), ["Apple Development: Dev (X)"])

    def test_scan_strings(self):
        p = self.tmp / "bin"
        p.write_bytes(fx.macho(strings=[b"_mach_absolute_time", b"_statfs"]))
        hits = macho.scan_strings(p, inspect_ipa._needles())
        self.assertIn("NSPrivacyAccessedAPICategorySystemBootTime", hits)
        self.assertIn("NSPrivacyAccessedAPICategoryDiskSpace", hits)
        self.assertNotIn("NSPrivacyAccessedAPICategoryActiveKeyboards", hits)

    def test_not_macho(self):
        p = self.tmp / "x"
        p.write_bytes(b"#!/bin/sh\necho hi\n")
        self.assertFalse(macho.is_macho(p))


# --------------------------------------------------------------------------- IPA parsing & safety
class TestIpa(TmpCase):
    def make_ipa(self, name="ok.ipa", **kw):
        app = fx.build_app(self.tmp / f"src-{name}", **kw)
        return fx.zip_app(app, self.tmp / name)

    def test_minimal_unsigned_ipa(self):
        ipa = self.make_ipa()
        with no_tools():
            f, facts = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(sevs(f, "IPA-STRUCT-001"), {PASS})
        self.assertEqual(sevs(f, "IPA-STRUCT-002"), {PASS})
        self.assertEqual(sevs(f, "BIN-ARCH-001"), {PASS})
        self.assertEqual(sevs(f, "SIGN-001"), {BLOCKER})
        for rid in ("SIGN-002", "SIGN-003", "SIGN-004"):
            self.assertEqual(sevs(f, rid), {NOT_VERIFIED}, rid)
        self.assertEqual(sevs(f, "PRIV-MANIFEST-002"), {PASS})
        self.assertEqual(facts["payload_apps"], ["Runner.app"])
        self.assertEqual([fw["name"] for fw in facts["frameworks"]], ["Flutter"])

    def test_signed_ipa_with_app_store_profile_offline(self):
        ipa = self.make_ipa(signed=True, profile=fx.profile_bytes("app_store"))
        with no_tools():
            f, facts = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(sevs(f, "SIGN-001"), {PASS})
        self.assertEqual(sevs(f, "SIGN-002"), {NOT_VERIFIED})  # no codesign => never PASS
        self.assertEqual(sevs(f, "SIGN-003"), {PASS})
        self.assertEqual(sevs(f, "SIGN-004"), {PASS})
        self.assertEqual(sevs(f, "ENT-GTA-001"), {PASS})
        self.assertEqual(sevs(f, "ENT-APS-001"), {PASS})
        self.assertEqual(sevs(f, "ENT-PROFILE-001"), {PASS})
        self.assertEqual(facts["signing"]["profile"]["decoded_with"], "stdlib")

    def test_debug_signed_ipa_blocked(self):
        ipa = self.make_ipa(signed=True, entitlements={"get-task-allow": True, "aps-environment": "development",
                                                       "application-identifier": "ABCDE12345.com.example.app"},
                            authority="Apple Development: Dev (ABCDE12345)", profile=fx.profile_bytes("development"))
        with no_tools():
            f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(sevs(f, "ENT-GTA-001"), {BLOCKER})
        self.assertEqual(sevs(f, "SIGN-003"), {BLOCKER})
        self.assertEqual(sevs(f, "SIGN-004"), {BLOCKER})
        self.assertEqual(sevs(f, "ENT-APS-001"), {CRITICAL})

    def test_expired_profile(self):
        ipa = self.make_ipa(signed=True, profile=fx.profile_bytes("app_store", expired=True))
        with no_tools():
            f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(sevs(f, "SIGN-004"), {BLOCKER})
        self.assertIn("expirado", by_rule(f, "SIGN-004")[0].evidence)

    def test_binary_info_plist_in_ipa(self):
        ipa = self.make_ipa(binary_plist=True)
        f, facts = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(facts["info"]["bundle_id"], "com.example.app")

    def test_missing_payload(self):
        ipa = self.tmp / "nopayload.ipa"
        with zipfile.ZipFile(ipa, "w") as zf:
            zf.writestr("Runner.app/Info.plist", plistlib.dumps(fx.info_plist()))
        f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(sevs(f, "IPA-STRUCT-002"), {BLOCKER})

    def test_missing_info_plist(self):
        ipa = self.tmp / "noinfo.ipa"
        with zipfile.ZipFile(ipa, "w") as zf:
            zf.writestr("Payload/Runner.app/Runner", fx.macho())
        f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(sevs(f, "PLIST-PARSE-001"), {BLOCKER})

    def test_zip_slip_rejected(self):
        ipa = self.tmp / "slip.ipa"
        with zipfile.ZipFile(ipa, "w") as zf:
            zf.writestr("Payload/Runner.app/Info.plist", plistlib.dumps(fx.info_plist()))
            zf.writestr("Payload/../evil", b"x")
        f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(sevs(f, "IPA-STRUCT-001"), {BLOCKER})
        self.assertIn("zip-slip", by_rule(f, "IPA-STRUCT-001")[0].evidence)
        self.assertFalse((self.tmp / "evil").exists())

    def test_absolute_path_rejected(self):
        ipa = self.tmp / "abs.ipa"
        with zipfile.ZipFile(ipa, "w") as zf:
            zf.writestr("/tmp/evil", b"x")
        f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertIn("absoluto", by_rule(f, "IPA-STRUCT-001")[0].evidence)

    def test_symlink_rejected(self):
        f, _ = inspect_ipa.inspect_ipa(fx.zip_with_symlink(self.tmp / "link.ipa"))
        self.assertEqual(sevs(f, "IPA-STRUCT-001"), {BLOCKER})
        self.assertIn("symlink", by_rule(f, "IPA-STRUCT-001")[0].evidence)

    def test_zip_bomb_ratio_rejected(self):
        f, _ = inspect_ipa.inspect_ipa(fx.zip_bomb(self.tmp / "bomb.ipa"))
        self.assertEqual(sevs(f, "IPA-STRUCT-001"), {BLOCKER})
        self.assertIn("zip bomb", by_rule(f, "IPA-STRUCT-001")[0].evidence)

    def test_total_size_cap(self):
        ipa = self.tmp / "big.ipa"
        with zipfile.ZipFile(ipa, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Payload/Runner.app/a", b"\0" * 2048)
            zf.writestr("Payload/Runner.app/b", b"\0" * 2048)
        with mock.patch.object(common, "MAX_TOTAL_UNCOMPRESSED", 3000):
            f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertIn("excede", by_rule(f, "IPA-STRUCT-001")[0].evidence)

    def test_corrupt_zip(self):
        ipa = self.tmp / "corrupt.ipa"
        ipa.write_bytes(b"PK\x03\x04garbage-not-a-zip")
        f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(sevs(f, "IPA-STRUCT-001"), {BLOCKER})

    def test_temp_dirs_cleaned_and_private(self):
        made = []
        real = common.make_private_tempdir

        def spy(prefix="asrg-"):
            d = real(prefix)
            made.append((d, d.stat().st_mode & 0o777))
            return d
        ipa = self.make_ipa()
        with mock.patch.object(inspect_ipa, "make_private_tempdir", spy):
            inspect_ipa.inspect_ipa(ipa)
        self.assertTrue(made)
        for d, mode in made:
            self.assertEqual(mode, 0o700)
            self.assertFalse(d.exists())

    def test_ipa_not_modified(self):
        ipa = self.make_ipa()
        before = common.sha256_file(ipa)
        inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(before, common.sha256_file(ipa))

    def test_missing_app_manifest_with_rr_symbols(self):
        ipa = self.make_ipa(manifest=None, strings=[b"_mach_absolute_time"], frameworks={})
        f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(sevs(f, "PRIV-MANIFEST-001"), {WARNING})
        self.assertEqual(sevs(f, "PRIV-RRAPI-002"), {WARNING})

    def test_invalid_app_manifest_in_ipa(self):
        ipa = self.make_ipa(manifest=fx.privacy_manifest(NSPrivacyTracking=True, NSPrivacyTrackingDomains=[]))
        f, _ = inspect_ipa.inspect_ipa(ipa)
        app_track = {x.severity for x in by_rule(f, "PRIV-TRACK-001") if x.component == "app"}
        self.assertEqual(app_track, {BLOCKER})

    def test_listed_sdk_without_manifest(self):
        ipa = self.make_ipa(frameworks={"Flutter": {"manifest": True}, "Alamofire": {"manifest": False}})
        f, _ = inspect_ipa.inspect_ipa(ipa)
        alamo = [x for x in by_rule(f, "PRIV-SDK-001") if x.component == "Alamofire"]
        self.assertEqual({x.severity for x in alamo}, {WARNING})

    def test_simulator_slice_and_debug_artifacts(self):
        ipa = self.make_ipa(frameworks={"Foo": {"cpu": fx.CPU_X86_64, "platform": 7}}, dsym=True, flutter_debug=True)
        f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(sevs(f, "BIN-ARCH-002"), {WARNING})
        self.assertEqual(sevs(f, "BIN-DEBUG-001"), {WARNING})
        self.assertEqual(sevs(f, "BIN-FLUTTER-001"), {CRITICAL})

    def test_simulator_main_binary_blocked(self):
        ipa = self.make_ipa(cpu=fx.CPU_X86_64, platform=7)
        f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(sevs(f, "BIN-ARCH-001"), {BLOCKER})

    def test_att_linked_without_usage_key(self):
        ipa = self.make_ipa(dylibs=["/System/Library/Frameworks/AppTrackingTransparency.framework/AppTrackingTransparency"])
        f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual(sevs(f, "PRIV-ATT-001"), {CRITICAL})

    def test_quick_mode_extracts_subset(self):
        app = fx.build_app(self.tmp / "q", frameworks={"Flutter": {"manifest": True}})
        ipa = fx.zip_app(app, self.tmp / "q.ipa", extra=[("Payload/Runner.app/Assets.car", b"x" * 4096),
                                                       ("Payload/Runner.app/Frameworks/App.framework/flutter_assets/big.bin", b"y" * 4096)])
        with no_tools():
            f, facts = inspect_ipa.inspect_ipa(ipa, full=False)
        self.assertLess(facts["extracted_members"], len(zipfile.ZipFile(ipa).namelist()))
        self.assertEqual(sevs(f, "PRIV-MANIFEST-002"), {PASS})


class TestSigningTools(TmpCase):
    def test_codesign_mocked_failure_is_blocker(self):
        app = fx.build_app(self.tmp, signed=True, profile=fx.profile_bytes())

        def which(name):
            return f"/usr/bin/{name}" if name == "codesign" else None

        def fake_run(args, timeout=60, input_bytes=None):
            if "--verify" in args:
                return 1, b"", b"Runner.app: a sealed resource is missing or invalid"
            return 0, b"", b"Authority=Apple Distribution: Example (ABCDE12345)\nAuthority=Apple Worldwide Developer Relations\n"
        with mock.patch("shutil.which", side_effect=which), mock.patch.object(validate_signing, "run_tool", fake_run):
            f, facts = validate_signing.check_signing(app)
        self.assertEqual(sevs(f, "SIGN-002"), {BLOCKER})
        self.assertEqual(sevs(f, "SIGN-003"), {PASS})
        self.assertIn("Apple Distribution", facts["authority"][0])

    def test_codesign_ok(self):
        app = fx.build_app(self.tmp, signed=True, profile=fx.profile_bytes())
        with mock.patch("shutil.which", side_effect=lambda n: f"/usr/bin/{n}" if n == "codesign" else None), \
                mock.patch.object(validate_signing, "run_tool", lambda a, timeout=60, input_bytes=None:
                                  (0, b"", b"Authority=Apple Distribution: Example (X)\n")):
            f, _ = validate_signing.check_signing(app)
        self.assertEqual(sevs(f, "SIGN-002"), {PASS})

    def test_run_tool_never_uses_shell(self):
        with mock.patch("shutil.which", return_value="/usr/bin/true"), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=b"", stderr=b"")
            common.run_tool(["codesign", "-dvv", "x; rm -rf /"])
        args, kw = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertFalse(kw.get("shell"))
        self.assertIn("timeout", kw)


# --------------------------------------------------------------------------- Flutter project
class TestFlutterProject(TmpCase):
    def test_social_login_without_apple(self):
        proj = fx.flutter_project(self.tmp, social_login=True, apple_login=False, deletion=False)
        f, facts = inspect_project.inspect_project(proj, "full")
        self.assertEqual(facts["version"], "2.0.1")
        self.assertEqual(facts["build"], "7")
        self.assertEqual(facts["bundle_id"], "com.example.demo")
        self.assertEqual(sevs(f, "REV-LOGIN-001"), {WARNING})
        self.assertEqual(sevs(f, "REV-ACCTDEL-001"), {WARNING})
        self.assertEqual(sevs(f, "PRIV-USAGE-002"), {WARNING})
        self.assertEqual(sevs(f, "PRIV-MANIFEST-001"), {WARNING})
        self.assertEqual(sevs(f, "ENT-APS-001"), {WARNING})
        self.assertEqual(sevs(f, "PROJ-DEPLOY-001"), {PASS})
        self.assertEqual(sevs(f, "PROJ-EXPORT-001"), {PASS})
        # heuristic evidence found => never PASS on a mandatory rule, only NOT_VERIFIED (needs human confirmation)
        self.assertEqual(sevs(f, "PRIV-POLICY-001"), {NOT_VERIFIED})
        self.assertIn(NOT_VERIFIED, sevs(f, "REV-DEMO-001"))

    def test_apple_login_and_deletion(self):
        proj = fx.flutter_project(self.tmp, social_login=True, apple_login=True, deletion=True, privacy_manifest_file=True)
        f, _ = inspect_project.inspect_project(proj, "full")
        self.assertEqual(sevs(f, "REV-LOGIN-001"), {NOT_VERIFIED})  # heuristic: evidence, not proof
        self.assertEqual(sevs(f, "REV-LOGIN-002"), {PASS})  # deterministic entitlement check
        self.assertEqual(sevs(f, "REV-ACCTDEL-001"), {NOT_VERIFIED})
        self.assertIn("indício de exclusão", by_rule(f, "REV-ACCTDEL-001")[0].evidence)
        self.assertEqual(sevs(f, "PRIV-MANIFEST-001"), set())
        self.assertEqual(sevs(f, "PRIV-MANIFEST-002"), {PASS})

    def test_low_deployment_target_blocker(self):
        proj = fx.flutter_project(self.tmp, deployment="12.0")
        f, _ = inspect_project.inspect_project(proj, "full")
        self.assertEqual(sevs(f, "PROJ-DEPLOY-001"), {BLOCKER})

    def test_missing_usage_key_for_plugin(self):
        proj = fx.flutter_project(self.tmp)
        dep = {"plugins": {"ios": [{"name": "image_picker_ios", "path": str(self.tmp / "nope"), "native_build": True}]}}
        (proj / ".flutter-plugins-dependencies").write_text(json.dumps(dep))
        info = plistlib.loads((proj / "ios/Runner/Info.plist").read_bytes())
        info.pop("NSPhotoLibraryUsageDescription")
        (proj / "ios/Runner/Info.plist").write_bytes(plistlib.dumps(info))
        f, _ = inspect_project.inspect_project(proj, "full")
        ev = " ".join(x.evidence for x in by_rule(f, "PRIV-USAGE-003"))
        self.assertIn("NSPhotoLibraryUsageDescription ausente", ev)
        self.assertEqual([x for x in by_rule(f, "PRIV-SDK-001") if x.component == "image_picker_ios"][0].severity, NOT_VERIFIED)

    def test_quick_mode_marks_guideline_checks_not_verified(self):
        proj = fx.flutter_project(self.tmp)
        f, _ = inspect_project.inspect_project(proj, "quick")
        self.assertEqual(sevs(f, "REV-ACCTDEL-001"), {NOT_VERIFIED})

    def test_no_ios_dir_insufficient_evidence(self):
        (self.tmp / "empty").mkdir()
        f, _ = inspect_project.inspect_project(self.tmp / "empty", "full")
        self.assertEqual({x.severity for x in f}, {NOT_VERIFIED})

    def test_project_not_modified(self):
        proj = fx.flutter_project(self.tmp)
        before = fx.tree_digest(proj)
        out = self.tmp / "out"
        with no_tools(), redirect_stdout(io.StringIO()):
            guardian.main(["audit", "--full", str(proj), "--output-dir", str(out)])
        self.assertEqual(before, fx.tree_digest(proj))

    def test_parsers(self):
        lock = inspect_dependencies.parse_pubspec_lock('packages:\n  a:\n    dependency: "direct main"\n    version: "1.0.0"\nsdks:\n  dart: ">=3.0.0 <4.0.0"\n')
        self.assertEqual(lock["packages"]["a"]["version"], "1.0.0")
        self.assertEqual(lock["sdks"]["dart"], ">=3.0.0 <4.0.0")
        pods = inspect_dependencies.parse_podfile_lock("PODS:\n  - AppAuth (2.0.0):\n    - AppAuth/Core (= 2.0.0)\n  - Sentry/HybridSDK (8.1.0)\n\nDEPENDENCIES:\n")
        self.assertEqual(pods, {"AppAuth": "2.0.0", "Sentry": "8.1.0"})


# --------------------------------------------------------------------------- ASC
class TestAsc(unittest.TestCase):
    def test_no_metadata_all_not_verified(self):
        f, _ = inspect_asc.check_asc(None, env={})
        self.assertTrue(f)
        self.assertEqual({x.severity for x in f}, {NOT_VERIFIED})

    def test_metadata_limits(self):
        md = {"name": "X" * 31, "subtitle": "ok", "keywords": "ab,meditação", "description": "<b>hi</b>",
              "privacy_policy_url": "", "support_url": "https://s.example.com"}
        f, _ = inspect_asc.check_asc(md, env={})
        self.assertEqual(sevs(f, "ASC-META-001"), {CRITICAL})
        self.assertEqual(sevs(f, "ASC-META-003"), {CRITICAL})
        self.assertEqual(sevs(f, "ASC-META-004"), {CRITICAL})
        self.assertEqual(sevs(f, "ASC-META-005"), {BLOCKER})
        self.assertEqual(sevs(f, "ASC-META-006"), {PASS})

    def test_api_credentials_never_read(self):
        env = {"ASC_KEY_ID": "K", "ASC_ISSUER_ID": "I", "ASC_KEY_PATH": "/nonexistent/AuthKey.p8"}
        with mock.patch("builtins.open", side_effect=AssertionError("key must not be opened")):
            f, facts = inspect_asc.check_asc(None, env=env)
        self.assertEqual(sevs(f, "ASC-API-001"), {NOT_VERIFIED})
        self.assertNotIn("/nonexistent", json.dumps([x.evidence for x in f]))


# --------------------------------------------------------------------------- sources / offline
class TestSources(TmpCase):
    def test_offline_graceful(self):
        def offline(url):
            raise OSError("network unreachable")
        res = check_sources.check_sources(self.tmp, fetch=offline)
        self.assertEqual(res["summary"]["checked"], 0)
        self.assertGreater(res["summary"]["offline"], 0)
        self.assertTrue((self.tmp / check_sources.CACHE_NAME).is_file())

    def test_change_detection_and_ttl(self):
        url = check_sources.source_urls()[0]
        calls = []

        def fetch(u):
            calls.append(u)
            return 200, b"<main>changed content</main>"
        with mock.patch.object(check_sources, "load_baseline", return_value={url: {"sha256": "0" * 64}}):
            res = check_sources.check_sources(self.tmp, fetch=fetch)
        self.assertTrue(res["sources"][url]["changed_since_baseline"])
        self.assertEqual(len(res["sources"][url]["sha256"]), 64)
        n = len(calls)
        res2 = check_sources.check_sources(self.tmp, fetch=fetch)
        self.assertEqual(len(calls), n)  # TTL reuse, no refetch
        self.assertTrue(res2["sources"][url]["reused"])
        check_sources.check_sources(self.tmp, fetch=fetch, force=True)
        self.assertGreater(len(calls), n)

    def test_docc_url_mapping(self):
        self.assertEqual(check_sources.fetch_url("https://developer.apple.com/documentation/bundleresources/privacy-manifest-files"),
                         "https://developer.apple.com/tutorials/data/documentation/bundleresources/privacy-manifest-files.json")

    def test_http_error_recorded(self):
        res = check_sources.check_sources(self.tmp, fetch=lambda u: (503, b""), force=True)
        self.assertGreater(res["summary"]["errors"], 0)

    def test_cli_offline_flag_no_network(self):
        with mock.patch.object(check_sources, "default_fetch", side_effect=AssertionError("network used")), \
                redirect_stdout(io.StringIO()) as out:
            rc = guardian.main(["rules", "--check-sources", "--offline", "--output-dir", str(self.tmp), "--json"])
        self.assertIn(rc, (0, 1))
        self.assertIn("summary", json.loads(out.getvalue()))


# --------------------------------------------------------------------------- end-to-end + reports
class TestEndToEnd(TmpCase):
    def run_cli(self, *args):
        buf = io.StringIO()
        with no_tools(), redirect_stdout(buf), redirect_stderr(io.StringIO()):
            rc = guardian.main(list(args))
        return rc, buf.getvalue()

    def test_full_audit_reports_schema_valid(self):
        proj = fx.flutter_project(self.tmp)
        ipa = fx.zip_app(fx.build_app(self.tmp / "b"), self.tmp / "a.ipa")
        out = self.tmp / "out"
        rc, stdout = self.run_cli("audit", "--full", str(proj), "--ipa", str(ipa), "--output-dir", str(out), "--json")
        s = json.loads(stdout)
        self.assertEqual(rc, 2)  # unsigned => SIGN-001 BLOCKER
        self.assertEqual(s["final_status"], "BLOCKED")
        rep = json.loads((out / "appstore-audit-report.json").read_text())
        md = (out / "appstore-audit-report.md").read_text()
        self.assertIn("FINAL STATUS: BLOCKED", md)
        self.assertIn("APP STORE REVIEW GUARDIAN", md)
        self.assertIn("não é garantia", rep["disclaimer"])
        self.assertIn("UNSIGNED", rep["target"]["ipa"])
        for k in ("technical", "apple_guidelines", "submission_preparation"):
            self.assertIn(k, rep["readiness"])
        self.assertIn("probabilidade", rep["readiness"]["note"])
        if HAVE_JSONSCHEMA:
            self.assertEqual(generate_report.validate_report(rep), [])
            self.assertEqual(s["schema_errors"], [])
        for f in rep["findings"]:
            if f["severity"] == PASS:
                self.assertTrue(f["evidence"].strip())
            if f["verification_method"] == "heuristic":
                self.assertNotIn(f["severity"], (BLOCKER, CRITICAL))

    def test_schema_rejects_bad_report(self):
        if not HAVE_JSONSCHEMA:
            self.skipTest("jsonschema indisponível")
        proj = fx.flutter_project(self.tmp)
        out = self.tmp / "out"
        self.run_cli("audit", "--quick", str(proj), "--output-dir", str(out))
        rep = json.loads((out / "appstore-audit-report.json").read_text())
        rep["findings"][0]["severity"] = "MAYBE"
        self.assertTrue(generate_report.validate_report(rep))
        rep["findings"][0]["severity"] = "BLOCKER"
        rep["findings"][0]["verification_method"] = "heuristic"
        self.assertTrue(generate_report.validate_report(rep))

    def test_validation_optional_without_jsonschema(self):
        with mock.patch.dict(sys.modules, {"jsonschema": None}):
            self.assertIsNone(generate_report.validate_report({}))

    def test_ready_ipa_only_with_complete_artifact(self):
        ipa = fx.zip_app(fx.build_app(self.tmp / "b", signed=True, profile=fx.profile_bytes()), self.tmp / "s.ipa")
        out = self.tmp / "out"
        rc, stdout = self.run_cli("audit", "--full", "--ipa", str(ipa), "--output-dir", str(out), "--json")
        s = json.loads(stdout)
        # Without codesign, crypto verification is NOT_VERIFIED => cannot be READY
        self.assertEqual(s["final_status"], "NEEDS_REVIEW")
        self.assertEqual(rc, 1)
        rep = json.loads((out / "appstore-audit-report.json").read_text())
        self.assertTrue(any(p["rule_id"] == "SIGN-002" for p in rep["pending"]))
        self.assertTrue(any(f["rule_id"] == "TOOLS-001" for f in rep["findings"]))

    def test_gate_without_ipa_needs_review(self):
        proj = fx.flutter_project(self.tmp, apple_login=True, deletion=True, privacy_manifest_file=True, aps="production",
                                  extra_info={"NSPhotoLibraryUsageDescription": "Usamos suas fotos para a foto de perfil.",
                                              "ITSAppUsesNonExemptEncryption": False})
        out = self.tmp / "out"
        rc, stdout = self.run_cli("gate", "--release", str(proj), "--output-dir", str(out), "--json")
        s = json.loads(stdout)
        self.assertEqual(s["final_status"], "NEEDS_REVIEW")
        self.assertTrue(any("ipa" in r for r in s["gate_reasons"]))
        self.assertEqual(rc, 1)

    def test_incremental_cache(self):
        proj = fx.flutter_project(self.tmp)
        out = self.tmp / "out"
        self.run_cli("audit", "--full", str(proj), "--output-dir", str(out))
        _, s2 = self.run_cli("audit", "--full", str(proj), "--output-dir", str(out), "--json")
        self.assertEqual(json.loads(s2)["cache"], {"hits": 1, "misses": 0})
        (proj / "lib" / "auth" / "auth.dart").write_text("// changed\n", encoding="utf-8")
        _, s3 = self.run_cli("audit", "--full", str(proj), "--output-dir", str(out), "--json")
        self.assertEqual(json.loads(s3)["cache"]["misses"], 1)
        rep = json.loads((out / "appstore-audit-report.json").read_text())
        self.assertIn("lib/auth/auth.dart", rep["cache"]["changed_inputs"])
        self.assertFalse((proj / ".guardian-cache.json").exists())

    def test_policy_mode_filters_technical(self):
        proj = fx.flutter_project(self.tmp)
        out = self.tmp / "out"
        self.run_cli("audit", "--policy", str(proj), "--output-dir", str(out))
        rep = json.loads((out / "appstore-audit-report.json").read_text())
        self.assertFalse([f for f in rep["findings"] if f["area"] == "technical"])

    def test_usage_errors(self):
        self.assertEqual(self.run_cli("audit")[0], 3)
        self.assertEqual(self.run_cli("bogus")[0], 3)
        self.assertEqual(self.run_cli("audit", "--full", str(self.tmp / "missing"))[0], 3)
        proj = fx.flutter_project(self.tmp)
        self.assertEqual(self.run_cli("audit", "--full", str(proj), "--output-dir", str(proj / "reports"))[0], 3)
        self.assertFalse((proj / "reports").exists())

    def test_asc_template(self):
        rc, out = self.run_cli("asc", "--template")
        self.assertEqual(rc, 0)
        self.assertIn("privacy_policy_url", json.loads(out))

    def test_markdown_render_sections(self):
        proj = fx.flutter_project(self.tmp)
        out = self.tmp / "out"
        self.run_cli("audit", "--full", str(proj), "--output-dir", str(out))
        md = (out / "appstore-audit-report.md").read_text()
        for h in ("## Indicadores de prontidão", "## Alertas (WARNING)", "## Pendências (NOT_VERIFIED)", "Referência oficial:"):
            self.assertIn(h, md)


if __name__ == "__main__":
    unittest.main()
