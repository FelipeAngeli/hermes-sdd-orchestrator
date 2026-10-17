"""Minimal, bounded Mach-O reader (stdlib only).

Reads headers and load commands only (never the whole binary), the code
signature superblob (to extract embedded entitlements without codesign) and
streams the file for symbol-string scanning.
"""
from __future__ import annotations

import plistlib
import struct
from dataclasses import dataclass, field
from pathlib import Path

FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
MH_MAGIC = 0xFEEDFACE
MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM = 0xCEFAEDFE
MH_CIGAM_64 = 0xCFFAEDFE

CPU_NAMES = {
    7: "i386",
    0x01000007: "x86_64",
    12: "arm",
    0x0100000C: "arm64",
    0x0200000C: "arm64_32",
}
PLATFORMS = {1: "macos", 2: "ios", 3: "tvos", 4: "watchos", 6: "maccatalyst", 7: "ios-simulator",
             8: "tvos-simulator", 9: "watchos-simulator", 11: "visionos", 12: "visionos-simulator"}

LC_SEGMENT = 0x1
LC_SEGMENT_64 = 0x19
LC_LOAD_DYLIB = 0xC
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001F
LC_CODE_SIGNATURE = 0x1D
LC_VERSION_MIN_IPHONEOS = 0x25
LC_BUILD_VERSION = 0x32
LC_ENCRYPTION_INFO_64 = 0x2C

CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_EMBEDDED_ENTITLEMENTS = 0xFADE7171
CSSLOT_ENTITLEMENTS = 5
CSSLOT_SIGNATURESLOT = 0x10000

MAX_LOAD_CMDS_BYTES = 4 * 1024 * 1024
MAX_SIGNATURE_BYTES = 8 * 1024 * 1024


class MachOError(Exception):
    pass


def _ver(v: int) -> str:
    return f"{v >> 16}.{(v >> 8) & 0xFF}.{v & 0xFF}"


@dataclass
class Slice:
    arch: str
    offset: int
    size: int
    platform: str | None = None
    minos: str | None = None
    sdk: str | None = None
    segments: list[str] = field(default_factory=list)
    dylibs: list[str] = field(default_factory=list)
    code_signature: tuple[int, int] | None = None  # (dataoff, datasize) relative to slice
    encrypted: bool | None = None


@dataclass
class MachO:
    path: str
    fat: bool
    slices: list[Slice]

    def archs(self) -> list[str]:
        return [s.arch for s in self.slices]

    def preferred(self) -> Slice | None:
        for s in self.slices:
            if s.arch == "arm64":
                return s
        return self.slices[0] if self.slices else None


def is_macho(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    if len(head) < 4:
        return False
    m = struct.unpack(">I", head)[0]
    return m in (FAT_MAGIC, FAT_MAGIC_64, MH_CIGAM, MH_CIGAM_64, MH_MAGIC, MH_MAGIC_64)


def _parse_thin(fh, offset: int, size: int) -> Slice:
    fh.seek(offset)
    raw = fh.read(32)
    if len(raw) < 28:
        raise MachOError("cabeçalho Mach-O truncado")
    magic_le = struct.unpack("<I", raw[:4])[0]
    if magic_le == MH_MAGIC_64:
        endian, hdr_size = "<", 32
    elif magic_le == MH_MAGIC:
        endian, hdr_size = "<", 28
    else:
        magic_be = struct.unpack(">I", raw[:4])[0]
        if magic_be == MH_MAGIC_64:
            endian, hdr_size = ">", 32
        elif magic_be == MH_MAGIC:
            endian, hdr_size = ">", 28
        else:
            raise MachOError(f"magic Mach-O desconhecido 0x{magic_le:08x}")
    _magic, cputype, _sub, _ftype, ncmds, sizeofcmds = struct.unpack(endian + "IiiIII", raw[:24])
    if sizeofcmds > MAX_LOAD_CMDS_BYTES or ncmds > 10000:
        raise MachOError("load commands grandes demais")
    sl = Slice(arch=CPU_NAMES.get(cputype & 0xFFFFFFFF, f"cpu_{cputype & 0xFFFFFFFF:x}"), offset=offset, size=size)
    fh.seek(offset + hdr_size)
    cmds = fh.read(sizeofcmds)
    pos = 0
    for _ in range(ncmds):
        if pos + 8 > len(cmds):
            break
        cmd, cmdsize = struct.unpack(endian + "II", cmds[pos:pos + 8])
        if cmdsize < 8 or pos + cmdsize > len(cmds):
            break
        body = cmds[pos:pos + cmdsize]
        if cmd in (LC_SEGMENT_64, LC_SEGMENT):
            sl.segments.append(body[8:24].split(b"\0", 1)[0].decode("ascii", "replace"))
        elif cmd in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB):
            name_off = struct.unpack(endian + "I", body[8:12])[0]
            sl.dylibs.append(body[name_off:].split(b"\0", 1)[0].decode("utf-8", "replace"))
        elif cmd == LC_CODE_SIGNATURE:
            dataoff, datasize = struct.unpack(endian + "II", body[8:16])
            sl.code_signature = (dataoff, datasize)
        elif cmd == LC_BUILD_VERSION:
            platform, minos, sdk = struct.unpack(endian + "III", body[8:20])
            sl.platform = PLATFORMS.get(platform, f"platform_{platform}")
            sl.minos, sl.sdk = _ver(minos), _ver(sdk)
        elif cmd == LC_VERSION_MIN_IPHONEOS:
            version, sdk = struct.unpack(endian + "II", body[8:16])
            sl.platform = sl.platform or "ios"
            sl.minos, sl.sdk = _ver(version), _ver(sdk)
        elif cmd == LC_ENCRYPTION_INFO_64:
            cryptid = struct.unpack(endian + "I", body[16:20])[0]
            sl.encrypted = bool(cryptid)
        pos += cmdsize
    return sl


def parse(path: Path) -> MachO:
    size = path.stat().st_size
    with open(path, "rb") as fh:
        head = fh.read(8)
        if len(head) < 8:
            raise MachOError("arquivo pequeno demais")
        magic = struct.unpack(">I", head[:4])[0]
        if magic in (FAT_MAGIC, FAT_MAGIC_64):
            nfat = struct.unpack(">I", head[4:8])[0]
            if nfat > 32:
                raise MachOError("fat header inválido")
            slices = []
            for i in range(nfat):
                if magic == FAT_MAGIC:
                    fh.seek(8 + i * 20)
                    _cpu, _sub, off, sz, _al = struct.unpack(">iiIII", fh.read(20))
                else:
                    fh.seek(8 + i * 32)
                    _cpu, _sub, off, sz, _al, _r = struct.unpack(">iiQQII", fh.read(32))
                if off + sz > size:
                    raise MachOError("fatia fora do arquivo")
                slices.append(_parse_thin(fh, off, sz))
            return MachO(str(path), True, slices)
        return MachO(str(path), False, [_parse_thin(fh, 0, size)])


def read_signature(path: Path, sl: Slice) -> dict:
    """Parse the embedded code-signature superblob of a slice.

    Returns {"entitlements": dict|None, "entitlements_error": str|None, "cms": bytes|None}.
    No cryptographic verification is done here.
    """
    out: dict = {"entitlements": None, "entitlements_error": None, "cms": None}
    if not sl.code_signature:
        return out
    dataoff, datasize = sl.code_signature
    if datasize > MAX_SIGNATURE_BYTES:
        out["entitlements_error"] = "assinatura grande demais"
        return out
    with open(path, "rb") as fh:
        fh.seek(sl.offset + dataoff)
        blob = fh.read(datasize)
    if len(blob) < 12:
        out["entitlements_error"] = "superblob truncado"
        return out
    magic, _length, count = struct.unpack(">III", blob[:12])
    if magic != CSMAGIC_EMBEDDED_SIGNATURE or count > 64:
        out["entitlements_error"] = f"superblob com magic inesperado 0x{magic:08x}"
        return out
    for i in range(count):
        base = 12 + i * 8
        if base + 8 > len(blob):
            break
        btype, boff = struct.unpack(">II", blob[base:base + 8])
        if boff + 8 > len(blob):
            continue
        bmagic, blen = struct.unpack(">II", blob[boff:boff + 8])
        payload = blob[boff + 8: boff + blen]
        if btype == CSSLOT_ENTITLEMENTS and bmagic == CSMAGIC_EMBEDDED_ENTITLEMENTS:
            try:
                out["entitlements"] = plistlib.loads(payload)
            except Exception as exc:  # noqa: BLE001
                out["entitlements_error"] = f"plist de entitlements inválida: {exc}"
        elif btype == CSSLOT_SIGNATURESLOT:
            out["cms"] = payload
    return out


def scan_strings(path: Path, needles: dict[str, list[bytes]], chunk: int = 4 * 1024 * 1024) -> dict[str, list[str]]:
    """Stream the file looking for exact byte needles. Returns key -> matched needles."""
    found: dict[str, set[bytes]] = {}
    allneedles = [(k, n) for k, ns in needles.items() for n in ns]
    if not allneedles:
        return {}
    overlap = max(len(n) for _, n in allneedles)
    tail = b""
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            window = tail + b
            for k, n in allneedles:
                if n in window:
                    found.setdefault(k, set()).add(n)
            tail = window[-overlap:]
    return {k: sorted(x.decode("ascii", "replace").rstrip("\0") for x in v) for k, v in found.items()}


def certificate_names(cms: bytes | None) -> list[str]:
    """Best-effort extraction of certificate common names from a CMS blob (no verification)."""
    if not cms:
        return []
    names = []
    for marker in (b"Apple Distribution: ", b"iPhone Distribution: ", b"Apple Development: ", b"iPhone Developer: "):
        i = cms.find(marker)
        while i != -1:
            end = i
            while end < len(cms) and end - i < 128 and 32 <= cms[end] < 127:
                end += 1
            names.append(cms[i:end].decode("ascii", "replace"))
            i = cms.find(marker, end)
    return sorted(set(names))
