"""Entitlements validation (signed bundle, provisioning profile or project file).

Isolated use:
  python3 validate_entitlements.py <file.entitlements|plist> [--source bundle|project] [--profile-entitlements f.plist]
"""
from __future__ import annotations

import argparse
import json
import sys
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


def check_aasa(domains: list[str], fetch, component: str = "app") -> list[Finding]:
    """fetch(url) -> (status, bytes). Injected so tests can run offline."""
    out = []
    for entry in domains:
        if not entry.startswith("applinks:"):
            continue
        host = entry.split(":", 1)[1].split("?", 1)[0]
        url = f"https://{host}/.well-known/apple-app-site-association"
        try:
            status, body = fetch(url)
        except Exception as exc:  # noqa: BLE001
            out.append(Finding("ENT-ASSOC-001", NOT_VERIFIED, component, f"{url}: falha de rede ({type(exc).__name__})", confidence=0.3))
            continue
        if status != 200:
            out.append(Finding("ENT-ASSOC-001", WARNING, component, f"{url} retornou HTTP {status}", confidence=0.8,
                               recommendation="Publique o AASA via HTTPS sem redirecionamento, Content-Type application/json."))
            continue
        try:
            data = json.loads(body.decode("utf-8"))
            ok = isinstance(data.get("applinks"), dict)
        except (ValueError, AttributeError):
            ok = False
        out.append(Finding("ENT-ASSOC-001", PASS if ok else WARNING, component,
                           f"{url}: HTTP 200, applinks {'presente' if ok else 'ausente/ inválido'}", confidence=0.8,
                           recommendation="" if ok else "Inclua o bloco 'applinks' com o appID TEAMID.bundleid."))
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
