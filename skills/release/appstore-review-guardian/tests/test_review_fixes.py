"""Regression tests for the PR #52 review findings (offline, no real apps, no real network)."""
from __future__ import annotations

import io
import json
import os
import plistlib
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))
sys.path.insert(0, str(HERE))

import common  # noqa: E402
import fixtures as fx  # noqa: E402
import generate_report  # noqa: E402
import guardian  # noqa: E402
import inspect_asc  # noqa: E402
import inspect_ipa  # noqa: E402
import inspect_project  # noqa: E402
import validate_entitlements as ve  # noqa: E402
from common import INFO, NOT_VERIFIED, PASS, WARNING, Finding  # noqa: E402

try:
    import jsonschema  # noqa: F401
    HAVE_JSONSCHEMA = True
except ImportError:
    HAVE_JSONSCHEMA = False

NON_DETERMINISTIC = ("heuristic", "manual")


def no_tools():
    return mock.patch("shutil.which", return_value=None)


def method_of(f: Finding) -> str:
    return f.verification_method or common.rules().get(f.rule_id)["verification_method"]


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="asrg-fix-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with no_tools(), redirect_stdout(out), redirect_stderr(err):
            rc = guardian.main(list(args))
        return rc, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------- 1. heuristic/manual never PASS
class TestCentralPassGuard(unittest.TestCase):
    def test_heuristic_pass_on_mandatory_rule_becomes_not_verified(self):
        d = Finding("REV-ACCTDEL-001", PASS, "project", "deleteAccount() encontrado").to_dict(1)
        self.assertEqual(d["verification_method"], "heuristic")
        self.assertEqual(d["severity"], NOT_VERIFIED)
        self.assertTrue(any("heur" in n for n in d["notes"]))

    def test_heuristic_pass_on_recommended_rule_becomes_info(self):
        d = Finding("REV-IAP-002", PASS, "project", "restorePurchases encontrado").to_dict(1)
        self.assertEqual(d["severity"], INFO)

    def test_manual_pass_becomes_info(self):
        d = Finding("ASC-PRIV-001", PASS, "asc", "declarado pelo usuário").to_dict(1)
        self.assertEqual(d["severity"], INFO)
        self.assertTrue(d["notes"])

    def test_method_override_to_heuristic_also_guarded(self):
        d = Finding("PLIST-ID-001", PASS, "app", "x", verification_method="heuristic").to_dict(1)
        self.assertEqual(d["severity"], NOT_VERIFIED)

    def test_heuristic_not_applicable_from_absence_becomes_not_verified(self):
        d = Finding("REV-UGC-001", common.NOT_APPLICABLE, "project", "nenhum sinal de UGC").to_dict(1)
        self.assertEqual(d["severity"], NOT_VERIFIED)
        self.assertTrue(d["notes"])

    def test_inferred_or_low_confidence_blocker_is_capped_at_warning(self):
        for sev_in in (common.BLOCKER, common.CRITICAL):
            for method, conf in (("heuristic", 0.9), ("manual", 0.9), ("deterministic", 0.1)):
                with self.subTest(severity=sev_in, method=method, confidence=conf):
                    d = Finding("PLIST-ID-001", sev_in, "app", "x", verification_method=method,
                                confidence=conf).to_dict(1)
                    self.assertEqual(d["severity"], common.WARNING)
                    self.assertTrue(d["notes"])
        kept = Finding("PLIST-ID-001", common.BLOCKER, "app", "x").to_dict(1)
        self.assertEqual(kept["severity"], common.BLOCKER)

    def test_deterministic_pass_kept(self):
        d = Finding("PLIST-ID-001", PASS, "app", "CFBundleIdentifier=com.x").to_dict(1)
        self.assertEqual(d["severity"], PASS)
        self.assertNotIn("notes", d)

    def test_decide_never_ready_on_heuristic(self):
        det = Finding("PLIST-ID-001", PASS, "app", "ok").to_dict(1)
        heur = Finding("REV-LOGIN-001", PASS, "project", "Sign in with Apple encontrado").to_dict(2)
        self.assertEqual(generate_report.decide([det, heur], "full", True)[0], "NEEDS_REVIEW")
        # defensive: a hand-edited/re-rendered report with a heuristic PASS must not be READY either
        forged = dict(det, rule_id="REV-UGC-001", verification_method="heuristic", requirement_level="mandatory")
        status, reasons = generate_report.decide([det, forged], "full", True)
        self.assertEqual(status, "NEEDS_REVIEW")
        self.assertTrue(any("heur" in r for r in reasons))

    def test_schema_rejects_heuristic_or_manual_pass(self):
        if not HAVE_JSONSCHEMA:
            self.skipTest("jsonschema indisponível")
        tmp = Path(tempfile.mkdtemp(prefix="asrg-fix-"))
        try:
            proj = fx.flutter_project(tmp)
            out = tmp / "out"
            with no_tools(), redirect_stdout(io.StringIO()):
                guardian.main(["audit", "--quick", str(proj), "--output-dir", str(out)])
            rep = json.loads((out / "appstore-audit-report.json").read_text())
            self.assertEqual(generate_report.validate_report(rep), [])
            for m in NON_DETERMINISTIC:
                bad = json.loads(json.dumps(rep))
                bad["findings"][0]["severity"] = PASS
                bad["findings"][0]["verification_method"] = m
                self.assertTrue(generate_report.validate_report(bad), m)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestNoHeuristicPassInReports(TmpCase):
    """Call sites must not emit heuristic/manual PASS at all (the central guard is only a backstop)."""

    def assert_no_raw_pass(self, findings):
        bad = [(f.rule_id, f.severity, f.evidence) for f in findings
               if f.severity in (PASS, common.NOT_APPLICABLE) and method_of(f) in NON_DETERMINISTIC]
        self.assertEqual(bad, [])

    def test_project_call_sites(self):
        for i, kw in enumerate([dict(social_login=True, apple_login=True, deletion=True, privacy_manifest_file=True),
                                dict(social_login=True, apple_login=False, deletion=False),
                                dict(social_login=False)]):
            proj = fx.flutter_project(self.tmp / f"p{i}", **kw)
            (proj / "ios" / "Configuration.storekit").write_text("{}", encoding="utf-8")
            (proj / "lib" / "shop.dart").write_text(
                "class Shop { void r() { InAppPurchase.instance.restorePurchases(); reportUser(); } }\n", encoding="utf-8")
            f, _ = inspect_project.inspect_project(proj, "full")
            self.assert_no_raw_pass(f)

    def test_ipa_call_sites(self):
        for i, kw in enumerate([dict(strings=[b"_mach_absolute_time"]), dict(manifest=None, frameworks={}), dict()]):
            ipa = fx.zip_app(fx.build_app(self.tmp / f"b{i}", **kw), self.tmp / f"{i}.ipa")
            with no_tools():
                f, _ = inspect_ipa.inspect_ipa(ipa)
            self.assert_no_raw_pass(f)

    def test_asc_manual_call_sites(self):
        md = json.loads(json.dumps(inspect_asc.TEMPLATE))
        for k, v in list(md.items()):
            if v is None or isinstance(v, bool):
                md[k] = True
        md["demo_account"] = {"provided": True}
        md["review_notes"] = "Use a conta demo."
        f, _ = inspect_asc.check_asc(md, env={})
        self.assert_no_raw_pass(f)

    def test_full_report_on_fixtures_has_no_heuristic_or_manual_pass(self):
        proj = fx.flutter_project(self.tmp, social_login=True, apple_login=True, deletion=True, privacy_manifest_file=True)
        ipa = fx.zip_app(fx.build_app(self.tmp / "b", strings=[b"_mach_absolute_time"]), self.tmp / "a.ipa")
        md = json.loads(json.dumps(inspect_asc.TEMPLATE))
        for k, v in list(md.items()):
            if v is None or isinstance(v, bool):
                md[k] = True
        md["demo_account"] = {"provided": True}
        md["review_notes"] = "Use a conta demo."
        asc = self.tmp / "asc.json"
        asc.write_text(json.dumps(md), encoding="utf-8")
        for cmd in (["audit", "--full", str(proj), "--ipa", str(ipa)], ["gate", "--release", str(proj), "--ipa", str(ipa)]):
            out = self.tmp / ("out-" + cmd[0])
            rc, _, err = self.run_cli(*cmd, "--asc-metadata", str(asc), "--output-dir", str(out), "--no-cache")
            self.assertNotIn("Traceback", err)
            rep = json.loads((out / "appstore-audit-report.json").read_text())
            methods = {f["verification_method"] for f in rep["findings"]}
            self.assertIn("heuristic", methods)
            self.assertIn("manual", methods)
            bad = [(f["rule_id"], f["verification_method"]) for f in rep["findings"]
                   if f["severity"] == PASS and f["verification_method"] in NON_DETERMINISTIC]
            self.assertEqual(bad, [])
            self.assertNotEqual(rep["final_status"], "READY")


# --------------------------------------------------------------------------- 2. hostile member names / module isolation
class TestHostileMemberNames(TmpCase):
    def hostile_ipa(self, name, extra):
        app = fx.build_app(self.tmp / f"src-{name}")
        return fx.zip_app(app, self.tmp / f"{name}.ipa", extra=extra)

    def check(self, ipa):
        out = self.tmp / ("out-" + ipa.stem)
        rc, stdout, err = self.run_cli("audit", "--full", "--ipa", str(ipa), "--output-dir", str(out), "--json")
        self.assertNotIn("Traceback", err)
        self.assertIn(rc, (0, 1, 2))
        self.assertTrue((out / "appstore-audit-report.json").is_file())
        rep = json.loads((out / "appstore-audit-report.json").read_text())
        ev = " ".join(f["evidence"] for f in rep["findings"] if f["rule_id"] == "IPA-STRUCT-001")
        self.assertIn("não extraída", ev)
        self.assertNotEqual(rep["final_status"], "READY")
        return rep

    def test_case_insensitive_collision(self):
        self.check(self.hostile_ipa("case", [("Payload/Runner.app/a.bin", b"1"), ("Payload/Runner.app/A.BIN", b"2")]))

    def test_file_then_directory_with_same_name(self):
        self.check(self.hostile_ipa("filedir", [("Payload/Runner.app/x", b"1"), ("Payload/Runner.app/x/y", b"2")]))

    def test_name_component_longer_than_255_bytes(self):
        self.check(self.hostile_ipa("long", [("Payload/Runner.app/" + "n" * 300, b"1")]))

    def test_extract_members_never_raises_oserror(self):
        ipa = self.hostile_ipa("unit", [("Payload/Runner.app/x", b"1"), ("Payload/Runner.app/x/y", b"2"),
                                        ("Payload/Runner.app/" + "n" * 300, b"1")])
        dest = common.make_private_tempdir()
        try:
            with zipfile.ZipFile(ipa) as zf:
                res = common.extract_members(zf, [n for n in zf.namelist() if not n.endswith("/")], dest)
            self.assertEqual(len(res.skipped), 2)
            self.assertTrue(all(reason for _, reason in res.skipped))
            self.assertTrue(res.path_for("Payload/Runner.app/Info.plist").is_file())
        finally:
            common.cleanup_dir(dest)

    def test_module_failure_becomes_not_verified(self):
        proj = fx.flutter_project(self.tmp)
        ipa = fx.zip_app(fx.build_app(self.tmp / "b"), self.tmp / "a.ipa")
        out = self.tmp / "out"
        with mock.patch.object(inspect_project, "inspect_project", side_effect=RuntimeError("boom")), \
                mock.patch.object(inspect_ipa, "inspect_ipa", side_effect=OSError(36, "File name too long")):
            rc, stdout, err = self.run_cli("gate", "--release", str(proj), "--ipa", str(ipa), "--output-dir", str(out), "--json")
        self.assertNotIn("Traceback", err)
        self.assertEqual(rc, 1)
        rep = json.loads((out / "appstore-audit-report.json").read_text())
        errs = [f for f in rep["findings"] if f["rule_id"] == "MODULE-ERROR-001"]
        self.assertEqual({f["component"] for f in errs}, {"project", "ipa"})
        self.assertEqual({f["severity"] for f in errs}, {NOT_VERIFIED})
        self.assertEqual({f["requirement_level"] for f in errs}, {"mandatory"})
        self.assertTrue(any(p["rule_id"] == "MODULE-ERROR-001" and p["gate_relevant"] for p in rep["pending"]))
        # a crashed module is never cached as a valid result
        cache = json.loads((out / ".guardian-cache.json").read_text())
        self.assertFalse([k for k in cache["entries"] if k.startswith(("project:", "ipa:"))])


# --------------------------------------------------------------------------- 3. AASA SSRF
def gai(*ips):
    def fake(host, port, *a, **kw):
        out = []
        for ip in ips:
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            out.append((fam, socket.SOCK_STREAM, 6, "", (ip, port) if fam == socket.AF_INET else (ip, port, 0, 0)))
        return out
    return fake


PUBLIC = "93.184.215.14"


class TestAasaSsrf(unittest.TestCase):
    def test_rejects_non_dns_hosts(self):
        bad = ["applinks:127.0.0.1", "applinks:[::1]", "applinks:::1", "applinks:2130706433", "applinks:0x7f000001",
               "applinks:0x7f.0.0.1", "applinks:017700000001", "applinks:localhost", "applinks:foo.localhost",
               "applinks:intranet", "applinks:printer.local", "applinks:db.internal", "applinks:nas.lan",
               "applinks:router.home.arpa", "applinks:example.com:8443", "applinks:user@example.com",
               "applinks:exa mple.com", "applinks:-bad.example.com", "applinks:example.com/path", "applinks:10.0.0.1.nip.io.",
               "applinks:" + "a" * 64 + ".com"]
        for entry in bad:
            host, reason = ve.aasa_host(entry)
            self.assertIsNone(host, entry)
            self.assertTrue(reason, entry)

    def test_accepts_dns_hosts(self):
        self.assertEqual(ve.aasa_host("applinks:example.com")[0], "example.com")
        self.assertEqual(ve.aasa_host("webcredentials:Login.Example.com")[0], "login.example.com")
        self.assertEqual(ve.aasa_host("applinks:example.com?mode=developer")[0], "example.com")
        self.assertEqual(ve.aasa_host("applinks:example.com:443")[0], "example.com")
        self.assertIsNone(ve.aasa_host("activitycontinuation:example.com")[0])

    def test_private_resolution_never_fetched(self):
        fetch = mock.Mock(side_effect=AssertionError("must not fetch"))
        for ips in (("10.0.0.5",), ("127.0.0.1",), ("169.254.169.254",), ("::1",), ("fe80::1",), ("fd00::1",),
                    ("224.0.0.1",), ("0.0.0.0",), ("100.64.0.1",), ("::ffff:127.0.0.1",), (PUBLIC, "192.168.1.1")):
            res = ve.check_aasa(["applinks:example.com"], fetch, resolver=gai(*ips))
            self.assertEqual([f.severity for f in res], [NOT_VERIFIED], ips)
            self.assertIn("não consultado", res[0].evidence)
        fetch.assert_not_called()

    def test_ip_literal_entry_is_warning_and_not_resolved(self):
        resolver = mock.Mock(side_effect=AssertionError("must not resolve"))
        fetch = mock.Mock(side_effect=AssertionError("must not fetch"))
        res = ve.check_aasa(["applinks:169.254.169.254", "webcredentials:localhost"], fetch, resolver=resolver)
        self.assertEqual({f.severity for f in res}, {WARNING})
        resolver.assert_not_called()
        fetch.assert_not_called()

    def test_public_host_fetched_once_with_pinned_ip(self):
        calls = []

        def fetch(url, ip):
            calls.append((url, ip))
            return 200, b'{"applinks":{"details":[]},"webcredentials":{"apps":[]}}'
        res = ve.check_aasa(["applinks:example.com", "webcredentials:example.com"], fetch, resolver=gai(PUBLIC))
        self.assertEqual(calls, [("https://example.com/.well-known/apple-app-site-association", PUBLIC)])
        self.assertEqual([f.severity for f in res], [PASS])

    def test_resolution_failure_not_verified(self):
        def boom(*a, **kw):
            raise socket.gaierror("nodename nor servname provided")
        res = ve.check_aasa(["applinks:example.com"], mock.Mock(), resolver=boom)
        self.assertEqual([f.severity for f in res], [NOT_VERIFIED])

    def test_redirects_refused(self):
        h = ve.NoRedirect()
        req = urllib.request.Request("https://example.com/.well-known/apple-app-site-association")
        with self.assertRaises(urllib.error.HTTPError):
            h.redirect_request(req, None, 301, "Moved", {}, "https://169.254.169.254/")

    def test_safe_fetch_https_only_size_cap_no_proxy(self):
        with self.assertRaises(ValueError):
            ve.safe_fetch("http://example.com/.well-known/apple-app-site-association", PUBLIC)

        class Resp:
            status = 200

            def __init__(self, n):
                self.n = n

            def read(self, k=-1):
                return b"x" * min(self.n, k if k >= 0 else self.n)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        opener = mock.Mock()
        opener.open.return_value = Resp(ve.AASA_MAX_BYTES + 10)
        with mock.patch.object(ve, "_build_opener", return_value=opener) as bo:
            with self.assertRaises(ValueError):
                ve.safe_fetch("https://example.com/.well-known/apple-app-site-association", PUBLIC)
        bo.assert_called_once_with(PUBLIC)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], ve.AASA_TIMEOUT)
        opener.open.return_value = Resp(100)
        with mock.patch.object(ve, "_build_opener", return_value=opener):
            status, body = ve.safe_fetch("https://example.com/.well-known/apple-app-site-association", PUBLIC)
        self.assertEqual((status, len(body)), (200, 100))
        real = ve._build_opener(PUBLIC)
        kinds = [type(h) for h in real.handlers]
        self.assertIn(ve.NoRedirect, kinds)
        self.assertNotIn(urllib.request.HTTPRedirectHandler, kinds)
        proxies = [h for h in real.handlers if isinstance(h, urllib.request.ProxyHandler)]
        self.assertTrue(all(not p.proxies for p in proxies))

    def test_guardian_network_uses_safe_fetch(self):
        tmp = Path(tempfile.mkdtemp(prefix="asrg-fix-"))
        try:
            proj = fx.flutter_project(tmp)
            ents = plistlib.loads((proj / "ios/Runner/Runner.entitlements").read_bytes())
            ents["com.apple.developer.associated-domains"] = ["applinks:127.0.0.1", "applinks:example.com"]
            (proj / "ios/Runner/Runner.entitlements").write_bytes(plistlib.dumps(ents))
            seen = []
            with mock.patch.object(ve, "safe_fetch", side_effect=lambda u, ip: seen.append((u, ip)) or (404, b"")), \
                    mock.patch.object(socket, "getaddrinfo", gai(PUBLIC)), \
                    mock.patch("check_sources.default_fetch", side_effect=AssertionError("unsafe fetcher used")), \
                    no_tools(), redirect_stdout(io.StringIO()):
                guardian.main(["audit", "--full", str(proj), "--network", "--output-dir", str(tmp / "out"), "--no-cache"])
            self.assertEqual(seen, [("https://example.com/.well-known/apple-app-site-association", PUBLIC)])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- 4. zip bomb budget
class TestZipBudget(TmpCase):
    def many_small_bombs(self, n=40, size=256 * 1024):
        ipa = self.tmp / "many.ipa"
        with zipfile.ZipFile(ipa, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Payload/Runner.app/Info.plist", plistlib.dumps(fx.info_plist()))
            for i in range(n):
                zf.writestr(f"Payload/Runner.app/chunk{i:03d}.bin", b"\0" * size)
        return ipa

    def test_aggregate_ratio_rejected_even_when_each_entry_is_below_ratio_threshold(self):
        ipa = self.many_small_bombs()
        with zipfile.ZipFile(ipa) as zf:
            self.assertTrue(all(i.file_size < common.RATIO_MIN_SIZE for i in zf.infolist()))
            with self.assertRaises(common.UnsafeArchive) as cm:
                common.validate_zip(zf, total_ratio_min=1024 * 1024)
        self.assertIn("zip bomb", str(cm.exception))
        with mock.patch.object(common, "TOTAL_RATIO_MIN_SIZE", 1024 * 1024):
            f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual({x.severity for x in f if x.rule_id == "IPA-STRUCT-001"}, {common.BLOCKER})

    def test_streamed_bytes_budget_enforced(self):
        ipa = self.many_small_bombs(n=8, size=64 * 1024)
        with zipfile.ZipFile(ipa) as zf:
            budget = common.ReadBudget(200 * 1024)
            with self.assertRaises(common.UnsafeArchive) as cm:
                common.stream_check(zf, zf.infolist(), budget)
        self.assertIn("orçamento", str(cm.exception))
        self.assertLessEqual(budget.used, 200 * 1024 + 1024 * 1024)
        with mock.patch.object(common, "MAX_STREAM_BYTES", 200 * 1024), \
                mock.patch.object(common, "TOTAL_RATIO_MIN_SIZE", 1 << 40):
            f, _ = inspect_ipa.inspect_ipa(ipa)
        ev = " ".join(x.evidence for x in f if x.rule_id == "IPA-STRUCT-001")
        self.assertIn("orçamento", ev)

    def test_legit_fixture_ipa_still_accepted(self):
        ipa = fx.zip_app(fx.build_app(self.tmp / "ok"), self.tmp / "ok.ipa")
        with zipfile.ZipFile(ipa) as zf:
            infos = common.validate_zip(zf)
            common.stream_check(zf, infos, common.ReadBudget())
        with no_tools():
            f, _ = inspect_ipa.inspect_ipa(ipa)
        self.assertEqual({x.severity for x in f if x.rule_id == "IPA-STRUCT-001"}, {PASS})


# --------------------------------------------------------------------------- 5. private output
@unittest.skipIf(os.name != "posix", "POSIX permissions only")
class TestPrivateOutput(TmpCase):
    def mode(self, p: Path) -> int:
        return p.stat().st_mode & 0o777

    def test_default_output_dir_is_private_mkdtemp(self):
        proj = fx.flutter_project(self.tmp)
        rc, stdout, _ = self.run_cli("audit", "--quick", str(proj), "--json")
        s = json.loads(stdout)
        jp = Path(s["reports"]["json"])
        try:
            self.assertEqual(self.mode(jp.parent), 0o700)
            self.assertNotEqual(jp.parent, Path(tempfile.gettempdir()) / "appstore-review-guardian" / proj.name)
            for p in jp.parent.iterdir():
                self.assertEqual(self.mode(p), 0o600, p.name)
        finally:
            shutil.rmtree(jp.parent, ignore_errors=True)

    def test_user_output_dir_created_private_and_files_0600(self):
        proj = fx.flutter_project(self.tmp)
        out = self.tmp / "new" / "nested" / "out"
        old = os.umask(0o022)
        try:
            self.run_cli("audit", "--quick", str(proj), "--output-dir", str(out))
        finally:
            os.umask(old)
        for d in (self.tmp / "new", self.tmp / "new" / "nested", out):
            self.assertEqual(self.mode(d), 0o700, d)
        names = {p.name for p in out.iterdir()}
        self.assertTrue({"appstore-audit-report.json", "appstore-audit-report.md", ".guardian-cache.json"} <= names)
        for p in out.iterdir():
            self.assertEqual(self.mode(p), 0o600, p.name)

    def test_existing_user_dir_mode_untouched_files_private(self):
        proj = fx.flutter_project(self.tmp)
        out = self.tmp / "exists"
        out.mkdir(mode=0o755)
        os.chmod(out, 0o755)
        (out / "appstore-audit-report.json").write_text("{}")
        os.chmod(out / "appstore-audit-report.json", 0o644)
        self.run_cli("audit", "--quick", str(proj), "--output-dir", str(out))
        self.assertEqual(self.mode(out), 0o755)
        self.assertEqual(self.mode(out / "appstore-audit-report.json"), 0o600)

    def test_rules_cache_private(self):
        out = self.tmp / "rules-out"
        self.run_cli("rules", "--check-sources", "--offline", "--output-dir", str(out), "--json")
        self.assertEqual(self.mode(out), 0o700)
        self.assertEqual(self.mode(out / "sources-cache.json"), 0o600)


# --------------------------------------------------------------------------- 6. no private data in the public skill
class TestNoPrivateData(unittest.TestCase):
    NEEDLES = ["/" + "Users/", "men" + "tly", "/" + "home/" + "felipe"]  # split so this file does not match itself

    def test_skill_tree_has_no_personal_data(self):
        hits = []
        for p in SKILL.rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix == ".pyc":
                continue
            text = p.read_text(encoding="utf-8", errors="replace").lower()
            for n in self.NEEDLES:
                if n.lower() in text:
                    hits.append((str(p.relative_to(SKILL)), n))
        self.assertEqual(hits, [])

    def test_examples_are_generated_and_consistent(self):
        for sub in ("examples", "examples/fixture"):
            rep = json.loads((SKILL / sub / "appstore-audit-report.json").read_text(encoding="utf-8"))
            self.assertEqual(rep["tool"]["version"], common.TOOL_VERSION)
            bad = [f["rule_id"] for f in rep["findings"] if f["severity"] == PASS and f["verification_method"] in NON_DETERMINISTIC]
            self.assertEqual(bad, [], sub)
            if HAVE_JSONSCHEMA:
                self.assertEqual(generate_report.validate_report(rep), [], sub)


if __name__ == "__main__":
    unittest.main()
