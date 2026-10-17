"""Regenerate examples/ by REALLY running guardian.py on synthetic fixtures (no real app, no network).

Usage (from the repository root):
  python3 skills/release/appstore-review-guardian/tests/make_examples.py [--work /private/tmp/asrg-examples]

Writes:
  examples/appstore-audit-report.{json,md}          gate on a disposable Flutter-like project + UNSIGNED .ipa
  examples/fixture/appstore-audit-report.{json,md}  gate on the same project + debug-signed .ipa (fake profile)
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(HERE))
import fixtures as fx  # noqa: E402


def build(work: Path) -> dict[str, Path]:
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    proj = fx.flutter_project(work, social_login=True, apple_login=True, deletion=True, privacy_manifest_file=True,
                              aps="development",
                              extra_info={"NSPhotoLibraryUsageDescription": "O app usa suas fotos para definir a foto de perfil.",
                                          "ITSAppUsesNonExemptEncryption": False})
    # make the synthetic project match the synthetic artifact's bundle id
    pbx = proj / "ios" / "Runner.xcodeproj" / "project.pbxproj"
    pbx.write_text(pbx.read_text(encoding="utf-8").replace("com.example.demo", "com.example.app"), encoding="utf-8")
    info = fx.info_plist(CFBundleShortVersionString="2.0.1", CFBundleVersion="7",
                         NSPhotoLibraryUsageDescription="O app usa suas fotos para definir a foto de perfil.")
    unsigned = fx.zip_app(fx.build_app(work / "unsigned-src", info=info,
                                       frameworks={"Flutter": {"manifest": True}, "App": {}},
                                       strings=[b"_mach_absolute_time"]),
                          work / "Example-unsigned.ipa")
    debug = fx.zip_app(fx.build_app(work / "debug-src", info=fx.info_plist(ITSAppUsesNonExemptEncryption=None,
                                                                          NSAppTransportSecurity={"NSAllowsArbitraryLoads": True}),
                                    signed=True, entitlements={"get-task-allow": True, "aps-environment": "development",
                                                               "application-identifier": "ABCDE12345.com.example.app"},
                                    authority="Apple Development: Example Dev (ABCDE12345)", profile=fx.profile_bytes("development"),
                                    frameworks={"Flutter": {"manifest": True}, "Alamofire": {"manifest": False}}),
                       work / "Example-debug-signed.ipa")
    return {"project": proj, "unsigned": unsigned, "debug": debug}


def run(project: Path, ipa: Path, out: Path) -> int:
    cmd = [sys.executable, str(SKILL / "scripts" / "guardian.py"), "gate", "--release", str(project), "--ipa", str(ipa),
           "--output-dir", str(out), "--no-cache", "--json"]
    cp = subprocess.run(cmd, capture_output=True, text=True, check=False)
    print(cp.stdout.strip()[:400])
    if cp.returncode not in (0, 1, 2):
        raise SystemExit(f"guardian.py falhou ({cp.returncode}): {cp.stderr[-800:]}")
    return cp.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", type=Path, default=Path("/private/tmp/asrg-examples"))
    a = ap.parse_args(argv)
    inputs = build(a.work)
    for name, dest in (("unsigned", SKILL / "examples"), ("debug", SKILL / "examples" / "fixture")):
        out = a.work / f"out-{name}"
        run(inputs["project"], inputs[name], out)
        dest.mkdir(parents=True, exist_ok=True)
        for f in ("appstore-audit-report.json", "appstore-audit-report.md"):
            shutil.copyfile(out / f, dest / f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
