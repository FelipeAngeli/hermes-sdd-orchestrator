"""Official-source freshness checker (urllib, offline-safe).

Re-fetches every official URL referenced by the rule base, normalizes the
content (DocC JSON for /documentation/ pages, visible text for HTML) and
records HTTP status, fetch date and sha256 in a cache file with TTL. Reports
changes against the baseline captured when the rules were authored.

Isolated use:
  python3 check_sources.py --cache-dir DIR [--ttl-days 7] [--force] [--offline]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RULES_DIR, now_iso, rules  # noqa: E402

UA = "Mozilla/5.0 (appstore-review-guardian; read-only source check)"
CACHE_NAME = "sources-cache.json"
MAX_BYTES = 8 * 1024 * 1024


def fetch_url(url: str) -> str:
    """Map Apple DocC pages to their public JSON (the HTML is a JS shell)."""
    m = re.match(r"^https://developer\.apple\.com/documentation/(.+?)/?$", url)
    if m:
        return f"https://developer.apple.com/tutorials/data/documentation/{m.group(1).lower()}.json"
    return url.split("#", 1)[0]


def normalize(url: str, body: bytes) -> str:
    text = body.decode("utf-8", "replace")
    if url.endswith(".json"):
        try:
            d = json.loads(text)
            return json.dumps(d.get("primaryContentSections", d), sort_keys=True, ensure_ascii=False)
        except ValueError:
            return text
    m = re.search(r"<main.*?</main>", text, re.S)
    text = m.group(0) if m else text
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def default_fetch(url: str, timeout: int = 20) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https Apple URLs)
            return resp.status, resp.read(MAX_BYTES)
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def source_urls() -> list[str]:
    urls = set()
    for r in rules().rules.values():
        urls.add(r["official_url"].split("#", 1)[0])
    return sorted(urls)


def load_baseline() -> dict:
    p = RULES_DIR / "sources-baseline.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8")).get("sources", {})
    return {}


def check_sources(cache_dir: Path, ttl_days: int = 7, force: bool = False, offline: bool = False,
                  fetch=default_fetch, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cpath = cache_dir / CACHE_NAME
    cache = {}
    if cpath.is_file():
        try:
            cache = json.loads(cpath.read_text(encoding="utf-8")).get("sources", {})
        except ValueError:
            cache = {}
    baseline = load_baseline()
    results = {}
    summary = {"checked": 0, "reused": 0, "changed": 0, "errors": 0, "offline": 0}
    for url in source_urls():
        prev = cache.get(url)
        if prev and not force and prev.get("fetched_at") and prev.get("status") == 200:
            age = now - dt.datetime.fromisoformat(prev["fetched_at"].replace("Z", "+00:00"))
            if age.days < ttl_days:
                results[url] = dict(prev, reused=True)
                summary["reused"] += 1
                if prev.get("changed_since_baseline"):
                    summary["changed"] += 1
                continue
        if offline:
            results[url] = dict(prev or {}, status=prev.get("status") if prev else None, error="offline: não consultado", reused=bool(prev))
            summary["offline"] += 1
            continue
        furl = fetch_url(url)
        try:
            status, body = fetch(furl)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            results[url] = dict(prev or {}, error=f"rede indisponível: {type(exc).__name__}", reused=bool(prev))
            summary["offline"] += 1
            continue
        summary["checked"] += 1
        entry = {"fetch_url": furl, "status": status, "fetched_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "reused": False}
        if status == 200 and body:
            norm = normalize(furl, body)
            entry["sha256"] = hashlib.sha256(norm.encode("utf-8")).hexdigest()
            entry["bytes"] = len(body)
            base = baseline.get(url, {}).get("sha256")
            entry["baseline_sha256"] = base
            entry["changed_since_baseline"] = bool(base) and base != entry["sha256"]
            if entry["changed_since_baseline"]:
                summary["changed"] += 1
            m = re.search(r"Last Updated:?\s*(?:<[^>]+>)?\s*([A-Z][a-z]+ \d{1,2}, \d{4})", body.decode("utf-8", "replace"))
            if m:
                entry["published_version"] = m.group(1)
        else:
            entry["error"] = f"HTTP {status}"
            summary["errors"] += 1
        results[url] = entry
    out = {"checked_at": now_iso(), "ttl_days": ttl_days, "rules_consulted_at": rules().oldest_consultation(),
           "summary": summary, "sources": results}
    cpath.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    out["cache_file"] = str(cpath)
    return out


def freshness(cache_dir: Path | None, now: dt.date | None = None) -> dict:
    """Compact freshness info for reports (no network)."""
    now = now or dt.date.today()
    consulted = rules().oldest_consultation()
    age = (now - dt.date.fromisoformat(consulted)).days
    info = {"rules_consulted_at": consulted, "rules_age_days": age, "sources_cache": None}
    if cache_dir and (cache_dir / CACHE_NAME).is_file():
        try:
            d = json.loads((cache_dir / CACHE_NAME).read_text(encoding="utf-8"))
            info["sources_cache"] = {"checked_at": d.get("checked_at"), **d.get("summary", {})}
        except ValueError:
            pass
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--ttl-days", type=int, default=7)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)
    res = check_sources(Path(a.cache_dir), a.ttl_days, a.force, a.offline)
    print(json.dumps({"summary": res["summary"], "cache_file": res["cache_file"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
