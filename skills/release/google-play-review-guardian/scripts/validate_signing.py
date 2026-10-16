"""Signing checks: debug certificate detection, certificate fingerprints, upload key size.

Pure-Python, no crypto: we *read* certificates (DER) from
  * v1 JAR signatures  META-INF/*.RSA|DSA|EC  (PKCS#7 SignedData)
  * APK Signing Block  (v2 0x7109871a, v3 0xf05368c0, v3.1 0x1b93ad61)
and compute SHA-256 fingerprints, subject CN and RSA modulus size.
Cryptographic signature *validity* needs apksigner (Java) -> NOT_VERIFIED without it.
"""
from __future__ import annotations

import hashlib
import re
import struct
import zipfile
import os
from pathlib import Path

from common import (BLOCKER, CRITICAL, INFO, NOT_VERIFIED, PASS, WARNING, FindingFactory, ToolInfo,
                    read_member, run_tool, tool_cmd, tool_env)

OID_CN = bytes.fromhex("550403")
OID_O = bytes.fromhex("55040a")
OID_NAMES = {bytes.fromhex("550403"): "CN", bytes.fromhex("55040a"): "O", bytes.fromhex("55040b"): "OU",
             bytes.fromhex("550406"): "C", bytes.fromhex("550408"): "ST", bytes.fromhex("550407"): "L"}
OID_RSA = bytes.fromhex("2a864886f70d010101")
OID_EC = bytes.fromhex("2a8648ce3d0201")
APK_SIG_BLOCK_MAGIC = b"APK Sig Block 42"
SIG_SCHEMES = {0x7109871A: "v2", 0xF05368C0: "v3", 0x1B93AD61: "v3.1"}


class DERError(ValueError):
    pass


def der_read(buf: bytes, p: int) -> tuple[int, int, int, int]:
    """Return (tag, content_start, content_len, next_pos)."""
    if p + 2 > len(buf):
        raise DERError("DER truncado")
    tag = buf[p]
    ln = buf[p + 1]
    p += 2
    if ln & 0x80:
        n = ln & 0x7F
        if n == 0 or n > 4 or p + n > len(buf):
            raise DERError("comprimento DER inválido")
        ln = int.from_bytes(buf[p:p + n], "big")
        p += n
    if p + ln > len(buf):
        raise DERError("conteúdo DER excede buffer")
    return tag, p, ln, p + ln


def der_children(buf: bytes, start: int, length: int) -> list[tuple[int, int, int, int]]:
    out, p, end = [], start, start + length
    while p < end:
        tag, cs, cl, nxt = der_read(buf, p)
        out.append((tag, p, cs, cl))
        p = nxt
    return out


def pkcs7_certificates(der: bytes) -> list[bytes]:
    """Extract certificates from a PKCS#7 ContentInfo/SignedData blob."""
    tag, cs, cl, _ = der_read(der, 0)
    if tag != 0x30:
        raise DERError("ContentInfo não é SEQUENCE")
    kids = der_children(der, cs, cl)
    if len(kids) < 2 or kids[1][0] != 0xA0:
        raise DERError("SignedData ausente")
    _, _, c0s, c0l = kids[1]
    t2, sds, sdl, _ = der_read(der, c0s)
    if t2 != 0x30:
        raise DERError("SignedData não é SEQUENCE")
    for tag, hdr, s, l in der_children(der, sds, sdl):
        if tag == 0xA0:  # [0] IMPLICIT certificates
            certs = []
            for ctag, chdr, cs2, cl2 in der_children(der, s, l):
                if ctag == 0x30:
                    certs.append(der[chdr:cs2 + cl2])
            return certs
    return []


def _name_attrs(buf: bytes, s: int, l: int) -> dict[str, str]:
    out = {}
    for _t, _h, rs, rl in der_children(buf, s, l):  # RDN SETs
        for _t2, _h2, as_, al in der_children(buf, rs, rl):  # AttributeTypeAndValue
            parts = der_children(buf, as_, al)
            if len(parts) == 2 and parts[0][0] == 0x06:
                oid = buf[parts[0][2]:parts[0][2] + parts[0][3]]
                val = buf[parts[1][2]:parts[1][2] + parts[1][3]].decode("utf-8", "replace")
                key = OID_NAMES.get(oid, oid.hex())
                out[key] = val
    return out


def cert_info(cert: bytes) -> dict:
    info = {"sha256": hashlib.sha256(cert).hexdigest().upper(), "sha1": hashlib.sha1(cert).hexdigest().upper()}
    try:
        _, cs, cl, _ = der_read(cert, 0)
        tbs = der_children(cert, cs, cl)[0]
        fields = der_children(cert, tbs[2], tbs[3])
        i = 1 if fields and fields[0][0] == 0xA0 else 0
        issuer = fields[i + 2]
        subject = fields[i + 4]
        spki = fields[i + 5]
        info["subject"] = _name_attrs(cert, subject[2], subject[3])
        info["issuer"] = _name_attrs(cert, issuer[2], issuer[3])
        alg_seq, bitstr = der_children(cert, spki[2], spki[3])[:2]
        alg_oid = der_children(cert, alg_seq[2], alg_seq[3])[0]
        oid = cert[alg_oid[2]:alg_oid[2] + alg_oid[3]]
        if oid == OID_RSA:
            info["key_algorithm"] = "RSA"
            # BIT STRING: first byte = unused bits, then RSAPublicKey SEQUENCE{n, e}
            _, rs, rl, _ = der_read(cert, bitstr[2] + 1)
            n = der_children(cert, rs, rl)[0]
            modulus = cert[n[2]:n[2] + n[3]].lstrip(b"\x00")
            info["key_bits"] = len(modulus) * 8
        elif oid == OID_EC:
            info["key_algorithm"] = "EC"
        else:
            info["key_algorithm"] = oid.hex()
    except (DERError, IndexError, ValueError) as exc:
        info["parse_error"] = str(exc)
    return info


def is_debug_cert(ci: dict) -> bool:
    return ci.get("subject", {}).get("CN", "").strip().lower() == "android debug"


def find_signing_block(path: Path) -> dict[int, bytes]:
    """Locate the APK Signing Block before the central directory and return id->value."""
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        tail_len = min(size, 65536 + 22)
        fh.seek(size - tail_len)
        tail = fh.read(tail_len)
        eocd = tail.rfind(b"PK\x05\x06")
        if eocd < 0:
            return {}
        cd_offset = struct.unpack_from("<I", tail, eocd + 16)[0]
        if cd_offset < 32 or cd_offset > size:
            return {}
        fh.seek(cd_offset - 24)
        footer = fh.read(24)
        if footer[8:] != APK_SIG_BLOCK_MAGIC:
            return {}
        block_size = struct.unpack_from("<Q", footer, 0)[0]
        if block_size > 64 * 1024 ** 2 or block_size + 8 > cd_offset:
            return {}
        fh.seek(cd_offset - block_size - 8)
        block = fh.read(block_size + 8)
    pairs = {}
    p, end = 8, len(block) - 24
    while p + 12 <= end:
        ln = struct.unpack_from("<Q", block, p)[0]
        if ln < 4 or p + 8 + ln > end:
            break
        pid = struct.unpack_from("<I", block, p + 8)[0]
        pairs[pid] = block[p + 12:p + 8 + ln]
        p += 8 + ln
    return pairs


def _lp(buf: bytes, p: int) -> tuple[bytes, int]:
    n = struct.unpack_from("<I", buf, p)[0]
    if p + 4 + n > len(buf):
        raise ValueError("length-prefixed truncado")
    return buf[p + 4:p + 4 + n], p + 4 + n


def scheme_certificates(value: bytes) -> list[bytes]:
    """v2/v3 value: signers -> signer -> signed_data -> (digests, certificates, ...)."""
    certs = []
    signers, _ = _lp(value, 0)
    p = 0
    while p < len(signers):
        signer, p = _lp(signers, p)
        signed_data, _ = _lp(signer, 0)
        _digests, q = _lp(signed_data, 0)
        cert_seq, _ = _lp(signed_data, q)
        r = 0
        while r < len(cert_seq):
            c, r = _lp(cert_seq, r)
            certs.append(c)
    return certs


def read_signatures(path: Path) -> dict:
    out = {"schemes": [], "certificates": [], "errors": []}
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if re.match(r"^META-INF/[^/]+\.(RSA|DSA|EC)$", info.filename, re.I):
                    out["schemes"].append("v1/jar")
                    try:
                        for c in pkcs7_certificates(read_member(zf, info, 1024 ** 2)):
                            out["certificates"].append({"source": info.filename, **cert_info(c)})
                    except DERError as exc:
                        out["errors"].append(f"{info.filename}: {exc}")
    except zipfile.BadZipFile as exc:
        out["errors"].append(str(exc))
        return out
    try:
        for pid, val in find_signing_block(path).items():
            if pid in SIG_SCHEMES:
                out["schemes"].append(SIG_SCHEMES[pid])
                try:
                    for c in scheme_certificates(val):
                        out["certificates"].append({"source": f"APK Signing Block {SIG_SCHEMES[pid]}", **cert_info(c)})
                except (ValueError, struct.error) as exc:
                    out["errors"].append(f"{SIG_SCHEMES[pid]}: {exc}")
    except (OSError, struct.error) as exc:
        out["errors"].append(str(exc))
    out["schemes"] = sorted(set(out["schemes"]))
    return out


def _fp(sha: str) -> str:
    return ":".join(sha[i:i + 2] for i in range(0, len(sha), 2))


def validate_signing(path: Path, kind: str, tools: dict[str, ToolInfo], F: FindingFactory, timeout: int = 120) -> dict:
    sig = read_signatures(path)
    fs = []
    comp = path.name
    certs = sig["certificates"]
    uniq = {c["sha256"]: c for c in certs}
    if not sig["schemes"]:
        sev = BLOCKER if kind == "aab" else WARNING
        fs.append(F("TECH-DEBUG-SIGNATURE", sev, "signing", "Artefato sem assinatura detectável",
                    component=comp, evidence="nenhum META-INF/*.RSA|DSA|EC nem APK Signing Block",
                    recommendation="Assine com a chave de upload: configure signingConfigs.release e gere `flutter build appbundle --release`."))
    elif any(is_debug_cert(c) for c in uniq.values()):
        fs.append(F("TECH-DEBUG-SIGNATURE", BLOCKER, "signing", "Assinado com certificado de debug",
                    component=comp, evidence="subject CN=Android Debug em " + ", ".join(sorted({c['source'] for c in certs if is_debug_cert(c)})),
                    recommendation="Gere o release assinado com a chave de upload (nunca a debug.keystore)."))
    elif uniq:
        fs.append(F("TECH-DEBUG-SIGNATURE", PASS, "signing", "Certificado não é de debug",
                    component=comp, evidence="; ".join(f"CN={c.get('subject', {}).get('CN', '?')} SHA-256={_fp(c['sha256'])}" for c in uniq.values())))
    for c in uniq.values():
        if c.get("key_algorithm") == "RSA" and c.get("key_bits"):
            if kind == "aab":
                ok = c["key_bits"] >= 2048
                fs.append(F("TECH-UPLOAD-KEY", PASS if ok else WARNING, "signing",
                            "Chave de upload RSA ≥ 2048 bits" if ok else "Chave de upload RSA menor que 2048 bits",
                            component=comp, evidence=f"RSA {c['key_bits']} bits; SHA-256={_fp(c['sha256'])}",
                            recommendation="-" if ok else "Solicite reset da chave de upload no Play Console com uma chave RSA ≥ 2048 bits."))
    if kind == "aab":
        fs.append(F("TECH-PLAY-APP-SIGNING", NOT_VERIFIED, "signing", "Inscrição no Play App Signing não verificável offline",
                    component=comp, method="manual",
                    evidence="A assinatura do .aab é a da chave de upload; os APKs distribuídos são re-assinados pelo Google Play.",
                    recommendation="Confira Play Console > Test and release > App integrity > Play app signing; registre o SHA-256 da app signing key em APIs/App Links."))
    apksigner = tools.get("apksigner")
    if kind == "apk":
        if apksigner and apksigner.available:
            r = run_tool(tool_cmd(apksigner, "verify", "--print-certs", "--verbose", str(path)), timeout=timeout, env=tool_env(apksigner))
            if r.ok:
                fs.append(F("TECH-SIGNATURE-VERIFY", PASS, "signing", "apksigner verify aprovou a assinatura",
                            component=comp, evidence=" ".join(l for l in r.stdout.splitlines() if "Verified using" in l)[:300]))
            elif r.error == "nonzero":
                fs.append(F("TECH-SIGNATURE-VERIFY", BLOCKER, "signing", "apksigner verify rejeitou a assinatura",
                            component=comp, evidence=(r.stderr or r.stdout).strip()[:300],
                            recommendation="Reassine o APK com apksigner/Gradle e verifique novamente."))
            else:
                fs.append(F("TECH-SIGNATURE-VERIFY", NOT_VERIFIED, "signing", "apksigner falhou ao executar",
                            component=comp, evidence=r.reason(), method="manual"))
        else:
            fs.append(F("TECH-SIGNATURE-VERIFY", NOT_VERIFIED, "signing", "Validade criptográfica da assinatura não verificada",
                        component=comp, method="manual",
                        evidence=f"apksigner indisponível: {apksigner.reason if apksigner else 'não descoberto'}; "
                        f"esquemas detectados por parsing: {', '.join(sig['schemes']) or 'nenhum'}",
                        recommendation=f"Instale um JRE e rode `apksigner verify --print-certs --verbose {comp}`."))
    else:
        fs.append(_verify_bundle_jar_signature(path, comp, sig, tools.get("java"), F, timeout))
    return {"signatures": {**sig, "certificates": list(uniq.values())}, "findings": fs}


def _verify_bundle_jar_signature(path: Path, comp: str, sig: dict, java: ToolInfo | None, F: FindingFactory, timeout: int):
    """A bundle is JAR-signed (v1). The JDK's `jarsigner -verify` checks every entry's digest and signature.

    `jarsigner` ships next to `java` only in a JDK; a JRE-only install keeps this NOT_VERIFIED.
    `-strict` is deliberately not used: it fails every Gradle-built bundle because upload keys are
    self-signed (no PKIX chain) and META-INF is not the first ZIP entry (JarInputStream ordering),
    neither of which Google Play requires. Without it, a non-zero exit means a real signature or
    digest failure; PASS additionally requires jarsigner's own "jar verified." line.
    """
    schemes = ", ".join(sig["schemes"]) or "nenhum"
    jarsigner = None
    if java and java.available and java.path:
        candidate = Path(java.path).with_name("jarsigner.exe" if os.name == "nt" else "jarsigner")
        if candidate.is_file():
            jarsigner = candidate
    if jarsigner is None:
        return F("TECH-SIGNATURE-VERIFY", NOT_VERIFIED, "signing", "Assinatura JAR do bundle não verificada criptograficamente",
                 component=comp, method="manual",
                 evidence=f"jarsigner indisponível (requer JDK, não só JRE); esquemas lidos: {schemes}; certificados extraídos sem validação de assinatura",
                 recommendation=f"Rode `jarsigner -verify -verbose -certs {comp}` (requer JDK); o Play Console também valida no upload.")
    env = {"PATH": os.pathsep.join([str(jarsigner.parent), os.defpath])}
    if java.java_home:
        env["JAVA_HOME"] = java.java_home
    r = run_tool([str(jarsigner), "-verify", str(path)], timeout=timeout, env=env)
    if r.ok and "jar verified." in r.stdout:
        return F("TECH-SIGNATURE-VERIFY", PASS, "signing", "jarsigner -verify aprovou digests e assinaturas do bundle",
                 component=comp, evidence="jarsigner -verify: jar verified. (avisos esperados em bundles Gradle: certificado de upload autoassinado, META-INF fora do início do ZIP)")
    if r.error == "nonzero":
        return F("TECH-SIGNATURE-VERIFY", BLOCKER, "signing", "jarsigner -verify rejeitou a assinatura do bundle",
                 component=comp, evidence=(r.stdout or r.stderr).strip()[-400:],
                 recommendation="Gere o bundle novamente com a chave de upload (`flutter build appbundle` / Gradle signingConfig) e verifique outra vez.")
    return F("TECH-SIGNATURE-VERIFY", NOT_VERIFIED, "signing", "jarsigner não confirmou a verificação",
             component=comp, evidence=r.reason() if not r.ok else "saída sem 'jar verified.'", method="manual")


# --------------------------------------------------------------------------
# Project-level signing hygiene (never reads secret values)
# --------------------------------------------------------------------------
def project_signing_hygiene(sensitive_files: list[str], git_status: dict[str, dict], F: FindingFactory) -> list:
    """sensitive_files: project-relative paths. Contents are NEVER opened."""
    fs = []
    for rel in sorted(sensitive_files)[:20]:
        name = rel.rsplit("/", 1)[-1]
        st = git_status.get(rel, {})
        if st.get("tracked"):
            fs.append(F("SEC-SIGNING-SECRETS-IN-VCS", CRITICAL, "security", "Arquivo de assinatura versionado no Git",
                        component=rel, evidence=f"{rel} está rastreado pelo Git (conteúdo NÃO lido/impresso)",
                        recommendation=f"Remova do índice com `git rm --cached {rel}`, adicione ao .gitignore e avalie rotacionar a chave de upload no Play Console (o histórico Git mantém cópias)."))
        elif st.get("ignored"):
            fs.append(F("SEC-SIGNING-SECRETS-IN-VCS", PASS, "security", "Arquivo de assinatura ignorado pelo Git",
                        component=rel, evidence=f"{rel} presente e coberto por .gitignore (conteúdo não lido)"))
        elif st.get("in_repo"):
            fs.append(F("SEC-SIGNING-SECRETS-IN-VCS", WARNING, "security", "Arquivo de assinatura não ignorado pelo Git",
                        component=rel, evidence=f"{rel} não rastreado, mas também não está no .gitignore (conteúdo não lido)",
                        recommendation=f"Adicione `{name}` ao .gitignore para evitar commit acidental."))
        else:
            fs.append(F("SEC-SIGNING-SECRETS-IN-VCS", NOT_VERIFIED, "security", "Status Git do arquivo de assinatura desconhecido",
                        component=rel, evidence="projeto fora de um repositório Git ou git indisponível (conteúdo não lido)", method="manual"))
    return fs
