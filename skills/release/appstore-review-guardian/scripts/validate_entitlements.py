"""Entitlements validation (signed bundle, provisioning profile or project file).

Isolated use:
  python3 validate_entitlements.py <file.entitlements|plist> [--source bundle|project] [--profile-entitlements f.plist]
"""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    BLOCKER, CRITICAL, INFO, NOT_VERIFIED, PASS, WARNING, Collector, Finding, load_plist,
)

# Keys that are informational and never need profile authorization comparison on value level.
_PREFIX_KEYS = {"application-identifier", "com.apple.developer.team-identifier", "keychain-access-groups"}


def validate_entitlements(ents: dict, source: str, component: str, file: str,
                          profile_ents: dict | None = None, info_plist: dict | None = None) -> tuple[list[Finding], dict]:
    """source: 'bundle' (claimed in the code signature) or 'project' (Release .entitlements)."""
    c = Collector()
    facts = {"keys": sorted(ents.keys()), "aps-environment": ents.get("aps-environment"),
             "get-task-allow": ents.get("get-task-allow"),
             "associated_domains": list(ents.get("com.apple.developer.associated-domains") or []),
             "sign_in_with_apple": "com.apple.developer.applesignin" in ents}

    gta = ents.get("get-task-allow")
    if gta is True:
        if source == "bundle":
            c.add("ENT-GTA-001", BLOCKER, component, "get-task-allow=true na assinatura do bundle", file=file, confidence=1.0,
                  recommendation="Exporte com perfil App Store: 'flutter build ipa --release --export-options-plist=ios/ExportOptions.plist' (method app-store-connect).")
        else:
            c.add("ENT-GTA-001", WARNING, component, "get-task-allow=true declarado no arquivo de entitlements do Release", file=file,
                  confidence=0.8, auto_fixable=True,
                  recommendation="Remova get-task-allow do .entitlements; o Xcode o define pelo perfil de desenvolvimento.")
    elif source == "bundle":
        c.add("ENT-GTA-001", PASS, component, f"get-task-allow={gta!r} na assinatura", file=file, confidence=1.0)

    aps = ents.get("aps-environment")
    if aps is not None:
        if source == "bundle":
            if aps == "production":
                c.add("ENT-APS-001", PASS, component, "aps-environment=production", file=file, confidence=1.0)
            else:
                c.add("ENT-APS-001", CRITICAL, component, f"aps-environment={aps!r} no artefato assinado", file=file, confidence=1.0,
                      recommendation="Assine com perfil de distribuição App Store; o valor deve ser 'production'.")
        elif aps == "development":
            c.add("ENT-APS-001", WARNING, component, "aps-environment=development no .entitlements de origem", file=file, confidence=0.5,
                  recommendation="Normalmente o Xcode troca para 'production' ao exportar com perfil de distribuição; confirme no .ipa "
                                 "('codesign -d --entitlements - Payload/Runner.app') ou crie RunnerRelease.entitlements com 'production'.")
        else:
            c.add("ENT-APS-001", PASS, component, f"aps-environment={aps!r} no .entitlements", file=file, confidence=0.8)

    if profile_ents is not None:
        missing = [k for k in ents if k not in profile_ents]
        if missing:
            c.add("ENT-PROFILE-001", BLOCKER, component, f"entitlements não autorizados pelo perfil: {missing}", file=file, confidence=0.95,
                  recommendation="Habilite as capabilities no App ID e regenere o provisioning profile de distribuição.")
        else:
            c.add("ENT-PROFILE-001", PASS, component, f"{len(ents)} entitlement(s) presentes na allowlist do perfil", file=file, confidence=0.95)
    elif source == "bundle":
        c.add("ENT-PROFILE-001", NOT_VERIFIED, component, "perfil indisponível para comparar entitlements", file=file, confidence=0.3,
              recommendation="Inclua embedded.mobileprovision (export App Store) para a comparação.")

    doms = facts["associated_domains"]
    if doms:
        c.add("ENT-ASSOC-001", NOT_VERIFIED, component, f"associated domains: {doms}; AASA não consultado (use --network)", file=file,
              confidence=0.3, recommendation="Confira https://<domínio>/.well-known/apple-app-site-association com o appID TEAMID.bundleid.")

    if info_plist is not None:
        bg = info_plist.get("UIBackgroundModes") or []
        if "remote-notification" in bg:
            if aps is None:
                c.add("REV-PUSH-001", WARNING, component, "UIBackgroundModes contém remote-notification mas não há aps-environment",
                      file=file, confidence=0.8, recommendation="Ative a capability Push Notifications no target Runner (Release).")
            else:
                c.add("REV-PUSH-001", PASS, component, f"remote-notification + aps-environment={aps}", file=file, confidence=0.9)
    return c.items, facts


AASA_SERVICES = ("applinks", "webcredentials")
AASA_TIMEOUT = 10
AASA_MAX_BYTES = 128 * 1024
AASA_UA = "appstore-review-guardian (read-only AASA check)"
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_BLOCKED_SUFFIXES = ("localhost", "local", "internal", "lan", "home.arpa", "localdomain", "intranet", "invalid", "test",
                     "onion", "arpa")


def aasa_host(entry: str) -> tuple[str | None, str]:
    """Return (hostname, '') for an applinks:/webcredentials: entry that is a public-looking DNS name.

    The host comes from the audited project (untrusted), so anything that is not a plain multi-label
    DNS name is refused before any resolution: IP literals in every notation (dotted, decimal, hex,
    octal, IPv6, bracketed), single-label names, local/internal suffixes, wildcards, userinfo,
    paths and any port other than 443.
    """
    if not isinstance(entry, str) or ":" not in entry:
        return None, "entrada mal formada"
    service, rest = entry.split(":", 1)
    if service not in AASA_SERVICES:
        return None, f"serviço {service!r} não consultado"
    host = rest.split("?", 1)[0].strip()
    if not host or len(host) > 260:
        return None, "host vazio ou longo demais"
    if any(ch in host for ch in "/@[]\\ \t%#"):
        return None, "host com caracteres não permitidos (caminho, userinfo, IPv6 entre colchetes)"
    if host.count(":") > 1:
        return None, "literal IPv6 não é permitido"
    if ":" in host:
        host, port = host.split(":", 1)
        if port != "443":
            return None, f"porta {port!r} não permitida (só 443)"
    host = host.lower()
    if host.endswith("."):
        return None, "nome com ponto final não suportado"
    if host.startswith("*."):
        return None, "domínio curinga: AASA não consultável diretamente"
    try:
        ipaddress.ip_address(host)
        return None, "literal IP não é permitido"
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) < 2:
        return None, "nome de rótulo único (não é DNS público)"
    if len(host) > 253 or not all(_LABEL.match(lab) for lab in labels):
        return None, "nome DNS inválido"
    if labels[-1].isdigit() or re.fullmatch(r"0x[0-9a-f]+", labels[-1]) or all(re.fullmatch(r"(0x[0-9a-f]+|\d+)", lab) for lab in labels):
        return None, "parece literal IP em notação alternativa"
    if any(host == s or host.endswith("." + s) for s in _BLOCKED_SUFFIXES):
        return None, "sufixo local/reservado"
    return host, ""


def _unsafe_ip(raw: str) -> str | None:
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return "endereço inválido"
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    for attr in ("is_private", "is_loopback", "is_link_local", "is_reserved", "is_multicast", "is_unspecified"):
        if getattr(ip, attr):
            return f"{ip} ({attr[3:]})"
    if not ip.is_global:
        return f"{ip} (não global)"
    return None


def resolve_public(host: str, resolver=None) -> tuple[str | None, str]:
    """Resolve host and return the first address only if EVERY resolved address is public."""
    resolver = resolver or socket.getaddrinfo
    try:
        infos = resolver(host, 443, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        return None, f"falha de DNS ({type(exc).__name__})"
    addrs = [str(i[4][0]) for i in infos if i and len(i) >= 5 and i[4]]
    if not addrs:
        return None, "DNS sem endereços"
    bad = [r for r in (_unsafe_ip(a) for a in addrs) if r]
    if bad:
        return None, f"resolve para endereço não público: {bad[:3]}"
    return addrs[0], ""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect (Apple also fetches the AASA without following redirects)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirecionamento recusado ({code})", headers, fp)


def _build_opener(pinned_ip: str) -> urllib.request.OpenerDirector:
    """HTTPS-only opener that connects to the already-validated IP (no DNS rebinding), verifies the
    certificate for the original hostname, ignores proxy environment variables and refuses redirects."""
    ctx = ssl.create_default_context()

    class PinnedHTTPSConnection(http.client.HTTPSConnection):
        def __init__(self, host, **kw):
            kw.pop("context", None)
            super().__init__(host, context=ctx, **kw)

        def connect(self):
            sock = socket.create_connection((pinned_ip, self.port or 443), self.timeout)
            self.sock = ctx.wrap_socket(sock, server_hostname=self.host)

    class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(PinnedHTTPSConnection, req)

    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), PinnedHTTPSHandler(context=ctx))


def safe_fetch(url: str, pinned_ip: str) -> tuple[int, bytes]:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.port not in (None, 443) or parts.username or parts.password:
        raise ValueError("só https na porta 443, sem credenciais")
    req = urllib.request.Request(url, headers={"User-Agent": AASA_UA, "Accept": "application/json"})
    try:
        with _build_opener(pinned_ip).open(req, timeout=AASA_TIMEOUT) as resp:
            body = resp.read(AASA_MAX_BYTES + 1)
            if len(body) > AASA_MAX_BYTES:
                raise ValueError(f"resposta maior que {AASA_MAX_BYTES} bytes")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def check_aasa(domains: list[str], fetch, component: str = "app", resolver=None) -> list[Finding]:
    """fetch(url, pinned_ip) -> (status, bytes); resolver mimics socket.getaddrinfo. Both injectable for offline tests."""
    out = []
    hosts: dict[str, set[str]] = {}
    for entry in domains:
        service = entry.split(":", 1)[0] if isinstance(entry, str) else ""
        if service not in AASA_SERVICES:
            continue
        host, reason = aasa_host(entry)
        if host is None:
            out.append(Finding("ENT-ASSOC-001", WARNING, component, f"{entry!r}: {reason}; AASA não consultado", confidence=0.8,
                               recommendation="Use um nome DNS público (ex.: applinks:example.com) nas associated domains."))
            continue
        hosts.setdefault(host, set()).add(service)
    for host, services in sorted(hosts.items()):
        url = f"https://{host}/.well-known/apple-app-site-association"
        ip, reason = resolve_public(host, resolver)
        if ip is None:
            out.append(Finding("ENT-ASSOC-001", NOT_VERIFIED, component, f"{host}: {reason}; AASA não consultado", confidence=0.3,
                               recommendation="Confira o AASA manualmente a partir de uma rede pública."))
            continue
        try:
            status, body = fetch(url, ip)
        except Exception as exc:  # noqa: BLE001 - any network/size failure degrades to NOT_VERIFIED
            out.append(Finding("ENT-ASSOC-001", NOT_VERIFIED, component, f"{url}: falha ao consultar ({type(exc).__name__}: {str(exc)[:80]})",
                               confidence=0.3))
            continue
        if status != 200:
            out.append(Finding("ENT-ASSOC-001", WARNING, component, f"{url} retornou HTTP {status}", confidence=0.8,
                               recommendation="Publique o AASA via HTTPS sem redirecionamento, Content-Type application/json."))
            continue
        try:
            data = json.loads(body.decode("utf-8"))
            missing = [s for s in sorted(services) if not isinstance(data.get(s), dict)]
        except (ValueError, AttributeError):
            missing = sorted(services)
        out.append(Finding("ENT-ASSOC-001", WARNING if missing else PASS, component,
                           f"{url}: HTTP 200, " + (f"bloco(s) ausente(s)/inválido(s): {missing}" if missing else f"blocos {sorted(services)} presentes"),
                           confidence=0.8,
                           recommendation=f"Inclua {missing} com o appID TEAMID.bundleid." if missing else ""))
    return out


def validate_file(path: Path, source: str = "project", component: str = "app", profile_ents: dict | None = None):
    ents, err = load_plist(path)
    if err or not isinstance(ents, dict):
        sev = CRITICAL if source == "project" else BLOCKER
        return [Finding("PROJ-ENT-001", sev, component, f"entitlements ilegível: {err}", file=str(path), confidence=1.0,
                        recommendation="Corrija com 'plutil -lint'.")], {}
    return validate_entitlements(ents, source, component, str(path), profile_ents)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file")
    ap.add_argument("--source", choices=["bundle", "project"], default="project")
    ap.add_argument("--profile-entitlements")
    a = ap.parse_args(argv)
    prof = None
    if a.profile_entitlements:
        prof, _ = load_plist(Path(a.profile_entitlements))
    findings, facts = validate_file(Path(a.file), a.source, profile_ents=prof)
    print(json.dumps({"facts": facts, "findings": [f.to_dict(i + 1) for i, f in enumerate(findings)]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = ["validate_entitlements", "check_aasa", "validate_file", "INFO"]
