"""Dependency inventory for Flutter/CocoaPods projects (read-only, offline).

Sources: pubspec.lock, .flutter-plugins-dependencies, ios/Podfile.lock and,
when present on disk, the plugin/pod source folders (pub cache, ios/Pods)
to look for bundled PrivacyInfo.xcprivacy.

Isolated use:
  python3 inspect_dependencies.py <flutter-app-dir>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import NOT_APPLICABLE, NOT_VERIFIED, PASS, WARNING, Collector, Finding, read_text_safe, ref  # noqa: E402


def parse_pubspec_lock(text: str) -> dict:
    """Minimal parser for the pubspec.lock subset (packages + sdks)."""
    pkgs: dict[str, dict] = {}
    sdks: dict[str, str] = {}
    section = None
    cur = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0:
            section = line.rstrip(":")
            cur = None
            continue
        if section == "packages":
            if indent == 2 and line.endswith(":"):
                cur = line[:-1].strip('"')
                pkgs[cur] = {}
            elif indent == 4 and cur and ":" in line:
                k, v = line.split(":", 1)
                v = v.strip().strip('"')
                if v:
                    pkgs[cur][k.strip()] = v
        elif section == "sdks" and ":" in line:
            k, v = line.split(":", 1)
            sdks[k.strip()] = v.strip().strip('"')
    return {"packages": pkgs, "sdks": sdks}


def parse_pubspec_yaml(text: str) -> dict:
    """Extract name, version and direct dependency names (top-level YAML subset)."""
    out = {"name": None, "version": None, "dependencies": [], "dev_dependencies": [], "environment": {}}
    section = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.split(" #", 1)[0].rstrip()
        s = line.strip()
        if indent == 0:
            key, _, val = s.partition(":")
            section = key
            val = val.strip().strip("'\"")
            if key in ("name", "version") and val:
                out[key] = val
            continue
        if indent == 2 and section in ("dependencies", "dev_dependencies") and ":" in s:
            out[section].append(s.split(":", 1)[0].strip())
        elif indent == 2 and section == "environment" and ":" in s:
            k, v = s.split(":", 1)
            out["environment"][k.strip()] = v.strip().strip("'\"")
    return out


def parse_podfile_lock(text: str) -> dict[str, str]:
    pods: dict[str, str] = {}
    in_pods = False
    for raw in text.splitlines():
        if raw.startswith("PODS:"):
            in_pods = True
            continue
        if in_pods and raw and not raw.startswith(" "):
            break
        m = re.match(r"^  - \"?([^ (\"]+) \(([^)]+)\)", raw)
        if in_pods and m:
            name = m.group(1)
            pods.setdefault(name.split("/")[0], m.group(2).lstrip("= "))
    return pods


def _has_manifest(root: Path, max_files: int = 4000) -> str | None:
    if not root.is_dir():
        return None
    n = 0
    for p in root.rglob("*"):
        n += 1
        if n > max_files:
            break
        if p.name == "PrivacyInfo.xcprivacy":
            return str(p.relative_to(root))
    return None


def inventory(app_dir: Path) -> dict:
    ios = app_dir / "ios"
    inv: dict = {"pubspec": None, "lock": None, "ios_plugins": [], "pods": {}, "flutter_sdk_constraint": None, "dart_sdk_constraint": None}
    if (app_dir / "pubspec.yaml").is_file():
        inv["pubspec"] = parse_pubspec_yaml(read_text_safe(app_dir / "pubspec.yaml"))
    if (app_dir / "pubspec.lock").is_file():
        lock = parse_pubspec_lock(read_text_safe(app_dir / "pubspec.lock", 8 * 1024 * 1024))
        inv["lock"] = {k: v.get("version") for k, v in lock["packages"].items()}
        inv["lock_meta"] = lock["packages"]
        inv["flutter_sdk_constraint"] = lock["sdks"].get("flutter")
        inv["dart_sdk_constraint"] = lock["sdks"].get("dart")
    dep_file = app_dir / ".flutter-plugins-dependencies"
    if dep_file.is_file():
        try:
            data = json.loads(read_text_safe(dep_file, 4 * 1024 * 1024))
            for p in data.get("plugins", {}).get("ios", []):
                if not p.get("dev_dependency"):
                    inv["ios_plugins"].append({"name": p["name"], "path": p.get("path"), "native_build": p.get("native_build", True)})
        except ValueError:
            pass
    if (ios / "Podfile.lock").is_file():
        inv["pods"] = parse_podfile_lock(read_text_safe(ios / "Podfile.lock"))
    return inv


def check_dependencies(app_dir: Path, inv: dict | None = None) -> tuple[list[Finding], dict]:
    c = Collector()
    inv = inv or inventory(app_dir)
    listed = set(ref("sdks_requiring_manifest"))
    facades = ref("federated_facades")
    plugin_names = {p["name"] for p in inv["ios_plugins"]}
    lock = inv.get("lock") or {}
    checked: set[str] = set()

    # Flutter plugins embedded in iOS
    for p in inv["ios_plugins"]:
        name = p["name"]
        if name not in listed:
            continue
        checked.add(name)
        src = Path(p["path"]) if p.get("path") else None
        found = None
        if src and src.is_dir():
            for sub in ("ios", "darwin"):
                found = found or _has_manifest(src / sub)
            if found:
                c.add("PRIV-SDK-001", PASS, name, f"{name} {lock.get(name, '')}: {found} no código-fonte do plugin",
                      file=f"pub-cache/{src.name}", confidence=0.9)
            else:
                c.add("PRIV-SDK-001", WARNING, name, f"{name} {lock.get(name, '')}: nenhum PrivacyInfo.xcprivacy em ios/ ou darwin/ do plugin",
                      file=f"pub-cache/{src.name}", confidence=0.75,
                      recommendation=f"Atualize {name} ('flutter pub upgrade {name}') para versão com privacy manifest; obrigatório em app novo ou quando a atualização adiciona o SDK.")
        else:
            c.add("PRIV-SDK-001", NOT_VERIFIED, name, f"{name}: código-fonte do plugin indisponível (rode 'flutter pub get')", confidence=0.3)

    # Federated facades listed by Apple: the iOS implementation is what ships
    for facade, impl in facades.items():
        if facade.startswith("_") or facade not in listed or facade in checked:
            continue
        if facade in lock and facade not in plugin_names:
            c.add("PRIV-SDK-001", NOT_APPLICABLE, facade,
                  f"{facade} é fachada Dart sem código iOS; implementação embarcada: {impl if impl in plugin_names else 'não encontrada'}",
                  confidence=0.8)

    # CocoaPods
    pods_dir = app_dir / "ios" / "Pods"
    for pod, ver in sorted(inv.get("pods", {}).items()):
        if pod not in listed or pod in checked or pod in plugin_names:
            continue
        checked.add(pod)
        if pod == "Flutter":
            c.add("PRIV-SDK-001", NOT_VERIFIED, pod, "Flutter.framework: manifest verificado apenas no bundle compilado", confidence=0.3)
            continue
        src = pods_dir / pod
        if src.is_dir():
            found = _has_manifest(src)
            c.add("PRIV-SDK-001", PASS if found else WARNING, pod, f"pod {pod} {ver}: " + (found or "sem PrivacyInfo.xcprivacy em ios/Pods"),
                  file=f"ios/Pods/{pod}", confidence=0.85 if found else 0.7,
                  recommendation="" if found else f"Atualize o pod {pod} para versão com privacy manifest ('pod update {pod}').")
        else:
            c.add("PRIV-SDK-001", NOT_VERIFIED, pod, f"pod {pod} {ver}: ios/Pods ausente; rode 'pod install' ou audite o .ipa", confidence=0.3)

    facts = {
        "flutter_sdk_constraint": inv.get("flutter_sdk_constraint"),
        "dart_sdk_constraint": inv.get("dart_sdk_constraint"),
        "ios_plugins": sorted(plugin_names),
        "pods": inv.get("pods", {}),
        "listed_sdks_present": sorted(checked),
        "direct_dependencies": (inv.get("pubspec") or {}).get("dependencies", []),
    }
    return c.items, facts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("app_dir")
    a = ap.parse_args(argv)
    findings, facts = check_dependencies(Path(a.app_dir))
    print(json.dumps({"facts": facts, "findings": [f.to_dict(i + 1) for i, f in enumerate(findings)]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
