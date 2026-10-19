"""Native libraries (16 KB), signing, external tool failures, SDK discovery."""
from __future__ import annotations

import os
import subprocess
import zipfile
from pathlib import Path
from unittest import mock

from helpers import F, RULES, TempDirCase, by_rule, no_tools, sev
import common
import fixtures as fx
import validate_native_libraries as vnl
import validate_signing


class TestELF(TempDirCase):
    def test_elf64_aligned(self):
        seg = vnl.parse_elf_segments(fx.make_elf(0x4000))
        self.assertEqual(seg["class"], 64)
        self.assertEqual(seg["machine"], "aarch64")
        self.assertTrue(vnl.elf_16kb_ok(seg)[0])

    def test_elf64_misaligned(self):
        ok, why = vnl.elf_16kb_ok(vnl.parse_elf_segments(fx.make_elf(0x1000)))
        self.assertFalse(ok)
        self.assertIn("0x1000", why)

    def test_elf32(self):
        seg = vnl.parse_elf_segments(fx.make_elf(0x1000, bits=32, machine=40))
        self.assertEqual((seg["class"], seg["machine"]), (32, "arm"))

    def test_not_elf(self):
        with self.assertRaises(vnl.ELFError):
            vnl.parse_elf_segments(b"MZ" + b"\x00" * 100)

    def test_relro_check(self):
        # RELRO ends at 0x11000 inside RW LOAD [0x10000, 0x12000) -> writable data shares the 16 KB page
        bad = vnl.parse_elf_segments(fx.make_elf(0x4000, relro=(0x10000, 0x1000)))
        self.assertFalse(vnl.relro_16kb_ok(bad)[0])
        good = vnl.parse_elf_segments(fx.make_elf(0x4000, relro=(0x10000, 0x4000)))
        self.assertTrue(vnl.relro_16kb_ok(good)[0])
        own = vnl.parse_elf_segments(fx.make_elf(0x4000, relro=(0x10000, 0x2000)))  # equals the RW LOAD
        self.assertTrue(vnl.relro_16kb_ok(own)[0])

    def test_readelf_output_parser(self):
        txt = ("  Type Offset VirtAddr PhysAddr FileSiz MemSiz Flg Align\n"
               "  LOAD 0x000000 0x0 0x0 0x680 0x680 R E 0x4000\n  LOAD 0x680 0x4680 0x4680 0x1d8 0x980 RW 0x1000\n")
        self.assertEqual(vnl.parse_readelf_loads(txt), [0x4000, 0x1000])


class TestNativeArtifacts(TempDirCase):
    def test_apk_aligned_pass(self):
        apk = fx.make_apk(self.tmp / "a.apk", libs={"arm64-v8a/liba.so": fx.make_elf(0x4000),
                                                    "armeabi-v7a/liba.so": fx.make_elf(0x1000, bits=32, machine=40)})
        res = vnl.validate_native_libraries(apk, "apk", no_tools(), F, 36, use_tools=False)
        self.assertEqual(sev(res["findings"], "TECH-16KB-PAGE-SIZE"), {common.PASS})  # 32-bit libs are out of scope
        self.assertEqual(sev(res["findings"], "TECH-16KB-ZIP-ALIGN"), {common.PASS})
        self.assertEqual(sev(res["findings"], "TECH-64BIT"), {common.PASS})

    def test_apk_misaligned_elf_and_zip(self):
        apk = fx.make_apk(self.tmp / "b.apk", libs={"arm64-v8a/libb.so": fx.make_elf(0x1000)}, align_libs=None)
        res = vnl.validate_native_libraries(apk, "apk", no_tools(), F, 36, use_tools=False)
        self.assertIn(common.BLOCKER, sev(res["findings"], "TECH-16KB-PAGE-SIZE"))
        self.assertIn(common.BLOCKER, sev(res["findings"], "TECH-16KB-ZIP-ALIGN"))
        lib = res["libs"][0]
        self.assertFalse(lib["zip_16kb"])
        with zipfile.ZipFile(apk) as z:
            self.assertNotEqual(common.local_data_offset(apk, z.getinfo("lib/arm64-v8a/libb.so")) % 16384, 0)

    def test_target_below_35_misaligned_is_warning(self):
        apk = fx.make_apk(self.tmp / "c.apk", libs={"arm64-v8a/libc.so": fx.make_elf(0x1000)})
        res = vnl.validate_native_libraries(apk, "apk", no_tools(), F, 34, use_tools=False)
        self.assertEqual(sev(res["findings"], "TECH-16KB-PAGE-SIZE"), {common.WARNING})

    def test_only_32bit_is_blocker(self):
        apk = fx.make_apk(self.tmp / "d.apk", libs={"armeabi-v7a/libd.so": fx.make_elf(0x1000, bits=32, machine=40)})
        res = vnl.validate_native_libraries(apk, "apk", no_tools(), F, 36, use_tools=False)
        self.assertEqual(sev(res["findings"], "TECH-64BIT"), {common.BLOCKER})

    def test_no_native_libs_not_applicable(self):
        apk = fx.make_apk(self.tmp / "e.apk")
        res = vnl.validate_native_libraries(apk, "apk", no_tools(), F, 36, use_tools=False)
        self.assertEqual(sev(res["findings"], "TECH-16KB-PAGE-SIZE"), {common.NOT_APPLICABLE})

    def test_aab_zip_alignment_not_verified_never_pass(self):
        aab = fx.make_aab(self.tmp / "f.aab", libs={"arm64-v8a/libf.so": fx.make_elf(0x4000)})
        res = vnl.validate_native_libraries(aab, "aab", no_tools(), F, 36, use_tools=False)
        self.assertEqual(sev(res["findings"], "TECH-16KB-ZIP-ALIGN"), {common.NOT_VERIFIED})
        self.assertEqual(sev(res["findings"], "TECH-16KB-PAGE-SIZE"), {common.PASS})

    def test_corrupt_so_not_verified(self):
        apk = fx.make_apk(self.tmp / "g.apk", libs={"arm64-v8a/libg.so": b"\x7fELF\x02\x01" + b"\x00" * 20})
        res = vnl.validate_native_libraries(apk, "apk", no_tools(), F, 36, use_tools=False)
        self.assertIn(common.NOT_VERIFIED, sev(res["findings"], "TECH-16KB-PAGE-SIZE"))


class TestSigning(TempDirCase):
    def test_debug_cert_detected_v1(self):
        apk = fx.make_apk(self.tmp / "dbg.apk", signer_cn="Android Debug")
        res = validate_signing.validate_signing(apk, "apk", no_tools(), F)
        self.assertEqual(sev(res["findings"], "TECH-DEBUG-SIGNATURE"), {common.BLOCKER})
        self.assertEqual(res["signatures"]["certificates"][0]["subject"]["CN"], "Android Debug")

    def test_release_cert_and_apksigner_missing(self):
        apk = fx.make_apk(self.tmp / "rel.apk", signer_cn="Release Key")
        res = validate_signing.validate_signing(apk, "apk", no_tools(), F)
        self.assertEqual(sev(res["findings"], "TECH-DEBUG-SIGNATURE"), {common.PASS})
        self.assertEqual(sev(res["findings"], "TECH-SIGNATURE-VERIFY"), {common.NOT_VERIFIED})
        nv = by_rule(res["findings"], "TECH-SIGNATURE-VERIFY")[0]
        self.assertIn("apksigner indisponível", nv.evidence)

    def test_unsigned_aab_blocker(self):
        aab = fx.make_aab(self.tmp / "u.aab", signer_cn=None)
        res = validate_signing.validate_signing(aab, "aab", no_tools(), F)
        self.assertEqual(sev(res["findings"], "TECH-DEBUG-SIGNATURE"), {common.BLOCKER})

    def test_upload_key_size(self):
        aab = fx.make_aab(self.tmp / "k.aab")
        res = validate_signing.validate_signing(aab, "aab", no_tools(), F)
        self.assertEqual(res["signatures"]["certificates"][0]["key_bits"], 2048)
        self.assertEqual(sev(res["findings"], "TECH-UPLOAD-KEY"), {common.PASS})
        self.assertEqual(sev(res["findings"], "TECH-PLAY-APP-SIGNING"), {common.NOT_VERIFIED})

    def _jdk(self, with_jarsigner: bool) -> dict:
        home = self.tmp / ("jdk" if with_jarsigner else "jre")
        (home / "bin").mkdir(parents=True)
        (home / "bin" / "java").write_text("#")
        if with_jarsigner:
            (home / "bin" / "jarsigner").write_text("#")
        tools = no_tools()
        tools["java"] = common.ToolInfo("java", str(home / "bin" / "java"), True, "", java_home=str(home))
        return tools

    def test_aab_signature_verified_with_jdk_jarsigner(self):
        aab = fx.make_aab(self.tmp / "s.aab")
        tools = self._jdk(with_jarsigner=True)
        calls = []

        def fake_run(cmd, timeout=120, env=None):
            calls.append((cmd, env))
            return common.ToolResult(True, 0, "jar verified.\n", "")

        with mock.patch("validate_signing.run_tool", side_effect=fake_run):
            res = validate_signing.validate_signing(aab, "aab", tools, F)
        self.assertEqual(sev(res["findings"], "TECH-SIGNATURE-VERIFY"), {common.PASS})
        cmd, env = calls[0]
        self.assertEqual([str(self.tmp / "jdk" / "bin" / "jarsigner"), "-verify", str(aab)], cmd)
        self.assertEqual(str(self.tmp / "jdk"), env["JAVA_HOME"])

        with mock.patch("validate_signing.run_tool",
                        return_value=common.ToolResult(False, 1, "jar is unsigned.", "", error="nonzero")):
            res = validate_signing.validate_signing(aab, "aab", tools, F)
        self.assertEqual(sev(res["findings"], "TECH-SIGNATURE-VERIFY"), {common.BLOCKER})

        with mock.patch("validate_signing.run_tool",
                        return_value=common.ToolResult(True, 0, "no manifest.\n", "")):
            res = validate_signing.validate_signing(aab, "aab", tools, F)
        self.assertEqual(sev(res["findings"], "TECH-SIGNATURE-VERIFY"), {common.NOT_VERIFIED}, "exit 0 alone is not PASS")

        with mock.patch("validate_signing.run_tool",
                        return_value=common.ToolResult(False, None, error="timeout")):
            res = validate_signing.validate_signing(aab, "aab", tools, F)
        self.assertEqual(sev(res["findings"], "TECH-SIGNATURE-VERIFY"), {common.NOT_VERIFIED})

    def test_aab_signature_stays_not_verified_with_a_jre_only(self):
        aab = fx.make_aab(self.tmp / "j.aab")
        with mock.patch("validate_signing.run_tool") as run:
            res = validate_signing.validate_signing(aab, "aab", self._jdk(with_jarsigner=False), F)
        run.assert_not_called()
        self.assertEqual(sev(res["findings"], "TECH-SIGNATURE-VERIFY"), {common.NOT_VERIFIED})
        self.assertIn("jarsigner indisponível", by_rule(res["findings"], "TECH-SIGNATURE-VERIFY")[0].evidence)

    def test_project_hygiene_never_reads_secrets(self):
        fs = validate_signing.project_signing_hygiene(["android/key.properties"], {"android/key.properties": {"in_repo": True, "tracked": True}}, F)
        self.assertEqual(fs[0].severity, common.CRITICAL)
        fs = validate_signing.project_signing_hygiene(["android/key.properties"], {"android/key.properties": {"in_repo": True, "ignored": True}}, F)
        self.assertEqual(fs[0].severity, common.PASS)
        fs = validate_signing.project_signing_hygiene(["android/key.properties"], {}, F)
        self.assertEqual(fs[0].severity, common.NOT_VERIFIED)


class TestExternalTools(TempDirCase):
    def test_run_tool_timeout(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
            r = common.run_tool(["aapt2", "dump"], timeout=1)
        self.assertEqual(r.error, "timeout")
        self.assertFalse(r.ok)

    def test_run_tool_nonzero(self):
        cp = subprocess.CompletedProcess(["x"], 1, b"", b"boom")
        with mock.patch("subprocess.run", return_value=cp):
            r = common.run_tool(["apksigner", "verify"])
        self.assertEqual((r.error, r.returncode), ("nonzero", 1))
        self.assertIn("boom", r.reason())

    def test_run_tool_missing(self):
        r = common.run_tool(["/definitely/not/here/aapt2"])
        self.assertEqual(r.error, "not_found")

    def _tools(self, **avail):
        t = no_tools()
        for n in avail:
            t[n] = common.ToolInfo(n, f"/fake/{n}", True)
        return t

    def test_apksigner_nonzero_is_blocker_timeout_is_not_verified(self):
        apk = fx.make_apk(self.tmp / "s.apk")
        tools = self._tools(apksigner=True)
        with mock.patch("validate_signing.run_tool", return_value=common.ToolResult(False, 1, "", "DOES NOT VERIFY", "nonzero")):
            fs = validate_signing.validate_signing(apk, "apk", tools, F)["findings"]
        self.assertEqual(sev(fs, "TECH-SIGNATURE-VERIFY"), {common.BLOCKER})
        with mock.patch("validate_signing.run_tool", return_value=common.ToolResult(False, None, error="timeout")):
            fs = validate_signing.validate_signing(apk, "apk", tools, F)["findings"]
        self.assertEqual(sev(fs, "TECH-SIGNATURE-VERIFY"), {common.NOT_VERIFIED})

    def test_zipalign_failure_and_timeout(self):
        apk = fx.make_apk(self.tmp / "z.apk", libs={"arm64-v8a/libz.so": fx.make_elf()})
        tools = self._tools(zipalign=True)
        with mock.patch("validate_native_libraries.run_tool", return_value=common.ToolResult(False, None, error="timeout")):
            fs = vnl.validate_native_libraries(apk, "apk", tools, F, 36)["findings"]
        self.assertIn(common.NOT_VERIFIED, sev(fs, "TECH-16KB-ZIP-ALIGN"))
        self.assertIn(common.PASS, sev(fs, "TECH-16KB-ZIP-ALIGN"))  # python offset check still ran

    def test_aapt2_crash_falls_back_to_python(self):
        import inspect_apk
        apk = fx.make_apk(self.tmp / "a.apk")
        tools = self._tools(aapt2=True)
        with mock.patch("inspect_apk.run_tool", return_value=common.ToolResult(False, 139, "", "segfault", "nonzero")):
            res = inspect_apk.inspect_apk(apk, tools, F)
        self.assertEqual(res["manifest"]["package"], "com.example.app")
        self.assertIn("139", res["verification"]["aapt2"])


class TestDiscovery(TempDirCase):
    def test_no_sdk_and_no_java(self):
        with mock.patch.dict("os.environ", {"ANDROID_HOME": "", "ANDROID_SDK_ROOT": "", "LOCALAPPDATA": "", "JAVA_HOME": ""}), \
             mock.patch("common.Path.home", return_value=self.tmp), \
             mock.patch("common.android_studio_java_homes", return_value=[]), \
             mock.patch("common.shutil.which", return_value=None):
            tools = common.discover_tools()
        self.assertTrue(all(not t.available for t in tools.values()))
        self.assertIn("não encontrado", tools["aapt2"].reason)

    def test_newest_build_tools_and_java_dependency(self):
        sdk = self.tmp / "sdk"
        for v in ("35.0.0", "37.0.0", "36.1.0"):
            d = sdk / "build-tools" / v
            d.mkdir(parents=True)
            for n in ("aapt2", "zipalign", "apksigner"):
                (d / n).write_text("#")
        with mock.patch.dict("os.environ", {"ANDROID_HOME": str(sdk), "JAVA_HOME": ""}), \
             mock.patch("common.Path.home", return_value=self.tmp / "nohome"), \
             mock.patch("common.android_studio_java_homes", return_value=[]), \
             mock.patch("common.shutil.which", return_value=None):
            tools = common.discover_tools()
        self.assertIn("37.0.0", tools["aapt2"].path)
        self.assertTrue(tools["aapt2"].available)
        self.assertFalse(tools["apksigner"].available)  # needs Java
        self.assertIn("Java", tools["apksigner"].reason)

    def _fake_java_home(self, root: Path) -> Path:
        java = root / "bin" / ("java.exe" if os.name == "nt" else "java")
        java.parent.mkdir(parents=True)
        java.write_text("#")
        return root

    def test_java_from_android_studio_jbr_enables_java_tools_and_sdk_bundletool(self):
        sdk = self.tmp / "sdk"
        bt = sdk / "build-tools" / "37.0.0"
        bt.mkdir(parents=True)
        for n in ("aapt2", "zipalign", "apksigner"):
            (bt / n).write_text("#")
        (sdk / "bundletool").mkdir()
        (sdk / "bundletool" / "bundletool-all-1.9.0.jar").write_text("#")
        (sdk / "bundletool" / "bundletool-all-1.18.3.jar").write_text("#")
        jbr = self._fake_java_home(self.tmp / "studio" / "jbr")
        ran = []

        def fake_run(cmd, timeout=120, env=None):
            ran.append((cmd, env))
            return common.ToolResult(True, 0, "", 'openjdk version "21"')

        with mock.patch.dict("os.environ", {"ANDROID_HOME": str(sdk), "JAVA_HOME": "", "BUNDLETOOL_JAR": ""}), \
             mock.patch("common.Path.home", return_value=self.tmp / "nohome"), \
             mock.patch("common.shutil.which", return_value=None), \
             mock.patch("common.android_studio_java_homes", return_value=[jbr]), \
             mock.patch("common.run_tool", side_effect=fake_run):
            tools = common.discover_tools()
        self.assertTrue(tools["java"].available)
        self.assertEqual(str(jbr / "bin" / "java"), tools["java"].path)
        self.assertTrue(tools["apksigner"].available)
        self.assertTrue(tools["bundletool"].available)
        self.assertTrue(tools["bundletool"].path.endswith("bundletool-all-1.18.3.jar"), "newest jar wins")
        # bundletool runs on the discovered Java, not on whatever `java` is on PATH
        self.assertEqual([str(jbr / "bin" / "java"), "-jar", tools["bundletool"].path, "version"],
                         common.tool_cmd(tools["bundletool"], "version"))
        # wrapper scripts (apksigner) get that Java through JAVA_HOME and PATH, nothing else from the environment
        env = common.tool_env(tools["apksigner"])
        self.assertEqual(str(jbr), env["JAVA_HOME"])
        self.assertTrue(env["PATH"].startswith(str(jbr / "bin")))
        self.assertLessEqual(set(env), {"JAVA_HOME", "PATH", "HOME", "LANG", "TMPDIR"})
        self.assertIsNone(common.tool_env(tools["aapt2"]), "native tools keep the default environment")

    def test_java_home_takes_precedence_and_a_broken_java_is_reported(self):
        good = self._fake_java_home(self.tmp / "jdk")

        def fake_run(cmd, timeout=120, env=None):
            return common.ToolResult(cmd[0].startswith(str(good)), 0 if cmd[0].startswith(str(good)) else 1,
                                     "", "", error=None if cmd[0].startswith(str(good)) else "nonzero")

        with mock.patch.dict("os.environ", {"ANDROID_HOME": "", "ANDROID_SDK_ROOT": "", "JAVA_HOME": str(good)}), \
             mock.patch("common.Path.home", return_value=self.tmp / "nohome"), \
             mock.patch("common.shutil.which", return_value="/usr/bin/java"), \
             mock.patch("common.android_studio_java_homes", return_value=[]), \
             mock.patch("common.run_tool", side_effect=fake_run):
            tools = common.discover_tools()
        self.assertEqual(str(good / "bin" / "java"), tools["java"].path)
        self.assertTrue(tools["java"].available)

        with mock.patch.dict("os.environ", {"ANDROID_HOME": "", "ANDROID_SDK_ROOT": "", "JAVA_HOME": ""}), \
             mock.patch("common.Path.home", return_value=self.tmp / "nohome"), \
             mock.patch("common.shutil.which", return_value="/usr/bin/java"), \
             mock.patch("common.android_studio_java_homes", return_value=[]), \
             mock.patch("common.run_tool", side_effect=fake_run):
            tools = common.discover_tools()
        self.assertFalse(tools["java"].available)
        self.assertIn("não executa", tools["java"].reason)
