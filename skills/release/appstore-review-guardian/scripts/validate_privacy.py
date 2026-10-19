"""Privacy manifest (PrivacyInfo.xcprivacy) validation per TN3181 and Apple docs.

Isolated use:
  python3 validate_privacy.py <PrivacyInfo.xcprivacy> [--component NAME]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BLOCKER, PASS, WARNING, Collector, Finding, load_plist_bytes, ref  # noqa: E402

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")


def validate_manifest_bytes(data: bytes, component: str, file: str) -> tuple[list[Finding], dict]:
    c = Collector()
    facts: dict = {"component": component, "file": file, "valid": False, "tracking": None,
                   "tracking_domains": [], "api_categories": {}, "collected": []}
    pl, err = load_plist_bytes(data)
    if err or not isinstance(pl, dict):
        c.add("PRIV-MANIFEST-002", BLOCKER, component, f"plist mal formada: {err or 'raiz não é dicionário'}", file=file,
              confidence=1.0, recommendation=f"Valide com 'plutil -lint {Path(file).name}' e corrija a estrutura (TN3181).")
        return c.items, facts
    pm = ref("privacy_manifest")
    errors = 0
    unknown = [k for k in pl if k not in pm["top_level_keys"]]
    if unknown:
        errors += 1
        c.add("PRIV-MANIFEST-002", BLOCKER, component, f"chaves de topo inválidas: {unknown}", file=file, confidence=0.95,
              recommendation="Use apenas NSPrivacyTracking, NSPrivacyTrackingDomains, NSPrivacyCollectedDataTypes e NSPrivacyAccessedAPITypes.")

    # --- tracking
    tr = pl.get("NSPrivacyTracking")
    doms = pl.get("NSPrivacyTrackingDomains")
    t_errs = []
    if tr is not None and not isinstance(tr, bool):
        t_errs.append(f"NSPrivacyTracking tem tipo {type(tr).__name__}, esperado Boolean")
    if doms is not None and (not isinstance(doms, list) or not all(isinstance(x, str) for x in doms)):
        t_errs.append("NSPrivacyTrackingDomains não é array de strings")
        doms = []
    doms = doms or []
    bad_doms = [d for d in doms if "/" in d or "?" in d or "://" in d or not DOMAIN_RE.match(d)]
    if bad_doms:
        t_errs.append(f"domínios mal formados (sem caminho, query ou barra final): {bad_doms}")
    if tr is True and not doms:
        t_errs.append("NSPrivacyTracking=true com NSPrivacyTrackingDomains vazio")
    facts["tracking"] = tr if isinstance(tr, bool) else None
    facts["tracking_domains"] = list(doms)
    if t_errs:
        errors += 1
        c.add("PRIV-TRACK-001", BLOCKER, component, "; ".join(t_errs), file=file, confidence=0.95,
              recommendation="Corrija tipos/valores conforme TN3181 (Boolean e array de domínios).")
    elif tr is not None or doms:
        c.add("PRIV-TRACK-001", PASS, component, f"NSPrivacyTracking={tr}, domínios={len(doms)}", file=file, confidence=1.0)

    # --- required reason APIs
    cats = ref("required_reason_categories")
    apis = pl.get("NSPrivacyAccessedAPITypes")
    if apis is not None:
        a_errs = []
        if not isinstance(apis, list):
            a_errs.append("NSPrivacyAccessedAPITypes não é array")
            apis = []
        for i, item in enumerate(apis):
            if not isinstance(item, dict):
                a_errs.append(f"item {i} não é dicionário")
                continue
            cat = item.get("NSPrivacyAccessedAPIType")
            reasons = item.get("NSPrivacyAccessedAPITypeReasons")
            if cat not in cats:
                a_errs.append(f"categoria inválida: {cat!r}")
                continue
            if not isinstance(reasons, list) or not reasons:
                a_errs.append(f"{cat}: NSPrivacyAccessedAPITypeReasons ausente ou vazio")
                continue
            bad = [r for r in reasons if r not in cats[cat]]
            if bad:
                a_errs.append(f"{cat}: motivos não aprovados para a categoria {bad} (válidos: {cats[cat]})")
            facts["api_categories"][cat] = [r for r in reasons if isinstance(r, str)]
        if a_errs:
            errors += 1
            c.add("PRIV-RRAPI-001", BLOCKER, component, "; ".join(a_errs), file=file, confidence=0.95,
                  recommendation="Use categorias e códigos de motivo da página 'Describing use of required reason API'.")
        else:
            c.add("PRIV-RRAPI-001", PASS, component, f"categorias declaradas: {sorted(facts['api_categories'])}", file=file, confidence=1.0)

    # --- collected data
    col = pl.get("NSPrivacyCollectedDataTypes")
    if col is not None:
        types = set(ref("collected_data_types"))
        purposes = set(ref("collected_data_purposes"))
        d_errs = []
        if not isinstance(col, list):
            d_errs.append("NSPrivacyCollectedDataTypes não é array")
            col = []
        for i, item in enumerate(col):
            if not isinstance(item, dict):
                d_errs.append(f"item {i} não é dicionário")
                continue
            t = item.get("NSPrivacyCollectedDataType")
            if t not in types:
                d_errs.append(f"tipo de dado inválido: {t!r}")
            for k in ("NSPrivacyCollectedDataTypeLinked", "NSPrivacyCollectedDataTypeTracking"):
                if not isinstance(item.get(k), bool):
                    d_errs.append(f"{t}: {k} ausente ou não Boolean")
            ps = item.get("NSPrivacyCollectedDataTypePurposes")
            if not isinstance(ps, list) or not ps:
                d_errs.append(f"{t}: NSPrivacyCollectedDataTypePurposes ausente ou vazio")
            else:
                badp = [p for p in ps if p not in purposes]
                if badp:
                    d_errs.append(f"{t}: finalidades inválidas {badp}")
            facts["collected"].append(t)
        if d_errs:
            errors += 1
            c.add("PRIV-DATA-001", BLOCKER, component, "; ".join(d_errs[:8]), file=file, confidence=0.95,
                  recommendation="Corrija conforme 'Describing data use in privacy manifests'.")
        else:
            c.add("PRIV-DATA-001", PASS, component, f"{len(col)} tipo(s) de dados declarados", file=file, confidence=1.0)

    facts["valid"] = errors == 0
    if errors == 0:
        c.add("PRIV-MANIFEST-002", PASS, component, "manifest bem formado; chaves e tipos válidos", file=file, confidence=0.95)
    return c.items, facts


def validate_manifest_file(path: Path, component: str = "app"):
    try:
        data = path.read_bytes()
    except OSError as exc:
        f = Finding("PRIV-MANIFEST-002", BLOCKER, component, f"não foi possível ler: {exc}", file=str(path))
        return [f], {"valid": False}
    return validate_manifest_bytes(data, component, str(path))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifest")
    ap.add_argument("--component", default="app")
    a = ap.parse_args(argv)
    findings, facts = validate_manifest_file(Path(a.manifest), a.component)
    print(json.dumps({"facts": facts, "findings": [f.to_dict(i + 1) for i, f in enumerate(findings)]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
