"""Regenerate the synthetic example report exactly as examples/README.md documents it.

Run from the skill directory:
    python tests/make_examples.py
It builds the fixtures in /private/tmp/gprg-example/, runs `guardian.py gate` and copies the two
reports into examples/. Keeping this script next to the fixtures is what makes the documented
provenance reproducible: no example is ever hand-edited.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(SKILL / "tests"), str(SKILL / "scripts")]

import fixtures as fx  # noqa: E402

ROOT = Path("/private/tmp/gprg-example")
COMPONENTS = [
    ("activity", {"android:name": ".MainActivity", "android:exported": True},
     [("intent-filter", {}, [("action", {"android:name": "android.intent.action.MAIN"}, []),
                             ("category", {"android:name": "android.intent.category.LAUNCHER"}, [])])]),
    ("service", {"android:name": "com.example.Player", "android:foregroundServiceType": "mediaPlayback",
                 "android:exported": False}, []),
]


def build() -> Path:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    proj = fx.make_flutter_project(ROOT / "demo_app", target_sdk="36", minify=True)
    gradle = proj / "android/app/build.gradle"
    gradle.write_text(gradle.read_text().replace("android {", "android {\n    ndkVersion '28.0.13004108'", 1))
    out = proj / "build/app/outputs"
    (out / "bundle/release").mkdir(parents=True)
    (out / "flutter-apk").mkdir(parents=True)
    arm64, arm32 = fx.make_elf(), fx.make_elf(bits=32, machine=40)
    fx.make_aab(out / "bundle/release/app-release.aab",
                fx.manifest_tree(permissions=["android.permission.INTERNET",
                                              "android.permission.ACCESS_BACKGROUND_LOCATION",
                                              "com.android.vending.BILLING"], components=COMPONENTS),
                libs={"arm64-v8a/libapp.so": arm64, "armeabi-v7a/libapp.so": arm32}, billing="7.1.1")
    fx.make_apk(out / "flutter-apk/app-release.apk",
                fx.manifest_tree(permissions=["android.permission.INTERNET"]),
                libs={"arm64-v8a/libapp.so": arm64}, signer_cn="Release Key")
    return proj


def main() -> int:
    proj = build()
    out = ROOT / "out"
    cmd = [sys.executable, str(SKILL / "scripts/guardian.py"), "gate", "--release", str(proj),
           "--output-dir", str(out), "--no-tools", "--no-cache", "--json", "--as-of", "2026-10-16"]
    code = subprocess.run(cmd, check=False).returncode
    for name in ("google-play-audit-report.md", "google-play-audit-report.json"):
        shutil.copy(out / name, SKILL / "examples" / name)
    print(f"gate exit={code}; examples refreshed from {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
