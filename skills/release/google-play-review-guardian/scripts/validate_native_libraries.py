"""16 KB page-size compatibility of native libraries.

Procedure from https://developer.android.com/guide/practices/page-sizes (consulted 2026-10-16):
  1. every PT_LOAD segment of each .so must have p_align >= 2**14 (0x4000)
     (official: `llvm-objdump -p lib.so | grep LOAD`, or `llvm-readelf -Wl`);
  2. uncompressed .so inside an APK must start at a 16 KB-aligned offset
     (official: `zipalign -c -P 16 -v 4 app.apk`).
Python fallback parses ELF headers in memory (never executes anything) and
computes the ZIP local-header data offset.
"""
from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path

from common import (BLOCKER, INFO, NOT_APPLICABLE, NOT_VERIFIED, PASS, WARNING, FindingFactory, ToolInfo,
                    SecureTempDir, extract_member_safe, local_data_offset, run_tool, tool_cmd)

PT_LOAD = 1
PT_GNU_RELRO = 0x6474E552
PAGE_16K = 0x4000
EM = {3: "x86", 40: "arm", 62: "x86_64", 183: "aarch64", 243: "riscv"}
ABI_64 = ("arm64-v8a", "x86_64", "riscv64")


class ELFError(ValueError):
    pass


def parse_elf_segments(data: bytes) -> dict:
    """Return {'class': 32|64, 'machine': str, 'loads': [{'offset','vaddr','memsz','align'}], 'relro': [...]}"""
    if len(data) < 52 or data[:4] != b"\x7fELF":
        raise ELFError("não é ELF")
    ei_class, ei_data = data[4], data[5]
    if ei_data != 1:
        raise ELFError("ELF big-endian não suportado")
    if ei_class == 2:
        if len(data) < 64:
            raise ELFError("cabeçalho ELF64 truncado")
        e_machine = struct.unpack_from("<H", data, 18)[0]
        e_phoff = struct.unpack_from("<Q", data, 32)[0]
        e_phentsize, e_phnum = struct.unpack_from("<HH", data, 54)
        fmt, size = "<IIQQQQQQ", 56
    elif ei_class == 1:
        e_machine = struct.unpack_from("<H", data, 18)[0]
        e_phoff = struct.unpack_from("<I", data, 28)[0]
        e_phentsize, e_phnum = struct.unpack_from("<HH", data, 42)
        fmt, size = "<IIIIIIII", 32
    else:
        raise ELFError("EI_CLASS inválido")
    if e_phnum > 4096 or e_phentsize < size or e_phoff + e_phnum * e_phentsize > len(data):
        raise ELFError("program headers fora do arquivo")
    loads, relro = [], []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        v = struct.unpack_from(fmt, data, off)
        if ei_class == 2:
            p_type, p_flags, p_offset, p_vaddr, _paddr, _filesz, p_memsz, p_align = v
        else:
            p_type, p_offset, p_vaddr, _paddr, _filesz, p_memsz, p_flags, p_align = v
        seg = {"offset": p_offset, "vaddr": p_vaddr, "memsz": p_memsz, "align": p_align, "writable": bool(p_flags & 0x2)}
        if p_type == PT_LOAD:
            loads.append(seg)
        elif p_type == PT_GNU_RELRO:
            relro.append(seg)
    return {"class": 64 if ei_class == 2 else 32, "machine": EM.get(e_machine, str(e_machine)), "loads": loads, "relro": relro}


def elf_16kb_ok(seg: dict) -> tuple[bool, str]:
    loads = seg["loads"]
    if not loads:
        return False, "sem segmentos PT_LOAD"
    bad = [l for l in loads if l["align"] < PAGE_16K]
    if bad:
        return False, "PT_LOAD align " + ", ".join(f"0x{l['align']:x}" for l in bad) + " < 0x4000"
    return True, "PT_LOAD align " + ",".join(sorted({f"0x{l['align']:x}" for l in loads}))


def relro_16kb_ok(seg: dict) -> tuple[bool, str]:
    """RELRO check from the page-sizes guide.

    The linker makes [page_down(relro_start), page_up(relro_end)) read-only. With 16 KB pages
    that is a problem only when writable (non-RELRO) data of a RW PT_LOAD lies inside
    [relro_end, page_up_16k(relro_end)). (VirtAddr + MemSiz) % 0x4000 == 0 is the trivial pass;
    a RELRO that owns its LOAD segment is also compliant (official examples)."""
    for r in seg.get("relro", []):
        end = r["vaddr"] + r["memsz"]
        if end % PAGE_16K == 0:
            continue
        window_end = (end + PAGE_16K - 1) & ~(PAGE_16K - 1)
        overlap = [l for l in seg["loads"] if l.get("writable")
                   and max(l["vaddr"], end) < min(l["vaddr"] + l["memsz"], window_end)]
        if overlap:
            return False, (f"GNU_RELRO termina em 0x{end:x} (mod 0x4000 = 0x{end % PAGE_16K:x}) e há dados graváveis "
                           f"em [0x{end:x}, 0x{window_end:x})")
    return True, "RELRO ok" if seg.get("relro") else "sem GNU_RELRO"


def parse_readelf_loads(text: str) -> list[int]:
    aligns = []
    for line in text.splitlines():
        if line.strip().startswith("LOAD"):
            m = re.search(r"(0x[0-9a-fA-F]+)\s*$", line.strip())
            if m:
                aligns.append(int(m.group(1), 16))
    return aligns


def abi_of(name: str) -> str:
    parts = name.split("/")
    return parts[-2] if len(parts) >= 2 else "?"


def validate_native_libraries(path: Path, kind: str, tools: dict[str, ToolInfo], F: FindingFactory,
                              target_sdk: int | None, timeout: int = 120, use_tools: bool = True) -> dict:
    fs = []
    comp = path.name
    libs_out = []
    pattern = re.compile(r"^lib/[^/]+/[^/]+\.so$" if kind == "apk" else r"^[^/]+/lib/[^/]+/[^/]+\.so$")
    readelf = tools.get("llvm-readelf") if use_tools else None
    with zipfile.ZipFile(path) as zf:
        so_entries = [i for i in zf.infolist() if pattern.match(i.filename)]
        if not so_entries:
            fs.append(F("TECH-16KB-PAGE-SIZE", NOT_APPLICABLE, "page_size_16kb", "Sem bibliotecas nativas (.so)",
                        component=comp, evidence="nenhuma entrada lib/<abi>/*.so no artefato",
                        recommendation="-"))
            return {"libs": [], "abis": [], "findings": fs}
        with SecureTempDir() as tmp:
            for idx, info in enumerate(so_entries):
                rec = {"name": info.filename, "abi": abi_of(info.filename), "size": info.file_size,
                       "compressed": info.compress_type != zipfile.ZIP_STORED}
                try:
                    with zf.open(info) as fh:
                        head = fh.read(min(info.file_size, 2 * 1024 * 1024))
                    seg = parse_elf_segments(head)
                    ok, why = elf_16kb_ok(seg)
                    rok, rwhy = relro_16kb_ok(seg)
                    rec.update({"elf_class": seg["class"], "machine": seg["machine"], "elf_16kb": ok, "elf_detail": why,
                                "relro_16kb": rok, "relro_detail": rwhy, "elf_method": "python-elf"})
                except (ELFError, struct.error) as exc:
                    rec.update({"elf_16kb": None, "elf_detail": f"ELF ilegível: {exc}", "elf_method": "python-elf"})
                if readelf and readelf.available and rec.get("elf_16kb") is not None:
                    try:
                        local = extract_member_safe(zf, info, tmp, idx)
                        r = run_tool(tool_cmd(readelf, "-Wl", str(local)), timeout=timeout)
                        if r.ok:
                            aligns = parse_readelf_loads(r.stdout)
                            tool_ok = bool(aligns) and all(a >= PAGE_16K for a in aligns)
                            rec["elf_method"] = "python-elf + llvm-readelf"
                            if tool_ok != rec["elf_16kb"]:
                                rec["elf_detail"] += f" (DIVERGÊNCIA llvm-readelf: {[hex(a) for a in aligns]})"
                                rec["elf_16kb"] = tool_ok
                        local.unlink(missing_ok=True)
                    except (ValueError, OSError) as exc:
                        rec["elf_detail"] += f" (llvm-readelf não executado: {exc})"
                if kind == "apk" and not rec["compressed"]:
                    try:
                        off = local_data_offset(path, info)
                        rec["zip_offset"] = off
                        rec["zip_16kb"] = off % PAGE_16K == 0
                    except (ValueError, OSError) as exc:
                        rec["zip_16kb"] = None
                        rec["zip_detail"] = str(exc)
                libs_out.append(rec)
    abis = sorted({l["abi"] for l in libs_out})
    res = {"libs": libs_out, "abis": abis, "findings": fs}

    # 64-bit requirement
    has32 = any(a in ("armeabi-v7a", "x86") for a in abis)
    has64 = any(a in ABI_64 for a in abis)
    if has32 and not has64:
        fs.append(F("TECH-64BIT", BLOCKER, "android_build", "Bibliotecas nativas sem versão 64-bit",
                    component=comp, evidence=f"ABIs: {', '.join(abis)}",
                    recommendation="Inclua arm64-v8a (e x86_64 se aplicável) no build (abiFilters / ndk)."))
    else:
        fs.append(F("TECH-64BIT", PASS, "android_build", "ABIs 64-bit presentes",
                    component=comp, evidence=f"ABIs: {', '.join(abis)}"))

    applies = target_sdk is None or target_sdk >= 35
    libs64 = [l for l in libs_out if l["abi"] in ABI_64]
    bad_elf = [l for l in libs64 if l.get("elf_16kb") is False]
    unknown = [l for l in libs64 if l.get("elf_16kb") is None]
    good = [l for l in libs64 if l.get("elf_16kb") is True]
    method_note = "; ".join(sorted({l["elf_method"] for l in libs64})) or "python-elf"
    if bad_elf:
        sev = BLOCKER if applies and target_sdk is not None else WARNING
        fs.append(F("TECH-16KB-PAGE-SIZE", sev, "page_size_16kb",
                    f"{len(bad_elf)} biblioteca(s) 64-bit sem alinhamento ELF de 16 KB",
                    component=comp, evidence="; ".join(f"{l['name']}: {l['elf_detail']}" for l in bad_elf[:10]) + f" [{method_note}]",
                    recommendation="Recompile com NDK r28+ (16 KB por padrão) ou `-Wl,-z,max-page-size=16384`; atualize SDKs de terceiros que fornecem a .so; AGP ≥ 8.5.1."))
    bad_relro = [l for l in libs64 if l.get("elf_16kb") and l.get("relro_16kb") is False]
    if bad_relro:
        fs.append(F("TECH-16KB-PAGE-SIZE", WARNING, "page_size_16kb", f"{len(bad_relro)} biblioteca(s) com GNU_RELRO não alinhado a 16 KB",
                    component=comp, confidence="medium",
                    evidence="; ".join(f"{l['name']}: {l['relro_detail']}" for l in bad_relro[:10]),
                    recommendation="Recompile com NDK r28+ ou flags de 16 KB (o RELRO desalinhado pode causar segfault em dispositivos 16 KB); confirme com `llvm-readelf -Wl <lib>.so`."))
    if unknown:
        fs.append(F("TECH-16KB-PAGE-SIZE", NOT_VERIFIED, "page_size_16kb", "Bibliotecas nativas não puderam ser analisadas",
                    component=comp, evidence="; ".join(f"{l['name']}: {l['elf_detail']}" for l in unknown[:10]), method="manual",
                    recommendation="Verifique com `llvm-objdump -p <lib>.so | grep LOAD` (NDK)."))
    if good and not bad_elf:
        fs.append(F("TECH-16KB-PAGE-SIZE", PASS, "page_size_16kb",
                    f"Segmentos ELF PT_LOAD alinhados a 16 KB ({len(good)} libs 64-bit)",
                    component=comp, evidence=f"{len(good)}/{len(libs64)} libs 64-bit com p_align ≥ 0x4000 [{method_note}]"))
    if kind == "apk":
        stored = [l for l in libs64 if not l["compressed"]]
        mis = [l for l in stored if l.get("zip_16kb") is False]
        if mis:
            fs.append(F("TECH-16KB-ZIP-ALIGN", BLOCKER if applies and target_sdk is not None else WARNING, "page_size_16kb",
                        f"{len(mis)} .so não comprimida(s) fora do alinhamento de 16 KB no APK",
                        component=comp, evidence="; ".join(f"{l['name']} offset={l['zip_offset']} (mod 16384 = {l['zip_offset'] % PAGE_16K})" for l in mis[:10]),
                        recommendation="Use AGP ≥ 8.5.1 (alinha 16 KB) ou `zipalign -P 16 -f -v 4 in.apk out.apk`; verifique com `zipalign -c -P 16 -v 4 app.apk`."))
        elif stored:
            fs.append(F("TECH-16KB-ZIP-ALIGN", PASS, "page_size_16kb", "Offsets ZIP das .so alinhados a 16 KB",
                        component=comp, evidence=f"{len(stored)} .so 64-bit não comprimidas com offset % 16384 == 0 [python-zip]"))
        compressed = [l for l in libs64 if l["compressed"]]
        if compressed and not stored:
            fs.append(F("TECH-16KB-ZIP-ALIGN", INFO, "page_size_16kb", ".so comprimidas no APK (extraídas na instalação)",
                        component=comp, evidence=f"{len(compressed)} libs comprimidas",
                        recommendation="Com AGP ≥ 8.5.1 prefira libs não comprimidas e alinhadas a 16 KB."))
        za = tools.get("zipalign") if use_tools else None
        if za and za.available:
            r = run_tool(tool_cmd(za, "-c", "-P", "16", "-v", "4", str(path)), timeout=timeout)
            if r.ok and "Verification succes" in r.stdout:
                fs.append(F("TECH-16KB-ZIP-ALIGN", PASS, "page_size_16kb", "zipalign -c -P 16 confirmou o alinhamento",
                            component=comp, evidence="zipalign -c -P 16 -v 4: Verification successful"))
            elif r.error == "nonzero":
                bad_lines = [l.strip() for l in r.stdout.splitlines() if "BAD" in l][:5]
                fs.append(F("TECH-16KB-ZIP-ALIGN", WARNING, "page_size_16kb", "zipalign -c -P 16 reportou falha de alinhamento",
                            component=comp, evidence="; ".join(bad_lines) or r.stdout.strip()[-300:],
                            recommendation="Realinhe com AGP ≥ 8.5.1 ou zipalign -P 16."))
            elif not r.ok:
                fs.append(F("TECH-16KB-ZIP-ALIGN", NOT_VERIFIED, "page_size_16kb", "zipalign não executou",
                            component=comp, evidence=r.reason(), method="manual"))
    else:
        fs.append(F("TECH-16KB-ZIP-ALIGN", NOT_VERIFIED, "page_size_16kb",
                    "Alinhamento ZIP dos APKs gerados pelo Play não verificável a partir do .aab",
                    component=comp, method="manual",
                    evidence="o alinhamento por offset vale para APKs; o .aab é convertido pelo Google Play",
                    recommendation="Gere APKs com `bundletool build-apks --bundle=app.aab --output=app.apks` e rode `zipalign -c -P 16 -v 4` nos splits; confira o aviso de 16 KB no App bundle explorer."))
    return res
