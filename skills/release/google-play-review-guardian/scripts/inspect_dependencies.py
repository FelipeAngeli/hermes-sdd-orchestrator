"""Dependency inventory (pubspec.lock, AAB dependencies.pb) with SDK categories.

Categories are *hints* for the Data safety / billing review. Presence of a
dependency never proves data collection (rule PRIV-SDK-DATA-DISCLOSURE).
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from common import INFO, NOT_VERIFIED, WARNING, FindingFactory, read_member

# package / maven-group prefix -> (category, possible data types to *review*)
SDK_CATALOG = {
    "firebase_analytics": ("analytics", ["App activity", "Device or other IDs"]),
    "com.google.firebase:firebase-analytics": ("analytics", ["App activity", "Device or other IDs"]),
    "firebase_crashlytics": ("crash", ["Crash logs", "Diagnostics", "Device or other IDs"]),
    "com.google.firebase:firebase-crashlytics": ("crash", ["Crash logs", "Diagnostics"]),
    "sentry_flutter": ("crash", ["Crash logs", "Diagnostics"]),
    "io.sentry": ("crash", ["Crash logs", "Diagnostics"]),
    "firebase_messaging": ("push", ["Device or other IDs"]),
    "onesignal_flutter": ("push", ["Device or other IDs", "App interactions"]),
    "com.onesignal": ("push", ["Device or other IDs", "App interactions"]),
    "google_mobile_ads": ("ads", ["Device or other IDs", "App interactions", "Approximate location"]),
    "com.google.android.gms:play-services-ads": ("ads", ["Device or other IDs", "App interactions"]),
    "facebook_app_events": ("analytics", ["App activity", "Device or other IDs"]),
    "com.facebook.android": ("analytics", ["App activity", "Device or other IDs"]),
    "appsflyer_sdk": ("attribution", ["Device or other IDs", "App activity"]),
    "adjust_sdk": ("attribution", ["Device or other IDs", "App activity"]),
    "mixpanel_flutter": ("analytics", ["App activity"]),
    "amplitude_flutter": ("analytics", ["App activity"]),
    "in_app_purchase": ("billing_play", ["Purchase history"]),
    "in_app_purchase_android": ("billing_play", ["Purchase history"]),
    "com.android.billingclient": ("billing_play", ["Purchase history"]),
    "purchases_flutter": ("billing_play", ["Purchase history"]),
    "flutter_stripe": ("payments_external", ["Financial info"]),
    "com.stripe": ("payments_external", ["Financial info"]),
    "pay": ("payments_google_pay", ["Financial info"]),
    "firebase_auth": ("auth", ["Email address", "User IDs"]),
    "flutter_appauth": ("auth", ["User IDs"]),
    "google_sign_in": ("auth", ["Email address", "Name", "User IDs"]),
    "sign_in_with_apple": ("auth", ["Email address", "Name", "User IDs"]),
    "geolocator": ("location", ["Approximate location", "Precise location"]),
    "location": ("location", ["Approximate location", "Precise location"]),
    "image_picker": ("media", ["Photos", "Videos"]),
    "camera": ("media", ["Photos", "Videos"]),
    "contacts_service": ("contacts", ["Contacts"]),
    "flutter_contacts": ("contacts", ["Contacts"]),
    "local_auth": ("biometric", []),
    "webview_flutter": ("webview", []),
    "flutter_inappwebview": ("webview", []),
}


def categorize(name: str) -> tuple[str, list[str]] | None:
    if name in SDK_CATALOG:
        return SDK_CATALOG[name]
    for k, v in SDK_CATALOG.items():
        if ":" in k or "." in k:
            if name.startswith(k):
                return v
    return None


def parse_dependencies_pb(buf: bytes) -> list[str]:
    """AppDependencies.library[].maven_library {group=1, artifact=2, version=5} (bundletool app_dependencies.proto)."""
    from inspect_aab import iter_fields, ProtoError
    out = []
    try:
        for fno, wt, lib in iter_fields(buf):
            if fno != 1 or wt != 2:
                continue
            for f2, w2, v2 in iter_fields(lib):
                if f2 == 1 and w2 == 2:
                    parts = {a: c for a, w, c in iter_fields(v2) if w == 2}
                    g = parts.get(1, b"").decode("utf-8", "replace")
                    a = parts.get(2, b"").decode("utf-8", "replace")
                    v = parts.get(5, b"").decode("utf-8", "replace")
                    if g and a:
                        out.append(f"{g}:{a}:{v}")
    except ProtoError:
        pass
    return out


def inspect_dependencies(pubspec: dict | None, aab_path: Path | None) -> dict:
    deps: list[dict] = []
    if pubspec and pubspec.get("present"):
        locked = pubspec.get("locked", {})
        for name, meta in (pubspec.get("dependencies") or {}).items():
            if meta.get("dev"):
                continue
            cat = categorize(name)
            deps.append({"name": name, "source": "pubspec", "version": (locked.get(name) or {}).get("version"),
                         "category": cat[0] if cat else None, "review_data_types": cat[1] if cat else []})
    maven: list[str] = []
    if aab_path is not None:
        try:
            with zipfile.ZipFile(aab_path) as zf:
                name = "BUNDLE-METADATA/com.android.tools.build.libraries/dependencies.pb"
                if name in zf.namelist():
                    maven = parse_dependencies_pb(read_member(zf, zf.getinfo(name), 8 * 1024 ** 2))
        except zipfile.BadZipFile:
            maven = []
    for coord in maven:
        cat = categorize(coord)
        if cat:
            deps.append({"name": coord, "source": "aab:dependencies.pb", "version": coord.rsplit(":", 1)[-1],
                         "category": cat[0], "review_data_types": cat[1]})
    return {"dependencies": deps, "maven_count": len(maven), "maven": maven[:2000]}


def dependency_findings(inv: dict, F: FindingFactory, component: str) -> list:
    fs = []
    if inv["dependencies"] or inv["maven_count"]:
        fs.append(F("POL-SDK-INDEX", NOT_VERIFIED, "data_safety", "SDKs não cruzados com o Google Play SDK Index",
                    component=component, method="manual",
                    evidence=f"{len(inv['dependencies'])} dependências categorizadas; {inv['maven_count']} bibliotecas Maven no bundle",
                    recommendation="Consulte https://play.google.com/sdks para cada SDK de terceiros (avisos de política/versões desatualizadas)."))
    return fs
