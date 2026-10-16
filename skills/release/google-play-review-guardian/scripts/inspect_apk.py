"""APK inspection: binary AXML manifest parser + official-tool cross-checks.

Pure-Python AXML parser follows the documented ResChunk format
(frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h):
  RES_XML_TYPE 0x0003, RES_STRING_POOL_TYPE 0x0001, RES_XML_RESOURCE_MAP_TYPE 0x0180,
  START_NAMESPACE 0x0100, END_NAMESPACE 0x0101, START_ELEMENT 0x0102, END_ELEMENT 0x0103, CDATA 0x0104.
Nothing from the archive is ever executed.
"""
from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path

from common import (BLOCKER, CRITICAL, NOT_VERIFIED, PASS, WARNING, INFO, FindingFactory, ToolInfo,
                    read_member, run_tool, scan_zip, tool_cmd)
from validate_manifest import Element, attr_key, extract_info

RES_STRING_POOL = 0x0001
RES_XML = 0x0003
RES_XML_START_NS = 0x0100
RES_XML_END_NS = 0x0101
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103
RES_XML_RESOURCE_MAP = 0x0180
UTF8_FLAG = 0x100

TYPE_REFERENCE = 0x01
TYPE_ATTRIBUTE = 0x02
TYPE_STRING = 0x03
TYPE_FLOAT = 0x04
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
TYPE_INT_BOOLEAN = 0x12


class AXMLError(ValueError):
    pass


def _parse_string_pool(buf: bytes, off: int) -> list[str]:
    ctype, hsize, size = struct.unpack_from("<HHI", buf, off)
    if ctype != RES_STRING_POOL:
        raise AXMLError("string pool esperado")
    count, _styles, flags, strings_start, _ = struct.unpack_from("<IIIII", buf, off + 8)
    if count > 1_000_000 or off + size > len(buf):
        raise AXMLError("string pool inválido")
    utf8 = bool(flags & UTF8_FLAG)
    offsets = struct.unpack_from(f"<{count}I", buf, off + hsize)
    base = off + strings_start
    out = []
    for so in offsets:
        p = base + so
        if p >= off + size:
            out.append("")
            continue
        if utf8:
            n = buf[p]
            p += 2 if n & 0x80 else 1  # utf16 length (skipped)
            blen = buf[p]
            if blen & 0x80:
                blen = ((blen & 0x7F) << 8) | buf[p + 1]
                p += 2
            else:
                p += 1
            out.append(buf[p:p + blen].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", buf, p)[0]
            p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", buf, p)[0]
                p += 2
            out.append(buf[p:p + n * 2].decode("utf-16-le", "replace"))
    return out


def _value_str(dtype: int, data: int, raw: str | None, strings: list[str]) -> str:
    if raw is not None:
        return raw
    if dtype == TYPE_STRING:
        return strings[data] if data < len(strings) else ""
    if dtype == TYPE_INT_BOOLEAN:
        return "true" if data != 0 else "false"
    if dtype == TYPE_INT_DEC:
        return str(struct.unpack("<i", struct.pack("<I", data))[0])
    if dtype == TYPE_INT_HEX:
        return hex(data)
    if dtype == TYPE_REFERENCE:
        return f"@0x{data:08x}"
    if dtype == TYPE_ATTRIBUTE:
        return f"?0x{data:08x}"
    if dtype == TYPE_FLOAT:
        return str(struct.unpack("<f", struct.pack("<I", data))[0])
    return hex(data)


def parse_axml(buf: bytes) -> Element:
    """Parse binary AndroidManifest.xml into an Element tree."""
    if len(buf) < 8:
        raise AXMLError("arquivo muito curto")
    ctype, hsize, size = struct.unpack_from("<HHI", buf, 0)
    if ctype != RES_XML:
        raise AXMLError(f"não é AXML (tipo 0x{ctype:04x})")
    strings: list[str] = []
    resmap: list[int] = []
    stack: list[Element] = []
    root: Element | None = None
    off = hsize
    end = min(size, len(buf))
    guard = 0
    while off + 8 <= end:
        guard += 1
        if guard > 2_000_000:
            raise AXMLError("chunks demais")
        ctype, chsize, csize = struct.unpack_from("<HHI", buf, off)
        if csize < 8 or off + csize > end:
            raise AXMLError("chunk com tamanho inválido")
        if ctype == RES_STRING_POOL:
            strings = _parse_string_pool(buf, off)
        elif ctype == RES_XML_RESOURCE_MAP:
            n = (csize - chsize) // 4
            resmap = list(struct.unpack_from(f"<{n}I", buf, off + chsize))
        elif ctype == RES_XML_START_ELEMENT:
            ext = off + chsize
            ns_i, name_i, attr_start, attr_size, attr_count = struct.unpack_from("<iiHHH", buf, ext)
            tag = strings[name_i] if 0 <= name_i < len(strings) else "?"
            el = Element(tag)
            p = ext + attr_start
            for _ in range(attr_count):
                a_ns, a_name, a_raw, _vsize, _res0, dtype, data = struct.unpack_from("<iiiHBBI", buf, p)
                p += attr_size or 20
                ns_uri = strings[a_ns] if 0 <= a_ns < len(strings) else ""
                name = strings[a_name] if 0 <= a_name < len(strings) else ""
                res_id = resmap[a_name] if 0 <= a_name < len(resmap) else None
                raw = strings[a_raw] if 0 <= a_raw < len(strings) else None
                el.attrs[attr_key(ns_uri, name, res_id)] = _value_str(dtype, data, raw, strings)
            if stack:
                stack[-1].children.append(el)
            elif root is None:
                root = el
            stack.append(el)
        elif ctype == RES_XML_END_ELEMENT:
            if stack:
                stack.pop()
        off += csize
    if root is None:
        raise AXMLError("nenhum elemento raiz")
    return root


BADGING_RE = {
    "package": re.compile(r"package: name='([^']*)'"),
    "version_code": re.compile(r"versionCode='(\d+)'"),
    "version_name": re.compile(r"versionName='([^']*)'"),
    "min_sdk": re.compile(r"(?:minSdkVersion|sdkVersion):'(\d+)'"),
    "target_sdk": re.compile(r"targetSdkVersion:'(\d+)'"),
    "compile_sdk": re.compile(r"compileSdkVersion='(\d+)'"),
}


def parse_badging(text: str) -> dict:
    out = {}
    for k, rx in BADGING_RE.items():
        m = rx.search(text)
        if m:
            out[k] = int(m.group(1)) if k not in ("package", "version_name") else m.group(1)
    out["permissions"] = re.findall(r"uses-permission: name='([^']*)'", text)
    return out


def cross_check(py: dict, tool: dict) -> list[str]:
    diffs = []
    for k in ("package", "version_code", "version_name", "min_sdk", "target_sdk"):
        if k in tool and py.get(k) != tool[k]:
            diffs.append(f"{k}: python={py.get(k)!r} aapt2={tool[k]!r}")
    return diffs


def inspect_apk(path: Path, tools: dict[str, ToolInfo], F: FindingFactory, timeout: int = 120) -> dict:
    """Inspect an APK. Returns {'manifest': info|None, 'zip': scan, 'findings': [...], ...}."""
    res: dict = {"path": str(path), "kind": "apk", "manifest": None, "findings": [], "native_libs": [],
                 "verification": {}}
    fs = res["findings"]
    scan = scan_zip(path)
    res["zip"] = scan
    comp = path.name
    if not scan.ok:
        fs.append(F("TECH-ARTIFACT-INTEGRITY", BLOCKER, "android_build", "APK corrompido ou inválido",
                    component=comp, evidence=scan.error or "; ".join(f"{i.kind}:{i.entry}" for i in scan.issues),
                    recommendation="Regere o artefato com o Gradle/Flutter e verifique o download/cópia."))
        return res
    unsafe = [i for i in scan.issues if i.kind in ("zip_slip", "absolute_path", "symlink", "zip_bomb", "too_many_entries", "duplicate")]
    if unsafe:
        fs.append(F("SEC-ARCHIVE-SAFETY", CRITICAL, "security", "Arquivo contém entradas inseguras",
                    component=comp, evidence="; ".join(f"{i.kind}:{i.entry} {i.detail}".strip() for i in unsafe[:10]),
                    recommendation="Não publique nem extraia este arquivo; regere o artefato a partir do código-fonte."))
        if any(i.kind in ("zip_bomb", "too_many_entries") for i in unsafe):
            return res
    names = {i.filename: i for i in scan.entries}
    if "AndroidManifest.xml" not in names:
        fs.append(F("TECH-ARTIFACT-INTEGRITY", BLOCKER, "android_build", "APK sem AndroidManifest.xml",
                    component=comp, evidence="entrada AndroidManifest.xml ausente"))
        return res
    with zipfile.ZipFile(path) as zf:
        raw = read_member(zf, names["AndroidManifest.xml"], 16 * 1024 ** 2)
    try:
        info = extract_info(parse_axml(raw))
        res["manifest"] = info
        res["verification"]["manifest"] = "deterministic (parser AXML em Python)"
    except (AXMLError, struct.error, IndexError) as exc:
        fs.append(F("TECH-ARTIFACT-INTEGRITY", WARNING, "android_build", "Manifest binário não pôde ser decodificado em Python",
                    component=comp, evidence=str(exc), confidence="medium"))
    aapt2 = tools.get("aapt2")
    if aapt2 and aapt2.available:
        r = run_tool(tool_cmd(aapt2, "dump", "badging", str(path)), timeout=timeout)
        if r.ok:
            badging = parse_badging(r.stdout)
            res["badging"] = badging
            if res["manifest"] is None:
                res["manifest"] = {**{k: badging.get(k) for k in ("package", "version_code", "version_name", "min_sdk", "target_sdk", "compile_sdk")},
                                   "permissions": [{"name": p, "max_sdk": None, "flags": None} for p in badging["permissions"]],
                                   "features": [], "components": [], "application": {}, "queries": [], "meta_data": []}
                res["verification"]["manifest"] = "deterministic (aapt2 dump badging)"
            else:
                diffs = cross_check(res["manifest"], badging)
                res["verification"]["manifest"] += "; confirmado com aapt2 dump badging" if not diffs else f"; divergências aapt2: {diffs}"
                if diffs:
                    fs.append(F("TECH-ARTIFACT-INTEGRITY", WARNING, "android_build", "Divergência entre parser Python e aapt2",
                                component=comp, evidence="; ".join(diffs), confidence="medium",
                                recommendation="Prefira os valores do aapt2; reporte o caso para correção do parser."))
        else:
            res["verification"]["aapt2"] = r.reason()
    else:
        res["verification"]["aapt2"] = aapt2.reason if aapt2 else "não descoberto"
    if res["manifest"] is not None:
        fs.append(F("TECH-ARTIFACT-INTEGRITY", PASS, "android_build", "Estrutura do APK íntegra e manifest legível",
                    component=comp, evidence=f"{len(scan.entries)} entradas; CRC verificado={scan.crc_checked}; "
                    f"manifest: {res['verification'].get('manifest')}"))
    res["native_libs"] = [i.filename for i in scan.entries if i.filename.startswith("lib/") and i.filename.endswith(".so")]
    res["dex_count"] = sum(1 for n in names if re.match(r"classes\d*\.dex$", n))
    fs.append(F("TECH-AAB-REQUIRED", INFO, "android_build", "APK não substitui o AAB para novos apps",
                component=comp, evidence="artefato analisado é APK",
                recommendation="Para publicação, envie o Android App Bundle (.aab). Use o APK apenas para testes/sideload."))
    return res

