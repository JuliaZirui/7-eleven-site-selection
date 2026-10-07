"""Phase 0 of plan_llm_opening_hours.md: can shop websites fill the
missing opening hours at all? A go / no-go measurement, no LLM.

For every competitor in a scored category with no recorded hours in the
latest conflated snapshot, this reports:
  - whether any URL is known for it: OSM `website` / `contact:website` /
    `url` on its OSM member, or Overture `websites` on its Overture member
    (looked up by id in the release the snapshots came from, so the
    candidate set itself does not move);
  - whether that URL is allowed by robots.txt and returns an HTML page
    (HTTP 200, text/html);
  - whether the page already carries schema.org opening hours in JSON-LD
    (extractable without an LLM), or at least something that looks like
    hours in its text (what an LLM would have to read).

Go if at least GO_THRESHOLD of the candidates have a reachable, allowed
URL. Read-only: writes one raw JSON under db/raw/website_hours/, never
touches site_selection.db or any score.
    python3 hours_coverage.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from html import unescape
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from calibrate_anchors import OVERTURE_RELEASE, RING_BBOX
from day_types import COMPETITION_WINDOWS
from metrics_pipeline import latest_snapshot_id
from models import CANDIDATE_NODES

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "site_selection.db"
OUT_DIR = PROJECT_ROOT / "db" / "raw" / "website_hours"

USER_AGENT = "berlin-site-selection-prototype/0.2 (research prototype; opening-hours coverage check, one page per shop)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
TIMEOUT_S = 15
POLITE_DELAY_S = 1.0
MAX_PAGE_BYTES = 500_000
GO_THRESHOLD = 0.25

SCORED_CATEGORIES = frozenset().union(*(w.categories for w in COMPETITION_WINDOWS))
OSM_URL_TAGS = ("website", "contact:website", "url")

# Hours-like text: a time range ("7:00-19:00", "07.00 – 19.00 Uhr") or an
# hours heading. Only a signal that an LLM would have something to read.
TIME_RANGE = re.compile(r"\b\d{1,2}[:.]\d{2}\s*(?:uhr\s*)?[-–—]\s*\d{1,2}[:.]\d{2}", re.I)
HOURS_WORDS = re.compile(r"öffnungszeiten|opening hours|business hours|geöffnet", re.I)
HOURS_LINK = re.compile(r'<a\s[^>]*href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>', re.I | re.S)
HOURS_LINK_WORDS = re.compile(r"öffnungszeit|oeffnungszeit|opening|hours|kontakt|contact|impressum|standort|location", re.I)
JSONLD_BLOCK = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.I | re.S)


def candidates(conn: sqlite3.Connection) -> list[dict]:
    """Scored-category competitors with no hours, per candidate node,
    with the member refs needed to look up URLs."""
    out = []
    for node in CANDIDATE_NODES:
        sid = latest_snapshot_id(conn, node.area_id, "poi")
        for name, category, hours, payload in conn.execute(
            "SELECT name, category, opening_hours_raw, payload_json FROM observations "
            "WHERE snapshot_id = ? AND area_id = ? AND entity_type = 'poi'", (sid, node.area_id)):
            if hours or category not in SCORED_CATEGORIES:
                continue
            members = json.loads(payload)["members"]
            out.append({"area_id": node.area_id, "name": name, "category": category, "conflated_snapshot": sid,
                        "members": [{"source": m["source"], "ref": m["ref"]} for m in members]})
    return out


def osm_urls(conn: sqlite3.Connection, ref: str) -> list[str]:
    row = conn.execute(
        "SELECT o.payload_json FROM observations o JOIN source_snapshots s USING (snapshot_id) "
        "WHERE o.external_ref = ? AND o.entity_type = 'poi_source' AND s.source_name = 'osm_overpass' "
        "ORDER BY o.snapshot_id DESC LIMIT 1", (ref,)).fetchone()
    tags = json.loads(row[0]).get("tags", {}) if row else {}
    return [tags[t] for t in OSM_URL_TAGS if tags.get(t)]


def overture_websites(ids: list[str]) -> dict[str, list[str]]:
    """Overture `websites` by place id, from the pinned release; cached."""
    path = OUT_DIR / f"overture_websites_{OVERTURE_RELEASE}.json"
    cached = json.loads(path.read_text()) if path.exists() else {}
    missing = sorted(set(ids) - set(cached))
    if missing:
        import duckdb

        from overture_client import resolve_place_files

        s, w, n, e = RING_BBOX
        _, files = resolve_place_files(w, s, e, n, OVERTURE_RELEASE)
        con = duckdb.connect()
        con.execute("SET enable_progress_bar = false")
        con.execute("INSTALL httpfs; LOAD httpfs;")
        rows = con.execute(
            "SELECT id, websites FROM read_parquet(?) "
            "WHERE bbox.xmin <= ? AND bbox.xmax >= ? AND bbox.ymin <= ? AND bbox.ymax >= ? AND list_contains(?, id)",
            [files, e, w, n, s, missing]).fetchall()
        found = {i: list(ws or []) for i, ws in rows}
        cached.update({i: found.get(i, []) for i in missing})
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cached, ensure_ascii=False, indent=2))
    return {i: cached[i] for i in ids}


def _normalize_url(url: str) -> str:
    url = url.strip().split()[0].rstrip(";,")
    return url if re.match(r"https?://", url, re.I) else f"https://{url}"


def robots_allows(url: str, cache: dict[str, RobotFileParser | None], client: httpx.Client) -> bool | None:
    """True / False per robots.txt; None if robots.txt itself was
    unreachable (5xx / network error), which RFC 9309 treats as disallow."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in cache:
        rp = RobotFileParser()
        try:
            r = client.get(f"{origin}/robots.txt")
            if r.status_code >= 500:
                cache[origin] = None
            else:
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])  # 4xx: no rules, all allowed
                cache[origin] = rp
        except httpx.HTTPError:
            cache[origin] = None
        time.sleep(POLITE_DELAY_S)
    rp = cache[origin]
    return None if rp is None else rp.can_fetch(USER_AGENT, url)


def _has_jsonld_hours(html: str) -> bool:
    return any("openingHours" in block for block in JSONLD_BLOCK.findall(html))


def _fetch(url: str, client: httpx.Client) -> tuple[int | None, str, str, str | None]:
    """(status, final_url, html, error); html empty unless 200 text/html."""
    try:
        with client.stream("GET", url) as r:
            body = b""
            if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) >= MAX_PAGE_BYTES:
                        break
            return r.status_code, str(r.url), body.decode("utf-8", errors="replace"), None
    except httpx.HTTPError as exc:
        return None, url, "", f"{type(exc).__name__}: {exc}"[:200]
    finally:
        time.sleep(POLITE_DELAY_S)


def _hours_signals(html: str) -> tuple[bool, bool]:
    return _has_jsonld_hours(html), bool(TIME_RANGE.search(html) and HOURS_WORDS.search(html))


def hours_link(html: str, base_url: str) -> str | None:
    """The first same-domain link whose text or href names hours / contact
    -- the one extra page the pipeline would be allowed to read."""
    host = urlsplit(base_url).netloc.removeprefix("www.")
    for href, text in HOURS_LINK.findall(html):
        url = urljoin(base_url, unescape(href))
        if urlsplit(url).netloc.removeprefix("www.") != host or url.rstrip("/") == base_url.rstrip("/"):
            continue
        if HOURS_LINK_WORDS.search(href) or HOURS_LINK_WORDS.search(re.sub(r"<[^>]+>", " ", text)):
            return url.split("#")[0]
    return None


def check_url(url: str, robots: dict, client: httpx.Client) -> dict:
    """Homepage, then at most one hours/contact link if the homepage
    shows no hours -- the same reach the pipeline would have."""
    url = _normalize_url(url)
    out = {"url": url, "robots_allowed": None, "status": None, "html": False, "followed_url": None,
           "jsonld_hours": False, "hours_text": False, "hours_on": None, "error": None}
    allowed = robots_allows(url, robots, client)
    out["robots_allowed"] = allowed
    if not allowed:
        out["error"] = "robots.txt unreachable" if allowed is None else "disallowed by robots.txt"
        return out
    out["status"], out["final_url"], html, out["error"] = _fetch(url, client)
    out["html"] = bool(html)
    if not html:
        return out
    out["jsonld_hours"], out["hours_text"] = _hours_signals(html)
    if out["jsonld_hours"] or out["hours_text"]:
        out["hours_on"] = "homepage"
        return out
    link = hours_link(html, out["final_url"])
    if link and robots_allows(link, robots, client):
        status, _, sub_html, _ = _fetch(link, client)
        out["followed_url"] = link
        if status == 200 and sub_html:
            out["jsonld_hours"], out["hours_text"] = _hours_signals(sub_html)
            if out["jsonld_hours"] or out["hours_text"]:
                out["hours_on"] = "linked page"
    return out


def run(db_path: Path = DB_PATH) -> dict:
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    try:
        cands = candidates(conn)
        for c in cands:
            c["urls"] = [u for m in c["members"] if m["source"] == "osm_overpass" for u in osm_urls(conn, m["ref"])]
    finally:
        conn.close()
    ov_ids = [m["ref"].split(":", 1)[1] for c in cands for m in c["members"] if m["source"] == "overture_places"]
    websites = overture_websites(ov_ids)
    for c in cands:
        for m in c["members"]:
            if m["source"] == "overture_places":
                c["urls"] += websites.get(m["ref"].split(":", 1)[1], [])
        c["urls"] = list(dict.fromkeys(_normalize_url(u) for u in c["urls"] if u and u.strip()))

    # Each distinct URL is fetched once, however many shops share it.
    robots: dict = {}
    checked: dict[str, dict] = {}
    with httpx.Client(headers=HEADERS, timeout=TIMEOUT_S, follow_redirects=True) as client:
        for c in cands:
            for u in c["urls"]:
                if u not in checked:
                    checked[u] = check_url(u, robots, client)
                    print(f"  {c['area_id']:20s} {c['name'][:30]:30s} {u[:60]:60s} "
                          f"{checked[u]['status'] or checked[u]['error']}", flush=True)

    domains: dict[str, int] = {}
    for c in cands:
        for d in {urlsplit(u).netloc.removeprefix("www.") for u in c["urls"]}:
            domains[d] = domains.get(d, 0) + 1
    for c in cands:
        checks = [checked[u] for u in c["urls"]]
        ok = [x for x in checks if x["robots_allowed"] and x["status"] == 200 and x["html"]]
        c["has_url"] = bool(checks)
        c["reachable_allowed"] = bool(ok)
        c["jsonld_hours"] = any(x["jsonld_hours"] for x in ok)
        c["hours_text"] = any(x["hours_text"] for x in ok)
        c["shared_domain"] = any(domains[urlsplit(u).netloc.removeprefix("www.")] > 1 for u in c["urls"])

    def summarize(rows: list[dict]) -> dict:
        return {k: sum(1 for r in rows if r[k]) for k in
                ("has_url", "reachable_allowed", "jsonld_hours", "hours_text", "shared_domain")} | {"n": len(rows)}

    by_node = {n.area_id: summarize([c for c in cands if c["area_id"] == n.area_id]) for n in CANDIDATE_NODES}
    total = summarize(cands)
    share = total["reachable_allowed"] / total["n"] if total["n"] else 0.0
    out = {
        "computed_at": now.isoformat(),
        "overture_release": OVERTURE_RELEASE,
        "user_agent": USER_AGENT,
        "go_threshold": GO_THRESHOLD,
        "reachable_allowed_share": share,
        "decision": "go" if share >= GO_THRESHOLD else "no-go",
        "by_node": by_node,
        "total": total,
        "urls_checked": len(checked),
        "candidates": cands,
        "url_checks": checked,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"coverage_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    for area, s in {**by_node, "total": total}.items():
        print(f"{area:20s} n={s['n']:3d} url={s['has_url']:3d} reachable+allowed={s['reachable_allowed']:3d} "
              f"jsonld={s['jsonld_hours']:3d} hours-text={s['hours_text']:3d} shared-domain={s['shared_domain']:3d}")
    print(f"Reachable + allowed: {share:.0%} (threshold {GO_THRESHOLD:.0%}) -> {out['decision']}")
    print(f"-> {path.relative_to(PROJECT_ROOT)}")
    return out


if __name__ == "__main__":
    run()
