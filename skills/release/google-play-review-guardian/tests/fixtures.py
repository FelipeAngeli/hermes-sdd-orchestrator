"""Programmatic fixture builders (no binary fixtures committed).

* encode_proto_manifest  -> aapt2 proto XmlNode bytes (Resources.proto field numbers)
* encode_axml            -> binary AXML (ResChunk format)
* make_elf               -> minimal ELF64/ELF32 with PT_LOAD headers
* make_cert / make_pkcs7 -> DER X.509-shaped certificate inside PKCS#7 SignedData
* make_aab / make_apk    -> zip archives combining the above
* make_flutter_project   -> disposable Flutter project tree
"""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path

ANDROID_NS = "http://schemas.android.com/apk/res/android"
ATTR_RES = {
    "name": 0x01010003, "exported": 0x01010010, "debuggable": 0x0101000F, "versionCode": 0x0101021B,
    "versionName": 0x0101021C, "minSdkVersion": 0x0101020C, "targetSdkVersion": 0x01010270,
    "usesCleartextTraffic": 0x010104EC, "allowBackup": 0x01010280, "foregroundServiceType": 0x01010599,
    "permission": 0x01010006, "scheme": 0x01010027, "host": 0x01010028, "autoVerify": 0x010104EE,
    "label": 0x01010001, "required": 0x0101028E, "compileSdkVersion": 0x01010572,
}


# ---------------------------------------------------------------- manifest model
def manifest_tree(*, package="com.example.app", version_code=45, version_name="2.1.0", min_sdk=24, target_sdk=36,
                  permissions=(), debuggable=None, cleartext=None, components=None, features=()):
    """Element tuples: (tag, {attr: value}, [children]). Android attrs are prefixed 'android:'."""
    app_attrs = {"android:label": "Example App"}
    if debuggable is not None:
        app_attrs["android:debuggable"] = debuggable
    if cleartext is not None:
        app_attrs["android:usesCleartextTraffic"] = cleartext
    comps = components if components is not None else [
        ("activity", {"android:name": ".MainActivity", "android:exported": True}, [
            ("intent-filter", {}, [("action", {"android:name": "android.intent.action.MAIN"}, []),
                                   ("category", {"android:name": "android.intent.category.LAUNCHER"}, [])])])]
    children = [("uses-sdk", {"android:minSdkVersion": min_sdk, "android:targetSdkVersion": target_sdk}, [])]
    children += [("uses-permission", {"android:name": p}, []) for p in permissions]
    children += [("uses-feature", {"android:name": f, "android:required": True}, []) for f in features]
    children.append(("application", app_attrs, comps))
    return ("manifest", {"package": package, "android:versionCode": version_code, "android:versionName": version_name,
                         "android:compileSdkVersion": target_sdk}, children)


def fgs_service(name="com.example.PlayerService", fgs="mediaPlayback", flag=0x02, exported=False):
    return ("service", {"android:name": name, "android:exported": exported, "android:foregroundServiceType": (fgs, flag)}, [])


# ---------------------------------------------------------------- protobuf
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _ld(field: int, payload: bytes) -> bytes:
    return _varint(field << 3 | 2) + _varint(len(payload)) + payload


def _vi(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _proto_attr(key: str, value) -> bytes:
    ns, name = (ANDROID_NS, key.split(":", 1)[1]) if key.startswith("android:") else ("", key)
    out = b""
    if ns:
        out += _ld(1, ns.encode())
    out += _ld(2, name.encode())
    rid = ATTR_RES.get(name) if ns else None
    if isinstance(value, tuple):  # (raw string, int flag) e.g. foregroundServiceType
        raw, flag = value
        out += _ld(3, raw.encode())
        if rid:
            out += _vi(5, rid)
        out += _ld(6, _ld(7, _vi(7, flag)))  # Item.prim.int_hexadecimal_value
    elif isinstance(value, bool):
        out += _ld(3, ("true" if value else "false").encode())
        if rid:
            out += _vi(5, rid)
        out += _ld(6, _ld(7, _vi(8, 1 if value else 0)))  # Item.prim.boolean_value
    elif isinstance(value, int):
        out += _ld(3, str(value).encode())
        if rid:
            out += _vi(5, rid)
        out += _ld(6, _ld(7, _vi(6, value)))  # Item.prim.int_decimal_value
    else:
        out += _ld(3, str(value).encode())
        if rid:
            out += _vi(5, rid)
    return out


def _proto_element(el) -> bytes:
    tag, attrs, children = el
    out = b""
    if tag == "manifest":
        out += _ld(1, _ld(1, b"android") + _ld(2, ANDROID_NS.encode()))  # namespace_declaration
    out += _ld(3, tag.encode())
    for k, v in attrs.items():
        out += _ld(4, _proto_attr(k, v))
    for c in children:
        out += _ld(5, _ld(1, _proto_element(c)))
        out += _ld(5, _ld(2, b"\n    "))  # interleaved text node
    return out


def encode_proto_manifest(tree) -> bytes:
    return _ld(1, _proto_element(tree)) + _ld(3, _vi(1, 1))  # XmlNode{element, source{line}}


# ---------------------------------------------------------------- AXML
def encode_axml(tree) -> bytes:
    strings: list[str] = []
    res_ids: list[int] = []

    def sidx(s: str) -> int:
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    # attribute names with resource ids must come first (resource map is index-aligned)
    def collect(el):
        for k in el[1]:
            if k.startswith("android:"):
                n = k.split(":", 1)[1]
                if n in ATTR_RES and n not in strings:
                    strings.append(n)
                    res_ids.append(ATTR_RES[n])
        for c in el[2]:
            collect(c)

    collect(tree)
    sidx("android")
    sidx(ANDROID_NS)
    body = b""

    def start(el):
        nonlocal body
        tag, attrs, children = el
        attr_bytes = b""
        for k, v in attrs.items():
            if k.startswith("android:"):
                ns_i, name_i = sidx(ANDROID_NS), sidx(k.split(":", 1)[1])
            else:
                ns_i, name_i = -1, sidx(k)
            if isinstance(v, tuple):
                raw_i, dtype, data = sidx(v[0]), 0x11, v[1]
            elif isinstance(v, bool):
                raw_i, dtype, data = -1, 0x12, 0xFFFFFFFF if v else 0
            elif isinstance(v, int):
                raw_i, dtype, data = -1, 0x10, v & 0xFFFFFFFF
            else:
                raw_i = sidx(str(v))
                dtype, data = 0x03, raw_i
            attr_bytes += struct.pack("<iiiHBBI", ns_i, name_i, raw_i, 8, 0, dtype, data)
        ext = struct.pack("<iiHHHHHH", -1, sidx(tag), 20, 20, len(attrs), 0, 0, 0)
        chunk = ext + attr_bytes
        body += struct.pack("<HHIIi", 0x0102, 16, 16 + len(chunk), 1, -1) + chunk
        for c in children:
            start(c)
        endext = struct.pack("<ii", -1, sidx(tag))
        body += struct.pack("<HHIIi", 0x0103, 16, 16 + len(endext), 1, -1) + endext

    ns_ext = lambda: struct.pack("<ii", sidx("android"), sidx(ANDROID_NS))
    start_ns = struct.pack("<HHIIi", 0x0100, 16, 24, 1, -1) + ns_ext()
    start(tree)
    end_ns = struct.pack("<HHIIi", 0x0101, 16, 24, 1, -1) + ns_ext()
    # string pool (UTF-16)
    offsets, data = [], b""
    for s in strings:
        offsets.append(len(data))
        enc = s.encode("utf-16-le")
        data += struct.pack("<H", len(s)) + enc + b"\x00\x00"
    while len(data) % 4:
        data += b"\x00"
    hdr = 28
    pool = struct.pack("<IIIII", len(strings), 0, 0, hdr + 4 * len(strings), 0)
    pool_chunk = struct.pack("<HHI", 0x0001, hdr, hdr + 4 * len(strings) + len(data)) + pool + \
        struct.pack(f"<{len(strings)}I", *offsets) + data
    resmap = struct.pack("<HHI", 0x0180, 8, 8 + 4 * len(res_ids)) + struct.pack(f"<{len(res_ids)}I", *res_ids)
    payload = pool_chunk + resmap + start_ns + body + end_ns
    return struct.pack("<HHI", 0x0003, 8, 8 + len(payload)) + payload


# ---------------------------------------------------------------- ELF
def make_elf(align: int = 0x4000, bits: int = 64, machine: int = 183, nloads: int = 2,
             relro: tuple | None = None) -> bytes:
    """relro=(vaddr, memsz) adds a PT_GNU_RELRO; loads are RX then RW at vaddr 0, 0x10000."""
    phs = []
    for i in range(nloads):
        phs.append((1, 0x5 if i == 0 else 0x6, i * 0x1000, i * 0x10000, 0x800, 0x2000))
    if relro:
        phs.append((0x6474E552, 0x4, 0x1000, relro[0], relro[1], relro[1]))
    if bits == 64:
        ehdr = b"\x7fELF" + bytes([2, 1, 1]) + b"\x00" * 9 + struct.pack(
            "<HHIQQQIHHHHHH", 3, machine, 1, 0, 64, 0, 0, 64, 56, len(phs), 0, 0, 0)
        ph = b"".join(struct.pack("<IIQQQQQQ", t, fl, off, va, va, fs, ms, align if t == 1 else 1)
                      for t, fl, off, va, fs, ms in phs)
    else:
        ehdr = b"\x7fELF" + bytes([1, 1, 1]) + b"\x00" * 9 + struct.pack(
            "<HHIIIIIHHHHHH", 3, machine, 1, 0, 52, 0, 0, 52, 32, len(phs), 0, 0, 0)
        ph = b"".join(struct.pack("<IIIIIIII", t, off, va, va, fs, ms, fl, align if t == 1 else 1)
                      for t, fl, off, va, fs, ms in phs)
    return ehdr + ph + b"\x00" * 256


# ---------------------------------------------------------------- DER / PKCS7
def _der(tag: int, content: bytes) -> bytes:
    n = len(content)
    if n < 0x80:
        ln = bytes([n])
    elif n < 0x100:
        ln = b"\x81" + bytes([n])
    else:
        ln = b"\x82" + n.to_bytes(2, "big")
    return bytes([tag]) + ln + content


def _name(cn: str) -> bytes:
    return _der(0x30, _der(0x31, _der(0x30, _der(0x06, bytes.fromhex("550403")) + _der(0x0C, cn.encode()))))


def make_cert(cn: str = "Android Debug", key_bits: int = 2048) -> bytes:
    modulus = b"\x00" + b"\xC3" + b"\x11" * (key_bits // 8 - 1)
    rsa_pub = _der(0x30, _der(0x02, modulus) + _der(0x02, b"\x01\x00\x01"))
    spki = _der(0x30, _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010101")) + b"\x05\x00") + _der(0x03, b"\x00" + rsa_pub))
    alg = _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d01010b")) + b"\x05\x00")
    validity = _der(0x30, _der(0x17, b"200101000000Z") + _der(0x17, b"500101000000Z"))
    tbs = _der(0x30, _der(0xA0, _der(0x02, b"\x02")) + _der(0x02, b"\x01") + alg + _name(cn) + validity + _name(cn) + spki)
    return _der(0x30, tbs + alg + _der(0x03, b"\x00" + b"\xAA" * 32))


def make_pkcs7(cert: bytes) -> bytes:
    signed_data = _der(0x30, _der(0x02, b"\x01") + _der(0x31, b"") + _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010701")))
                       + _der(0xA0, cert) + _der(0x31, b""))
    return _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010702")) + _der(0xA0, signed_data))


# ---------------------------------------------------------------- archives
def make_aab(path: Path, tree=None, *, libs: dict | None = None, signer_cn: str | None = "Upload Key",
             billing: str | None = None, extra: dict | None = None, skip_bundle_config=False) -> Path:
    tree = tree or manifest_tree()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if not skip_bundle_config:
            z.writestr("BundleConfig.pb", _ld(1, _ld(1, b"1.18.1")))
        z.writestr("base/manifest/AndroidManifest.xml", encode_proto_manifest(tree))
        z.writestr("base/dex/classes.dex", b"dex\n035\x00" + b"\x00" * 100)
        z.writestr("base/resources.pb", b"")
        for name, data in (libs or {}).items():
            z.writestr(f"base/lib/{name}", data)
        if billing:
            z.writestr("base/root/billing.properties", f"version={billing}\nclient=billing\nbilling_client={billing}\n")
        if signer_cn:
            z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
            z.writestr("META-INF/KEY.RSA", make_pkcs7(make_cert(signer_cn)))
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return path


def _zip_add_aligned(z: zipfile.ZipFile, name: str, data: bytes, align: int) -> None:
    """Store `data` uncompressed with its payload starting at a multiple of `align` (extra-field padding)."""
    fp = z.fp
    offset = fp.tell() if fp else 0
    base = offset + 30 + len(name.encode())
    pad = (-base) % align
    if 0 < pad < 4:
        pad += align
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    if pad:
        info.extra = struct.pack("<HH", 0xD935, pad - 4) + b"\x00" * (pad - 4)
    z.writestr(info, data)


def make_apk(path: Path, tree=None, *, libs: dict | None = None, align_libs: int | None = 16384,
             signer_cn: str | None = "Release Key") -> Path:
    tree = tree or manifest_tree()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", encode_axml(tree))
        z.writestr("classes.dex", b"dex\n035\x00" + b"\x00" * 100)
        for name, data in (libs or {}).items():
            if align_libs:
                _zip_add_aligned(z, f"lib/{name}", data, align_libs)
            else:
                info = zipfile.ZipInfo(f"lib/{name}", date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                # force misalignment: ensure payload offset % 16384 != 0
                fp = z.fp
                base = (fp.tell() if fp else 0) + 30 + len(info.filename.encode())
                if base % 16384 == 0:
                    info.extra = struct.pack("<HH", 0xD935, 0)
                z.writestr(info, data)
        if signer_cn:
            z.writestr("META-INF/CERT.RSA", make_pkcs7(make_cert(signer_cn)))
    return path


def make_flutter_project(root: Path, *, kts=False, target_sdk="36", min_sdk="24", debuggable=False, minify=True,
                         key_properties=True, manifest_tree_=None, extra_dart="") -> Path:
    (root / "android/app/src/main/res/xml").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "pubspec.yaml").write_text(
        "name: demo\nversion: 2.1.0+45\nenvironment:\n  sdk: '>=3.3.0 <4.0.0'\n  flutter: '>=3.22.0'\n"
        "dependencies:\n  flutter:\n    sdk: flutter\n  firebase_analytics: ^11.0.0\n  in_app_purchase: ^3.2.0\n"
        "dev_dependencies:\n  flutter_test:\n    sdk: flutter\n")
    (root / "pubspec.lock").write_text(
        'packages:\n  firebase_analytics:\n    dependency: "direct main"\n    source: hosted\n    version: "11.3.0"\n'
        '  in_app_purchase:\n    dependency: "direct main"\n    source: hosted\n    version: "3.2.0"\n'
        'sdks:\n  dart: ">=3.5.0 <4.0.0"\n  flutter: ">=3.24.0"\n')
    if kts:
        rel = (f'        getByName("release") {{\n            isMinifyEnabled = {str(minify).lower()}\n'
               + ('            isDebuggable = true\n' if debuggable else '') + '            signingConfig = signingConfigs.getByName("release")\n        }\n')
        (root / "android/app/build.gradle.kts").write_text(
            'plugins { id("com.android.application") }\nandroid {\n    namespace = "com.example.app"\n    compileSdk = 36\n'
            f'    defaultConfig {{\n        applicationId = "com.example.app"\n        minSdk = {min_sdk}\n        targetSdk = {target_sdk}\n'
            '        versionCode = 45\n        versionName = "2.1.0"\n    }\n    buildTypes {\n' + rel + '    }\n}\n')
    else:
        rel = (f"        release {{\n            minifyEnabled {str(minify).lower()}\n"
               + ("            debuggable true\n" if debuggable else "") + "            signingConfig signingConfigs.release\n        }\n")
        (root / "android/app/build.gradle").write_text(
            "plugins { id 'com.android.application' }\nandroid {\n    namespace 'com.example.app'\n    compileSdkVersion 36\n"
            f"    defaultConfig {{\n        applicationId 'com.example.app'\n        minSdkVersion {min_sdk}\n        targetSdkVersion {target_sdk}\n"
            "    }\n    buildTypes {\n" + rel + "    }\n}\n")
    (root / "android/settings.gradle").write_text('plugins {\n    id "com.android.application" version "8.7.0" apply false\n}\n')
    (root / "android/app/src/main/AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '  <uses-permission android:name="android.permission.INTERNET"/>\n'
        '  <uses-permission android:name="android.permission.ACCESS_BACKGROUND_LOCATION"/>\n'
        '  <application android:label="Demo" android:usesCleartextTraffic="true">\n'
        '    <activity android:name=".MainActivity" android:exported="true">\n'
        '      <intent-filter><action android:name="android.intent.action.MAIN"/>'
        '<category android:name="android.intent.category.LAUNCHER"/></intent-filter>\n'
        '    </activity>\n  </application>\n</manifest>\n')
    if key_properties:
        (root / "android/key.properties").write_text("storePassword=SuperSecretValue123\nkeyPassword=AnotherSecret456\nkeyAlias=upload\nstoreFile=upload.jks\n")
        (root / "android/upload.jks").write_bytes(b"\xfe\xed\xfe\xed" + b"SECRETKEYSTORE" * 4)
    (root / "lib/main.dart").write_text(
        "void main() {}\n// signUp flow\nFuture<void> signUp() async {}\n" + extra_dart)
    return root
