"""Programmatic fixtures for appstore-review-guardian tests (no network, no real apps)."""
from __future__ import annotations

import datetime as dt
import plistlib
import stat
import struct
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

MH_MAGIC_64 = 0xFEEDFACF
CPU_ARM64 = 0x0100000C
CPU_X86_64 = 0x01000007


def _ver(s: str) -> int:
    p = [int(x) for x in s.split(".")] + [0, 0]
    return (p[0] << 16) | (p[1] << 8) | p[2]


def superblob(entitlements: dict | None, cms_text: bytes | None) -> bytes:
    blobs = []
    if entitlements is not None:
        body = plistlib.dumps(entitlements, fmt=plistlib.FMT_XML)
        blobs.append((5, struct.pack(">II", 0xFADE7171, 8 + len(body)) + body))
    if cms_text is not None:
        blobs.append((0x10000, struct.pack(">II", 0xFADE0B01, 8 + len(cms_text)) + cms_text))
    header_len = 12 + 8 * len(blobs)
    index, data, off = b"", b"", header_len
    for t, b in blobs:
        index += struct.pack(">II", t, off)
        data += b
        off += len(b)
    return struct.pack(">III", 0xFADE0CC0, header_len + len(data), len(blobs)) + index + data


def macho(cpu: int = CPU_ARM64, platform: int = 2, minos: str = "15.0", sdk: str = "26.4",
          dylibs: list[str] | None = None, strings: list[bytes] | None = None,
          entitlements: dict | None = None, cms_text: bytes | None = None, signed: bool = False,
          dwarf: bool = False) -> bytes:
    cmds = []
    cmds.append(struct.pack("<IIIIII", 0x32, 24, platform, _ver(minos), _ver(sdk), 0))
    for seg in ["__TEXT"] + (["__DWARF"] if dwarf else []):
        cmds.append(struct.pack("<II16sQQQQIIII", 0x19, 72, seg.encode(), 0, 0, 0, 0, 5, 5, 0, 0))
    for d in dylibs or []:
        name = d.encode() + b"\0"
        name += b"\0" * ((8 - (24 + len(name)) % 8) % 8)
        cmds.append(struct.pack("<IIIIII", 0xC, 24 + len(name), 24, 2, 0x10000, 0x10000) + name)
    payload = b"\0" + b"\0".join(strings or []) + b"\0" * 8
    sig = superblob(entitlements, cms_text) if signed else b""
    sizeofcmds = sum(len(c) for c in cmds) + (16 if signed else 0)
    header_len = 32
    data_off = header_len + sizeofcmds
    sig_off = data_off + len(payload)
    if signed:
        cmds.append(struct.pack("<IIII", 0x1D, 16, sig_off, len(sig)))
    header = struct.pack("<IiiIIIII", MH_MAGIC_64, cpu, 0, 2, len(cmds), sizeofcmds, 0, 0)
    return header + b"".join(cmds) + payload + sig


def info_plist(**over) -> dict:
    d = {
        "CFBundleIdentifier": "com.example.app",
        "CFBundleExecutable": "Runner",
        "CFBundleName": "Example",
        "CFBundleDisplayName": "Example App",
        "CFBundleShortVersionString": "1.2.0",
        "CFBundleVersion": "42",
        "CFBundlePackageType": "APPL",
        "MinimumOSVersion": "15.0",
        "DTXcode": "2641",
        "DTSDKName": "iphoneos26.4",
        "DTPlatformVersion": "26.4",
        "ITSAppUsesNonExemptEncryption": False,
        "NSCameraUsageDescription": "O app usa a câmera para digitalizar o QR code do ingresso.",
        "UIBackgroundModes": ["remote-notification"],
    }
    for k, v in over.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    return d


def privacy_manifest(**over) -> dict:
    d = {
        "NSPrivacyTracking": False,
        "NSPrivacyTrackingDomains": [],
        "NSPrivacyCollectedDataTypes": [{
            "NSPrivacyCollectedDataType": "NSPrivacyCollectedDataTypeEmailAddress",
            "NSPrivacyCollectedDataTypeLinked": True,
            "NSPrivacyCollectedDataTypeTracking": False,
            "NSPrivacyCollectedDataTypePurposes": ["NSPrivacyCollectedDataTypePurposeAppFunctionality"],
        }],
        "NSPrivacyAccessedAPITypes": [
            {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryUserDefaults", "NSPrivacyAccessedAPITypeReasons": ["CA92.1"]},
            {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryFileTimestamp", "NSPrivacyAccessedAPITypeReasons": ["C617.1"]},
        ],
    }
    d.update(over)
    return d


def profile_bytes(kind: str = "app_store", bundle_id: str = "com.example.app", expired: bool = False,
                  entitlements: dict | None = None) -> bytes:
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    p = {
        "Name": f"Test {kind}",
        "TeamIdentifier": ["ABCDE12345"],
        "ExpirationDate": now + (dt.timedelta(days=-1) if expired else dt.timedelta(days=200)),
        "Entitlements": entitlements if entitlements is not None else {
            "application-identifier": f"ABCDE12345.{bundle_id}",
            "com.apple.developer.team-identifier": "ABCDE12345",
            "aps-environment": "production",
            "get-task-allow": kind == "development",
        },
    }
    if kind in ("development", "ad_hoc"):
        p["ProvisionedDevices"] = ["00008030-000000000000002E"]
    if kind == "enterprise":
        p["ProvisionsAllDevices"] = True
    # Fake CMS wrapper: DER-ish prefix + XML plist (the stdlib fallback only needs the XML).
    return b"\x30\x82\x10\x00FAKECMS" + plistlib.dumps(p, fmt=plistlib.FMT_XML) + b"\x00\x00FAKESIG"


def build_app(root: Path, name: str = "Runner", info: dict | None = None, manifest: dict | bytes | None = "default",
              signed: bool = False, entitlements: dict | None = None, authority: str = "Apple Distribution: Example (ABCDE12345)",
              profile: bytes | None = None, frameworks: dict | None = None, strings: list[bytes] | None = None,
              binary_plist: bool = False, cpu: int = CPU_ARM64, platform: int = 2, flutter_debug: bool = False,
              dylibs: list[str] | None = None, dsym: bool = False) -> Path:
    app = root / f"{name}.app"
    app.mkdir(parents=True, exist_ok=True)
    info = info if info is not None else info_plist()
    fmt = plistlib.FMT_BINARY if binary_plist else plistlib.FMT_XML
    (app / "Info.plist").write_bytes(plistlib.dumps(info, fmt=fmt))
    ents = entitlements if entitlements is not None else {"application-identifier": "ABCDE12345.com.example.app",
                                                          "aps-environment": "production", "get-task-allow": False}
    exe = info.get("CFBundleExecutable", name)
    (app / exe).write_bytes(macho(cpu=cpu, platform=platform, strings=strings, signed=signed, entitlements=ents if signed else None,
                                  cms_text=b"\x30\x82" + authority.encode() + b"\x00Apple Worldwide Developer Relations" if signed else None,
                                  dylibs=dylibs or ["/usr/lib/libSystem.B.dylib", "/System/Library/Frameworks/UIKit.framework/UIKit"]))
    if manifest == "default":
        manifest = privacy_manifest()
    if isinstance(manifest, dict):
        (app / "PrivacyInfo.xcprivacy").write_bytes(plistlib.dumps(manifest))
    elif isinstance(manifest, bytes):
        (app / "PrivacyInfo.xcprivacy").write_bytes(manifest)
    if signed:
        (app / "_CodeSignature").mkdir(exist_ok=True)
        (app / "_CodeSignature" / "CodeResources").write_bytes(plistlib.dumps({"files": {}}))
        if profile is not None:
            (app / "embedded.mobileprovision").write_bytes(profile)
    fws = frameworks if frameworks is not None else {"Flutter": {"manifest": True}}
    for fw, opt in fws.items():
        d = app / "Frameworks" / f"{fw}.framework"
        d.mkdir(parents=True, exist_ok=True)
        (d / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": fw, "CFBundleIdentifier": f"io.example.{fw}"}))
        (d / fw).write_bytes(macho(cpu=opt.get("cpu", CPU_ARM64), platform=opt.get("platform", 2), strings=opt.get("strings")))
        if opt.get("manifest"):
            (d / "PrivacyInfo.xcprivacy").write_bytes(plistlib.dumps(privacy_manifest(NSPrivacyCollectedDataTypes=[])))
    if flutter_debug or "App" in fws:
        appfw = app / "Frameworks" / "App.framework" / "flutter_assets"
        appfw.mkdir(parents=True, exist_ok=True)
        if flutter_debug:
            (appfw / "kernel_blob.bin").write_bytes(b"\0" * 64)
    if dsym:
        (app / "Runner.app.dSYM" / "Contents").mkdir(parents=True, exist_ok=True)
        (app / "Runner.app.dSYM" / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundlePackageType": "dSYM"}))
    return app


def zip_app(app: Path, ipa: Path, extra: list[tuple[str, bytes]] | None = None, prefix: str = "Payload/") -> Path:
    with zipfile.ZipFile(ipa, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(app.rglob("*")):
            if p.is_file():
                zf.write(p, prefix + str(Path(app.name) / p.relative_to(app)))
        for name, data in extra or []:
            zf.writestr(name, data)
    return ipa


def zip_with_symlink(ipa: Path) -> Path:
    with zipfile.ZipFile(ipa, "w") as zf:
        zf.writestr("Payload/Runner.app/Info.plist", plistlib.dumps(info_plist()))
        zi = zipfile.ZipInfo("Payload/Runner.app/link")
        zi.create_system = 3
        zi.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(zi, "/etc/passwd")
    return ipa


def zip_bomb(ipa: Path, size: int = 64 * 1024 * 1024) -> Path:
    with zipfile.ZipFile(ipa, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Payload/Runner.app/Info.plist", plistlib.dumps(info_plist()))
        zf.writestr("Payload/Runner.app/bomb.bin", b"\0" * size)
    return ipa


def flutter_project(root: Path, *, social_login: bool = True, apple_login: bool = False, deletion: bool = False,
                    deployment: str = "15.0", aps: str = "development", privacy_manifest_file: bool = False,
                    extra_info: dict | None = None) -> Path:
    app = root / "flutter_app"
    (app / "lib" / "auth").mkdir(parents=True)
    (app / "ios" / "Runner").mkdir(parents=True)
    (app / "ios" / "Runner.xcodeproj").mkdir(parents=True)
    (app / "ios" / "Flutter").mkdir(parents=True)
    (app / "pubspec.yaml").write_text(
        "name: demo\nversion: 2.0.1+7\nenvironment:\n  sdk: '>=3.3.0 <4.0.0'\ndependencies:\n  flutter:\n    sdk: flutter\n"
        "  image_picker: ^1.1.2\n  firebase_messaging: ^15.0.0\n" + ("  google_sign_in: ^6.2.1\n" if social_login else "")
        + ("  sign_in_with_apple: ^6.1.0\n" if apple_login else ""), encoding="utf-8")
    (app / "pubspec.lock").write_text(
        "packages:\n  image_picker:\n    dependency: \"direct main\"\n    source: hosted\n    version: \"1.1.2\"\n"
        "  firebase_messaging:\n    dependency: \"direct main\"\n    source: hosted\n    version: \"15.0.0\"\n"
        + ("  google_sign_in:\n    dependency: \"direct main\"\n    source: hosted\n    version: \"6.2.1\"\n" if social_login else "")
        + "sdks:\n  dart: \">=3.5.0 <4.0.0\"\n  flutter: \">=3.24.0\"\n", encoding="utf-8")
    (app / "ios" / "Podfile").write_text(f"platform :ios, '{deployment}'\ntarget 'Runner' do\nend\n", encoding="utf-8")
    (app / "ios" / "Podfile.lock").write_text("PODS:\n  - Flutter (1.0.0)\n  - FirebaseMessaging (11.0.0):\n    - FirebaseCore\n\nDEPENDENCIES:\n", encoding="utf-8")
    (app / "ios" / "ExportOptions.plist").write_bytes(plistlib.dumps({"method": "app-store-connect"}))
    (app / "ios" / "Flutter" / "Generated.xcconfig").write_text("FLUTTER_BUILD_NAME=2.0.1\nFLUTTER_BUILD_NUMBER=7\n", encoding="utf-8")
    info = {
        "CFBundleIdentifier": "$(PRODUCT_BUNDLE_IDENTIFIER)", "CFBundleExecutable": "$(EXECUTABLE_NAME)",
        "CFBundleShortVersionString": "$(FLUTTER_BUILD_NAME)", "CFBundleVersion": "$(FLUTTER_BUILD_NUMBER)",
        "NSPhotoLibraryUsageDescription": "Allow this app to access your photos",
        "UIBackgroundModes": ["remote-notification"],
    }
    info.update(extra_info or {})
    (app / "ios" / "Runner" / "Info.plist").write_bytes(plistlib.dumps(info))
    ents = {"aps-environment": aps}
    if apple_login:
        ents["com.apple.developer.applesignin"] = ["Default"]
    (app / "ios" / "Runner" / "Runner.entitlements").write_bytes(plistlib.dumps(ents))
    if privacy_manifest_file:
        (app / "ios" / "Runner" / "PrivacyInfo.xcprivacy").write_bytes(plistlib.dumps(privacy_manifest()))
    pbx = (
        "// !$*UTF8*$!\n{\n\tobjects = {\n"
        "/* Begin XCBuildConfiguration section */\n"
        "\t\tAAA /* Release */ = {\n\t\t\tisa = XCBuildConfiguration;\n\t\t\tbuildSettings = {\n"
        f"\t\t\t\tIPHONEOS_DEPLOYMENT_TARGET = {deployment};\n\t\t\t\tSDKROOT = iphoneos;\n\t\t\t}};\n\t\t\tname = Release;\n\t\t}};\n"
        "\t\tBBB /* Release */ = {\n\t\t\tisa = XCBuildConfiguration;\n\t\t\tbuildSettings = {\n"
        "\t\t\t\tCODE_SIGN_ENTITLEMENTS = Runner/Runner.entitlements;\n\t\t\t\tINFOPLIST_FILE = Runner/Info.plist;\n"
        "\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = com.example.demo;\n"
        + ("\t\t\t\tPRIVACY = 1; /* PrivacyInfo.xcprivacy in Resources */\n" if privacy_manifest_file else "")
        + "\t\t\t};\n\t\t\tname = Release;\n\t\t};\n"
        "/* End XCBuildConfiguration section */\n\t};\n}\n")
    (app / "ios" / "Runner.xcodeproj" / "project.pbxproj").write_text(pbx, encoding="utf-8")
    code = ["import 'package:flutter/material.dart';", "class Auth {"]
    code.append("  Future<void> signUp() async { await api.post('/auth/register'); }")
    if social_login:
        code.append("  final g = GoogleSignIn();")
    if apple_login:
        code.append("  Future<void> apple() => SignInWithApple.getAppleIDCredential(scopes: []);")
    if deletion:
        code.append("  Future<void> deleteAccount() async { await api.delete('/me'); }")
    code.append("  final privacyUrl = 'https://example.com/privacy-policy';")
    code.append("  Future<void> pick() => picker.pickImage(source: ImageSource.gallery);")
    code.append("}")
    (app / "lib" / "auth" / "auth.dart").write_text("\n".join(code) + "\n", encoding="utf-8")
    return app


def tree_digest(root: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out
