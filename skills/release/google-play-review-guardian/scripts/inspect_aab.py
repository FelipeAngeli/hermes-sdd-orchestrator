"""Android App Bundle (.aab) inspection.

The bundle manifest (base/manifest/AndroidManifest.xml) is an aapt2 proto
`XmlNode`. Field numbers verified against frameworks/base/tools/aapt2/Resources.proto
(AOSP main, consulted 2026-10-16):
  XmlNode      { XmlElement element = 1; string text = 2; SourcePosition source = 3; }
  XmlElement   { repeated XmlNamespace namespace_declaration = 1; string namespace_uri = 2;
                 string name = 3; repeated XmlAttribute attribute = 4; repeated XmlNode child = 5; }
  XmlAttribute { string namespace_uri = 1; string name = 2; string value = 3; SourcePosition source = 4;
                 uint32 resource_id = 5; Item compiled_item = 6; }
  Item         { Reference ref = 1; String str = 2; RawString raw_str = 3; ... Primitive prim = 7; }
  Primitive    { ... int32 int_decimal_value = 6; uint32 int_hexadecimal_value = 7; bool boolean_value = 8; }
Official tools (bundletool) are preferred when a Java runtime exists.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from common import (BLOCKER, CRITICAL, INFO, NOT_VERIFIED, PASS, WARNING, FindingFactory, ToolInfo,
                    read_member, run_tool, scan_zip, tool_cmd, tool_env, SecureTempDir, extract_member_safe)
from validate_manifest import Element, attr_key, extract_info


class ProtoError(ValueError):
    pass


def _varint(buf: bytes, p: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if p >= len(buf):
            raise ProtoError("varint truncado")
        b = buf[p]
        p += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, p
        shift += 7
        if shift > 63:
            raise ProtoError("varint longo demais")


def iter_fields(buf: bytes):
    """Yield (field_number, wire_type, value) for a protobuf message."""
    p = 0
    n = len(buf)
    while p < n:
        key, p = _varint(buf, p)
        fno, wt = key >> 3, key & 7
        if fno == 0:
            raise ProtoError("campo 0 inválido")
        if wt == 0:
            v, p = _varint(buf, p)
        elif wt == 1:
            if p + 8 > n:
                raise ProtoError("fixed64 truncado")
            v = buf[p:p + 8]
            p += 8
        elif wt == 2:
            ln, p = _varint(buf, p)
            if p + ln > n:
                raise ProtoError("length-delimited truncado")
            v = buf[p:p + ln]
            p += ln
        elif wt == 5:
            if p + 4 > n:
                raise ProtoError("fixed32 truncado")
            v = buf[p:p + 4]
            p += 4
        else:
            raise ProtoError(f"wire type {wt} não suportado")
        yield fno, wt, v


def _item_value(buf: bytes) -> str | None:
    for fno, wt, v in iter_fields(buf):
        if fno == 1 and wt == 2:  # Reference
            rid = name = None
            for f2, w2, v2 in iter_fields(v):
                if f2 == 2 and w2 == 0:
                    rid = v2
                elif f2 == 3 and w2 == 2:
                    name = v2.decode("utf-8", "replace")
            return f"@{name}" if name else (f"@0x{rid:08x}" if rid is not None else "@null")
        if fno in (2, 3) and wt == 2:  # String / RawString
            for f2, w2, v2 in iter_fields(v):
                if f2 == 1 and w2 == 2:
                    return v2.decode("utf-8", "replace")
            return ""
        if fno == 7 and wt == 2:  # Primitive
            for f2, w2, v2 in iter_fields(v):
                if f2 == 8 and w2 == 0:
                    return "true" if v2 else "false"
                if f2 == 6 and w2 == 0:
                    return str(v2 - (1 << 64) if v2 >= 1 << 63 else v2)
                if f2 == 7 and w2 == 0:
                    return hex(v2)
    return None


def _parse_attr(buf: bytes) -> tuple[str, str]:
    ns = name = value = ""
    rid = None
    compiled = None
    for fno, wt, v in iter_fields(buf):
        if fno == 1 and wt == 2:
            ns = v.decode("utf-8", "replace")
        elif fno == 2 and wt == 2:
            name = v.decode("utf-8", "replace")
        elif fno == 3 and wt == 2:
            value = v.decode("utf-8", "replace")
        elif fno == 5 and wt == 0:
            rid = v
        elif fno == 6 and wt == 2:
            compiled = _item_value(v)
    # Prefer the compiled value for primitives (booleans/ints); keep raw for strings/flags.
    if compiled is not None and (value == "" or compiled in ("true", "false") or compiled.startswith("0x") and not value):
        final = compiled
    else:
        final = value if value != "" else (compiled or "")
    return attr_key(ns, name, rid), final


def _parse_element(buf: bytes, depth: int = 0) -> Element:
    if depth > 200:
        raise ProtoError("aninhamento excessivo")
    el = Element("")
    for fno, wt, v in iter_fields(buf):
        if fno == 3 and wt == 2:
            el.tag = v.decode("utf-8", "replace")
        elif fno == 4 and wt == 2:
            k, val = _parse_attr(v)
            el.attrs[k] = val
        elif fno == 5 and wt == 2:
            child = _parse_node(v, depth + 1)
            if child is not None:
                el.children.append(child)
    return el


def _parse_node(buf: bytes, depth: int = 0) -> Element | None:
    for fno, wt, v in iter_fields(buf):
        if fno == 1 and wt == 2:
            return _parse_element(v, depth)
    return None  # text node


def parse_proto_xml(buf: bytes) -> Element:
    root = _parse_node(buf)
    if root is None or not root.tag:
        raise ProtoError("XmlNode raiz sem elemento")
    return root


def parse_native_pb(buf: bytes) -> list[str]:
    """NativeLibraries { repeated TargetedNativeDirectory directory = 1 { string path = 1; ... } }"""
    out = []
    try:
        for fno, wt, v in iter_fields(buf):
            if fno == 1 and wt == 2:
                for f2, w2, v2 in iter_fields(v):
                    if f2 == 1 and w2 == 2:
                        out.append(v2.decode("utf-8", "replace"))
    except ProtoError:
        pass
    return out


def module_names(entries) -> list[str]:
    mods = set()
    for i in entries:
        m = re.match(r"^([^/]+)/manifest/AndroidManifest\.xml$", i.filename)
        if m:
            mods.add(m.group(1))
    return sorted(mods)


def bundle_signature_files(entries) -> list[str]:
    return [i.filename for i in entries if re.match(r"^META-INF/[^/]+\.(RSA|DSA|EC)$", i.filename, re.I)]


def inspect_aab(path: Path, tools: dict[str, ToolInfo], F: FindingFactory, timeout: int = 180) -> dict:
    res: dict = {"path": str(path), "kind": "aab", "manifest": None, "modules": [], "findings": [],
                 "native_libs": [], "abis": [], "verification": {}}
    fs = res["findings"]
    comp = path.name
    scan = scan_zip(path)
    res["zip"] = scan
    if not scan.ok:
        fs.append(F("TECH-ARTIFACT-INTEGRITY", BLOCKER, "android_build", "AAB corrompido ou inválido",
                    component=comp, evidence=scan.error or "; ".join(f"{i.kind}:{i.entry}" for i in scan.issues),
                    recommendation="Regere com `flutter build appbundle --release` ou `./gradlew bundleRelease`."))
        return res
    unsafe = [i for i in scan.issues if i.kind in ("zip_slip", "absolute_path", "symlink", "zip_bomb", "too_many_entries", "duplicate")]
    if unsafe:
        fs.append(F("SEC-ARCHIVE-SAFETY", CRITICAL, "security", "Bundle contém entradas inseguras",
                    component=comp, evidence="; ".join(f"{i.kind}:{i.entry} {i.detail}".strip() for i in unsafe[:10]),
                    recommendation="Não envie este arquivo; regere o bundle a partir do código-fonte."))
        if any(i.kind in ("zip_bomb", "too_many_entries") for i in unsafe):
            return res
    names = {i.filename: i for i in scan.entries}
    required = ["base/manifest/AndroidManifest.xml", "BundleConfig.pb"]
    missing = [r for r in required if r not in names]
    if missing:
        fs.append(F("TECH-ARTIFACT-INTEGRITY", BLOCKER, "android_build", "Estrutura de App Bundle incompleta",
                    component=comp, evidence="ausente: " + ", ".join(missing),
                    recommendation="Um .aab válido contém BundleConfig.pb e base/manifest/AndroidManifest.xml; regere o bundle."))
        return res
    res["modules"] = module_names(scan.entries)
    with zipfile.ZipFile(path) as zf:
        raw = read_member(zf, names["base/manifest/AndroidManifest.xml"], 16 * 1024 ** 2)
        native_pb = read_member(zf, names["base/native.pb"], 1024 ** 2) if "base/native.pb" in names else b""
    try:
        res["manifest"] = extract_info(parse_proto_xml(raw))
        res["verification"]["manifest"] = "deterministic (decodificador protobuf aapt2 XmlNode em Python)"
    except (ProtoError, UnicodeDecodeError) as exc:
        fs.append(F("TECH-ARTIFACT-INTEGRITY", BLOCKER, "android_build", "Manifest do bundle não é um XmlNode protobuf válido",
                    component=comp, evidence=str(exc), confidence="medium",
                    recommendation="Regere o bundle; o manifest compilado está corrompido."))
    # Cross-check with aapt2 (works on a proto manifest when wrapped in a tiny proto-APK).
    aapt2 = tools.get("aapt2")
    if aapt2 and aapt2.available and res["manifest"] is not None:
        with SecureTempDir() as tmp, zipfile.ZipFile(path) as zf:
            wrapper = tmp / "proto-manifest.apk"
            with zipfile.ZipFile(wrapper, "w") as w:
                w.writestr("AndroidManifest.xml", raw)
            r = run_tool(tool_cmd(aapt2, "dump", "xmltree", "--file", "AndroidManifest.xml", str(wrapper)), timeout=timeout)
            if r.ok:
                m = re.search(r"targetSdkVersion\(0x01010270\)=(\d+)", r.stdout)
                m2 = re.search(r"versionCode\(0x0101021b\)=(\d+)", r.stdout)
                checks = []
                if m:
                    checks.append(("target_sdk", int(m.group(1))))
                if m2:
                    checks.append(("version_code", int(m2.group(1))))
                diffs = [f"{k}: python={res['manifest'].get(k)} aapt2={v}" for k, v in checks if res["manifest"].get(k) != v]
                res["verification"]["manifest"] += ("; confirmado com aapt2 dump xmltree" if not diffs else f"; divergências: {diffs}")
                if diffs:
                    fs.append(F("TECH-ARTIFACT-INTEGRITY", WARNING, "android_build", "Divergência entre parser Python e aapt2",
                                component=comp, evidence="; ".join(diffs), confidence="medium"))
            else:
                res["verification"]["aapt2"] = r.reason()
    bt = tools.get("bundletool")
    if bt and bt.available:
        r = run_tool(tool_cmd(bt, "validate", f"--bundle={path}"), timeout=timeout, env=tool_env(bt))
        res["verification"]["bundletool"] = "bundletool validate: OK" if r.ok else f"bundletool validate falhou: {r.reason()}"
        if not r.ok and r.error == "nonzero":
            fs.append(F("TECH-ARTIFACT-INTEGRITY", BLOCKER, "android_build", "bundletool validate rejeitou o bundle",
                        component=comp, evidence=r.stderr.strip()[:400] or r.stdout.strip()[:400],
                        recommendation=f"Execute `bundletool validate --bundle={path.name}` e corrija os erros."))
    else:
        res["verification"]["bundletool"] = f"NOT_VERIFIED: {bt.reason if bt else 'não descoberto'}"
        fs.append(F("TECH-BUNDLETOOL-VALIDATE", NOT_VERIFIED, "android_build", "bundletool validate não executado",
                    component=comp, evidence=res["verification"]["bundletool"], method="manual",
                    recommendation="Instale um JRE e o bundletool e rode `java -jar bundletool.jar validate --bundle=<app>.aab`."))
    if res["manifest"] is not None:
        fs.append(F("TECH-ARTIFACT-INTEGRITY", PASS, "android_build", "Estrutura do AAB íntegra e manifest legível",
                    component=comp, evidence=f"{len(scan.entries)} entradas; CRC verificado={scan.crc_checked}; "
                    f"módulos={','.join(res['modules'])}; {res['verification']['manifest']}"))
        fs.append(F("TECH-AAB-REQUIRED", PASS, "android_build", "Artefato no formato Android App Bundle",
                    component=comp, evidence="BundleConfig.pb + base/manifest presentes"))
    res["native_libs"] = [i.filename for i in scan.entries if re.match(r"^[^/]+/lib/[^/]+/[^/]+\.so$", i.filename)]
    res["abis"] = sorted({n.split("/")[2] for n in res["native_libs"]})
    res["native_pb_dirs"] = parse_native_pb(native_pb)
    sig = bundle_signature_files(scan.entries)
    res["bundle_signature_files"] = sig
    res["dependencies_metadata"] = "BUNDLE-METADATA/com.android.tools.build.libraries/dependencies.pb" in names
    res["obfuscation_mapping"] = any(n.startswith("BUNDLE-METADATA/com.android.tools.build.obfuscation/") for n in names)
    with zipfile.ZipFile(path) as zf:
        if "base/root/billing.properties" in names:
            txt = read_member(zf, names["base/root/billing.properties"], 4096).decode("utf-8", "replace")
            m = re.search(r"(?m)^(?:billing_client|version)=([0-9.]+)", txt)
            res["billing_library_version"] = m.group(1) if m else None
    return res


# The bundle signature is the *upload* signature; Play re-signs distribution APKs (Play App Signing).
def signing_note() -> str:
    return ("A assinatura do .aab é a da chave de upload; os APKs distribuídos são assinados pelo Google Play "
            "(Play App Signing) com a chave de assinatura do app.")
