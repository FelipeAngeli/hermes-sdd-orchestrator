"""Artifact parsing: AAB proto manifest, APK AXML, invalid/malicious archives."""
from __future__ import annotations

import os
import stat
import zipfile

from helpers import F, RULES, TempDirCase, by_rule, no_tools, sev  # noqa: F401
import common
import fixtures as fx
import inspect_aab
import inspect_apk
import validate_manifest


class TestProtoManifest(TempDirCase):
    def test_decode_proto_manifest_fields(self):
        tree = fx.manifest_tree(permissions=["android.permission.CAMERA"], debuggable=False,
                                components=[fx.fgs_service()])
        info = validate_manifest.extract_info(inspect_aab.parse_proto_xml(fx.encode_proto_manifest(tree)))
        self.assertEqual(info["package"], "com.example.app")
        self.assertEqual(info["version_code"], 45)
        self.assertEqual(info["version_name"], "2.1.0")
        self.assertEqual((info["min_sdk"], info["target_sdk"]), (24, 36))
        self.assertEqual([p["name"] for p in info["permissions"]], ["android.permission.CAMERA"])
        self.assertIs(info["application"]["debuggable"], False)
        self.assertEqual(info["components"][0]["fgs_types"], ["mediaPlayback"])
        self.assertIs(info["components"][0]["exported"], False)

    def test_truncated_proto_raises(self):
        raw = fx.encode_proto_manifest(fx.manifest_tree())
        with self.assertRaises(inspect_aab.ProtoError):
            inspect_aab.parse_proto_xml(raw[: len(raw) // 2])

    def test_valid_aab(self):
        aab = fx.make_aab(self.tmp / "ok.aab", libs={"arm64-v8a/libx.so": fx.make_elf()})
        res = inspect_aab.inspect_aab(aab, no_tools(), F)
        self.assertEqual(res["manifest"]["target_sdk"], 36)
        self.assertEqual(res["modules"], ["base"])
        self.assertIn(common.PASS, sev(res["findings"], "TECH-ARTIFACT-INTEGRITY"))
        self.assertIn(common.PASS, sev(res["findings"], "TECH-AAB-REQUIRED"))
        # zip-valid is never "approved": bundletool not run -> NOT_VERIFIED, not PASS
        self.assertEqual(sev(res["findings"], "TECH-BUNDLETOOL-VALIDATE"), {common.NOT_VERIFIED})
        self.assertEqual(res["abis"], ["arm64-v8a"])

    def test_aab_missing_bundle_config_is_blocker(self):
        aab = fx.make_aab(self.tmp / "bad.aab", skip_bundle_config=True)
        res = inspect_aab.inspect_aab(aab, no_tools(), F)
        self.assertIn(common.BLOCKER, sev(res["findings"], "TECH-ARTIFACT-INTEGRITY"))
        self.assertIsNone(res["manifest"])

    def test_aab_garbage_manifest(self):
        aab = self.tmp / "garbage.aab"
        with zipfile.ZipFile(aab, "w") as z:
            z.writestr("BundleConfig.pb", b"")
            z.writestr("base/manifest/AndroidManifest.xml", b"\xff\xff\xff\xff\x0f")
        res = inspect_aab.inspect_aab(aab, no_tools(), F)
        self.assertIn(common.BLOCKER, sev(res["findings"], "TECH-ARTIFACT-INTEGRITY"))

    def test_not_a_zip(self):
        p = self.tmp / "x.aab"
        p.write_bytes(b"this is not a zip")
        res = inspect_aab.inspect_aab(p, no_tools(), F)
        self.assertEqual(sev(res["findings"], "TECH-ARTIFACT-INTEGRITY"), {common.BLOCKER})

    def test_billing_version_read(self):
        aab = fx.make_aab(self.tmp / "b.aab", billing="6.2.1")
        res = inspect_aab.inspect_aab(aab, no_tools(), F)
        self.assertEqual(res["billing_library_version"], "6.2.1")


class TestAXML(TempDirCase):
    def test_decode_axml(self):
        tree = fx.manifest_tree(permissions=["android.permission.READ_SMS"], debuggable=True, cleartext=True,
                                version_code=7, target_sdk=34)
        info = validate_manifest.extract_info(inspect_apk.parse_axml(fx.encode_axml(tree)))
        self.assertEqual(info["package"], "com.example.app")
        self.assertEqual(info["version_code"], 7)
        self.assertEqual(info["target_sdk"], 34)
        self.assertIs(info["application"]["debuggable"], True)
        self.assertIs(info["application"]["uses_cleartext_traffic"], True)
        self.assertEqual(info["permissions"][0]["name"], "android.permission.READ_SMS")

    def test_deep_links_and_exported(self):
        comps = [("activity", {"android:name": ".Link", "android:exported": True}, [
            ("intent-filter", {"android:autoVerify": True}, [
                ("action", {"android:name": "android.intent.action.VIEW"}, []),
                ("category", {"android:name": "android.intent.category.BROWSABLE"}, []),
                ("data", {"android:scheme": "https", "android:host": "example.com"}, [])]),
            ("intent-filter", {}, [
                ("action", {"android:name": "android.intent.action.VIEW"}, []),
                ("data", {"android:scheme": "myapp"}, [])])]),
            ("receiver", {"android:name": ".NoExported"}, [
                ("intent-filter", {}, [("action", {"android:name": "x.Y"}, [])])])]
        info = validate_manifest.extract_info(inspect_apk.parse_axml(fx.encode_axml(fx.manifest_tree(components=comps))))
        links = validate_manifest.deep_links(info)
        self.assertEqual({l["scheme"] for l in links}, {"https", "myapp"})
        fs = validate_manifest.check_manifest(info, F, source="t", artifact=True)
        self.assertIn(common.BLOCKER, sev(fs, "TECH-EXPORTED-EXPLICIT"))
        self.assertIn(common.NOT_VERIFIED, sev(fs, "SEC-APP-LINKS"))
        self.assertIn(common.INFO, sev(fs, "SEC-CUSTOM-SCHEME"))

    def test_invalid_axml(self):
        with self.assertRaises(inspect_apk.AXMLError):
            inspect_apk.parse_axml(b"\x01\x02\x03\x04\x05\x06\x07\x08\x09")

    def test_valid_apk(self):
        apk = fx.make_apk(self.tmp / "ok.apk")
        res = inspect_apk.inspect_apk(apk, no_tools(), F)
        self.assertEqual(res["manifest"]["version_name"], "2.1.0")
        self.assertIn(common.PASS, sev(res["findings"], "TECH-ARTIFACT-INTEGRITY"))
        self.assertIn(common.INFO, sev(res["findings"], "TECH-AAB-REQUIRED"))  # APK never replaces AAB

    def test_debuggable_apk_blocker(self):
        apk = fx.make_apk(self.tmp / "dbg.apk", fx.manifest_tree(debuggable=True))
        res = inspect_apk.inspect_apk(apk, no_tools(), F)
        fs = validate_manifest.check_manifest(res["manifest"], F, source="dbg", artifact=True)
        self.assertEqual(sev(fs, "TECH-DEBUGGABLE"), {common.BLOCKER})

    def test_invalid_package_and_version_code(self):
        info = validate_manifest.extract_info(inspect_apk.parse_axml(fx.encode_axml(
            fx.manifest_tree(package="nodots", version_code=2100000001))))
        fs = validate_manifest.check_manifest(info, F, source="x", artifact=True)
        self.assertEqual(sev(fs, "TECH-PACKAGE-NAME"), {common.BLOCKER})
        self.assertEqual(sev(fs, "TECH-VERSION-CODE"), {common.BLOCKER})


class TestMaliciousArchives(TempDirCase):
    def _zip(self, name, entries):
        p = self.tmp / name
        with zipfile.ZipFile(p, "w") as z:
            for e in entries:
                if isinstance(e, zipfile.ZipInfo):
                    z.writestr(e, b"x")
                else:
                    z.writestr(e, b"x")
        return p

    def test_zip_slip_and_absolute(self):
        p = self._zip("slip.apk", ["AndroidManifest.xml", "../../evil.so", "/etc/passwd"])
        scan = common.scan_zip(p)
        kinds = {i.kind for i in scan.issues}
        self.assertIn("zip_slip", kinds)
        self.assertIn("absolute_path", kinds)
        res = inspect_apk.inspect_apk(p, no_tools(), F)
        self.assertEqual(sev(res["findings"], "SEC-ARCHIVE-SAFETY"), {common.CRITICAL})

    def test_symlink_entry(self):
        info = zipfile.ZipInfo("lib/arm64-v8a/link.so")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        p = self._zip("sym.apk", ["AndroidManifest.xml", info])
        self.assertIn("symlink", {i.kind for i in common.scan_zip(p).issues})
        with zipfile.ZipFile(p) as z, common.SecureTempDir() as d:
            with self.assertRaises(ValueError):
                common.extract_member_safe(z, z.getinfo("lib/arm64-v8a/link.so"), d, 0)

    def test_zip_bomb_ratio(self):
        p = self.tmp / "bomb.aab"
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            z.writestr("BundleConfig.pb", b"")
            z.writestr("base/assets/zeros.bin", b"\x00" * (12 * 1024 * 1024))
        scan = common.scan_zip(p)
        self.assertIn("zip_bomb", {i.kind for i in scan.issues})
        self.assertFalse(scan.crc_checked)  # never decompresses suspected bombs
        res = inspect_aab.inspect_aab(p, no_tools(), F)
        self.assertEqual(sev(res["findings"], "SEC-ARCHIVE-SAFETY"), {common.CRITICAL})
        self.assertIsNone(res["manifest"])

    def test_corrupt_zip_crc(self):
        p = fx.make_apk(self.tmp / "c.apk")
        data = bytearray(p.read_bytes())
        idx = data.find(b"AndroidManifest.xml") + len("AndroidManifest.xml") + 40
        data[idx] ^= 0xFF
        p.write_bytes(bytes(data))
        res = inspect_apk.inspect_apk(p, no_tools(), F)
        self.assertIn(common.BLOCKER, sev(res["findings"], "TECH-ARTIFACT-INTEGRITY"))

    def test_secure_temp_dir_permissions_and_cleanup(self):
        with common.SecureTempDir() as d:
            self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
            path = d
        self.assertFalse(path.exists())
