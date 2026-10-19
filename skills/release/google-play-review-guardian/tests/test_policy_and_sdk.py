"""Permissions, target SDK (rule-data driven), severity semantics, testing requirement."""
from __future__ import annotations

import copy
import datetime as dt

from helpers import F, RULES, TempDirCase, by_rule, sev
import common
import fixtures as fx
import inspect_apk
import validate_manifest
import validate_permissions
import validate_play_console
import validate_sdk

A = "android.permission."


def info_for(perms=(), components=None, target=36):
    tree = fx.manifest_tree(permissions=perms, components=components, target_sdk=target)
    return validate_manifest.extract_info(inspect_apk.parse_axml(fx.encode_axml(tree)))


class TestPermissions(TempDirCase):
    def run_perm(self, perms=(), components=None, target=36, declarations=None):
        return validate_permissions.validate_permissions(info_for(perms, components, target), RULES, F,
                                                         component="t.aab", artifact=True,
                                                         declarations=declarations)["findings"]

    def test_restricted_permissions_are_warning_not_blocker(self):
        cases = {
            "POL-BACKGROUND-LOCATION": [A + "ACCESS_BACKGROUND_LOCATION"],
            "POL-SMS-CALL-LOG": [A + "READ_SMS"],
            "POL-ALL-FILES-ACCESS": [A + "MANAGE_EXTERNAL_STORAGE"],
            "POL-QUERY-ALL-PACKAGES": [A + "QUERY_ALL_PACKAGES"],
            "POL-REQUEST-INSTALL-PACKAGES": [A + "REQUEST_INSTALL_PACKAGES"],
            "POL-PHOTO-VIDEO": [A + "READ_MEDIA_IMAGES"],
        }
        for rid, perms in cases.items():
            with self.subTest(rid=rid):
                fs = self.run_perm(perms)
                self.assertEqual(sev(fs, rid), {common.WARNING})
                self.assertFalse(any(f.severity in (common.BLOCKER, common.CRITICAL) for f in fs))

    def test_call_log(self):
        self.assertEqual(sev(self.run_perm([A + "READ_CALL_LOG"]), "POL-SMS-CALL-LOG"), {common.WARNING})

    def test_exact_alarm(self):
        self.assertEqual(sev(self.run_perm([A + "USE_EXACT_ALARM"]), "POL-EXACT-ALARM"), {common.WARNING})
        self.assertEqual(sev(self.run_perm([A + "SCHEDULE_EXACT_ALARM"]), "POL-EXACT-ALARM"), {common.INFO})

    def test_foreground_service_type_missing_permission_is_blocker(self):
        fs = self.run_perm([A + "FOREGROUND_SERVICE"], components=[fx.fgs_service()])
        self.assertIn(common.BLOCKER, sev(fs, "POL-FOREGROUND-SERVICE"))

    def test_foreground_service_complete_needs_declaration(self):
        fs = self.run_perm([A + "FOREGROUND_SERVICE", A + "FOREGROUND_SERVICE_MEDIA_PLAYBACK"], components=[fx.fgs_service()])
        self.assertEqual(sev(fs, "POL-FOREGROUND-SERVICE"), {common.WARNING})
        self.assertTrue(any("mediaPlayback" in f.evidence for f in by_rule(fs, "POL-FOREGROUND-SERVICE")))

    def test_accessibility_service(self):
        comp = [("service", {"android:name": ".Acc", "android:exported": True, "android:permission": A + "BIND_ACCESSIBILITY_SERVICE"}, [
            ("intent-filter", {}, [("action", {"android:name": "android.accessibilityservice.AccessibilityService"}, [])])])]
        self.assertEqual(sev(self.run_perm(components=comp), "POL-ACCESSIBILITY-API"), {common.WARNING})

    def test_target_below_30_query_all_packages_is_info(self):
        fs = self.run_perm([A + "QUERY_ALL_PACKAGES"], target=29)
        self.assertEqual(sev(fs, "POL-QUERY-ALL-PACKAGES"), {common.INFO})

    def test_user_declaration_downgrades(self):
        fs = self.run_perm([A + "ACCESS_BACKGROUND_LOCATION"],
                           declarations={"permissions_declared": [A + "ACCESS_BACKGROUND_LOCATION"]})
        self.assertEqual(sev(fs, "POL-BACKGROUND-LOCATION"), {common.INFO})

    def test_clean_manifest_pass(self):
        fs = self.run_perm([A + "INTERNET"])
        self.assertIn(common.PASS, sev(fs, "POL-PERMISSIONS-DECLARATION"))

    def test_missing_manifest_not_verified(self):
        fs = validate_permissions.validate_permissions(None, RULES, F, component="x", artifact=False)["findings"]
        self.assertEqual(sev(fs, "POL-PERMISSIONS-DECLARATION"), {common.NOT_VERIFIED})


class TestTargetSdk(TempDirCase):
    def ts(self, target, **kw):
        kw.setdefault("as_of", dt.date(2026, 10, 16))
        return validate_sdk.validate_target_sdk(target, rules=RULES, F=F, component="c", **kw)

    def test_phone_below_requirement_blocker(self):
        fs = self.ts(35)
        self.assertEqual(sev(fs, "TECH-TARGET-API-LEVEL") - {common.INFO}, {common.BLOCKER})
        self.assertTrue(any("mínimo=36" in f.evidence for f in fs))

    def test_phone_meets_requirement(self):
        self.assertEqual(sev(self.ts(36), "TECH-TARGET-API-LEVEL"), {common.PASS})

    def test_before_deadline_uses_previous_requirement(self):
        fs = self.ts(35, as_of=dt.date(2026, 8, 30))
        self.assertIn(common.PASS, sev(fs, "TECH-TARGET-API-LEVEL"))
        self.assertTrue(any(f.severity == common.INFO and "36" in f.title for f in fs))  # next requirement

    def test_form_factors_use_rule_data(self):
        self.assertEqual(sev(self.ts(35, form_factor="wear"), "TECH-TARGET-API-LEVEL"), {common.PASS})
        self.assertEqual(sev(self.ts(34, form_factor="tv"), "TECH-TARGET-API-LEVEL"), {common.PASS})
        self.assertEqual(sev(self.ts(34, form_factor="xr"), "TECH-TARGET-API-LEVEL"), {common.PASS})
        self.assertEqual(sev(self.ts(34, form_factor="automotive"), "TECH-TARGET-API-LEVEL"), {common.BLOCKER})

    def test_extension_granted_downgrades_until_deadline(self):
        fs = self.ts(35, extension_granted=True)
        self.assertEqual(sev(fs, "TECH-TARGET-API-LEVEL"), {common.WARNING})
        fs = self.ts(35, extension_granted=True, as_of=dt.date(2026, 11, 2))
        self.assertEqual(sev(fs, "TECH-TARGET-API-LEVEL"), {common.BLOCKER})

    def test_existing_app_availability_is_warning(self):
        fs = self.ts(34, submission="existing_app")
        self.assertEqual(sev(fs, "TECH-TARGET-API-AVAILABILITY"), {common.WARNING})

    def test_unknown_target_not_verified(self):
        self.assertEqual(sev(self.ts(None), "TECH-TARGET-API-LEVEL"), {common.NOT_VERIFIED})

    def test_requirement_is_data_not_constant(self):
        rules = copy.deepcopy(RULES)
        for r in rules["TECH-TARGET-API-LEVEL"]["requirements"]:
            if r["form_factor"] == "phone/tablet" and r["min_target_api"] == 36:
                r["min_target_api"] = 40
        fs = validate_sdk.validate_target_sdk(36, rules=rules, F=common.FindingFactory(rules), component="c",
                                              as_of=dt.date(2026, 10, 16))
        self.assertEqual(sev(fs, "TECH-TARGET-API-LEVEL"), {common.BLOCKER})

    def test_heuristic_source_never_blocker(self):
        fs = self.ts(30, method="heuristic", confidence="medium")
        self.assertEqual(sev(fs, "TECH-TARGET-API-LEVEL"), {common.WARNING})

    def test_form_factor_detection(self):
        info = info_for()
        self.assertEqual(validate_sdk.detect_form_factor(info)[0], "phone/tablet")
        tree = fx.manifest_tree(features=["android.hardware.type.watch"])
        info = validate_manifest.extract_info(inspect_apk.parse_axml(fx.encode_axml(tree)))
        self.assertEqual(validate_sdk.detect_form_factor(info)[0], "wear")


class TestSeverityAndGate(TempDirCase):
    def test_blocker_downgraded_without_confirmed_evidence(self):
        f = F("TECH-DEBUGGABLE", common.BLOCKER, "android_build", "t", method="manual")
        self.assertEqual(f.severity, common.WARNING)
        f = F("TECH-DEBUGGABLE", common.CRITICAL, "android_build", "t", confidence="low")
        self.assertEqual(f.severity, common.WARNING)

    def test_final_status(self):
        P = F("TECH-DEBUGGABLE", common.PASS, "android_build", "ok")
        B = F("TECH-DEBUGGABLE", common.BLOCKER, "android_build", "bad")
        W = F("SEC-CLEARTEXT", common.WARNING, "security", "w")
        NVm = F("PRIV-DATA-SAFETY-FORM", common.NOT_VERIFIED, "data_safety", "nv", method="manual")
        NVr = F("POL-SDK-INDEX", common.NOT_VERIFIED, "data_safety", "nv", method="manual")
        self.assertEqual(common.final_status([P, B]), common.BLOCKED)
        self.assertEqual(common.final_status([P, W]), common.NEEDS_REVIEW)
        self.assertEqual(common.final_status([P, NVm]), common.NEEDS_REVIEW)  # pending mandatory check
        self.assertEqual(common.final_status([P, NVr]), common.READY)
        self.assertEqual(common.final_status([]), common.NEEDS_REVIEW)  # nothing ran -> never READY

    def test_section_status(self):
        self.assertEqual(common.section_status([]), common.NOT_VERIFIED)
        na = F("TECH-16KB-PAGE-SIZE", common.NOT_APPLICABLE, "page_size_16kb", "na")
        self.assertEqual(common.section_status([na]), common.NOT_APPLICABLE)
        guessed = F("POL-PLAY-BILLING", common.NOT_APPLICABLE, "billing", "na", method="heuristic", confidence="medium")
        self.assertEqual(common.section_status([guessed]), common.NOT_VERIFIED)

    def test_unknown_rule_rejected(self):
        with self.assertRaises(KeyError):
            F("NOPE", common.PASS, "android_build", "x")

    def test_rules_have_required_fields_and_sources(self):
        for rid, r in RULES.items():
            for k in common.RULE_REQUIRED_FIELDS:
                self.assertIn(k, r, rid)
            self.assertTrue(r["official_url"].startswith("https://"), rid)
            self.assertIn(r["verification_method"], common.METHODS)
            self.assertIn(r["requirement_level"], ("mandatory", "recommended"))
            self.assertIsNotNone(common.parse_date(r["consulted_at"]))
            self.assertTrue(set(r["platforms_affected"]) <= {"phone/tablet", "wear", "tv", "automotive", "xr"})


class TestClosedTesting(TempDirCase):
    def test_missing_account_type_not_verified(self):
        self.assertEqual(sev(validate_play_console.validate_testing(None, F), "POL-CLOSED-TESTING"), {common.NOT_VERIFIED})

    def test_organization_not_applicable(self):
        fs = validate_play_console.validate_testing({"type": "organization", "created": "2024-05-01"}, F)
        # A user-declared account type is not verified evidence: the rule stays pending until confirmed.
        self.assertEqual(sev(fs, "POL-CLOSED-TESTING"), {common.NOT_VERIFIED})
        self.assertIn("organizacional", by_rule(fs, "POL-CLOSED-TESTING")[0].title.lower())

    def test_old_personal_not_applicable(self):
        fs = validate_play_console.validate_testing({"type": "personal", "created": "2023-11-01"}, F)
        self.assertEqual(sev(fs, "POL-CLOSED-TESTING"), {common.NOT_VERIFIED})

    def test_new_personal_requirement(self):
        fs = validate_play_console.validate_testing({"type": "personal", "created": "2024-01-10"}, F)
        self.assertEqual(sev(fs, "POL-CLOSED-TESTING"), {common.WARNING})
        fs = validate_play_console.validate_testing({"type": "personal", "created": "2024-01-10",
                                                     "closed_test_testers": 12, "closed_test_days": 14}, F)
        # user-declared numbers are not a verification: mandatory rule stays pending, never PASS
        self.assertEqual(sev(fs, "POL-CLOSED-TESTING"), {common.NOT_VERIFIED})
        fs = validate_play_console.validate_testing({"type": "personal", "created": "2024-01-10",
                                                     "closed_test_testers": 11, "closed_test_days": 20}, F)
        self.assertEqual(sev(fs, "POL-CLOSED-TESTING"), {common.WARNING})

    def test_store_listing_limits(self):
        fs = validate_play_console.validate_store_listing({"title": "x" * 31, "short_description": "ok", "full_description": "ok"}, RULES, F)
        self.assertIn(common.WARNING, sev(fs, "POL-STORE-LISTING"))
        fs = validate_play_console.validate_store_listing(None, RULES, F)
        self.assertEqual(sev(fs, "POL-STORE-LISTING"), {common.NOT_VERIFIED})
