"""Evaluation side of the data-plausibility audit (plan_llm_data_audit.md).

Holds the audit's node sets and the answer-key matchers from
comparison/audit_answer_key.md. Kept apart from audit_candidates.py and
(Phase 2) the auditor: nothing here may be imported by code that builds a
worklist or a prompt, so the answer key cannot shape what is audited.

Phase 1 measures *candidate recall*: how many answer-key items reach the
worklist at all, before any LLM runs. An item missing here cannot be
found by the auditor.

    python3 src/audit_eval.py candidates   # after audit_candidates.py
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from geo import haversine_m

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ANSWER_KEY = PROJECT_ROOT / "comparison" / "audit_answer_key.md"
WORKLIST_DIR = PROJECT_ROOT / "comparison" / "audit_runs" / "worklists"
UNREGISTERED_CSV = PROJECT_ROOT / "db" / "raw" / "calibration" / "worker_onsite_unregistered.csv"
CANDIDATE_RECALL_MD = PROJECT_ROOT / "comparison" / "audit_candidate_recall.md"

AUDIT_SETS: dict[str, tuple[str, ...]] = {
    "development": ("warschauer_strasse",),
    "holdout": ("brandenburger_tor",),
    "control": ("schoenhauser_allee",),
    # Drawn with random.Random(20261007).sample(pool, 3) -- see the key.
    "generalisation": ("Kurfürstendamm", "Rosenthaler Platz", "Deutsche Oper"),
}

# The 136-station pool the generalisation stations were drawn from.
DRAW_ANCHORS = PROJECT_ROOT / "db" / "raw" / "calibration" / "demand_anchors_20261002T092010Z.json"
DRAW_SEED = 20261007


def reference_station(name: str) -> dict:
    stations = json.loads(DRAW_ANCHORS.read_text())["stations"]
    return next(s for s in stations if s["station"] == name)


def redraw_generalisation_stations() -> list[str]:
    """Reproduces the key's draw; a test checks it still gives AUDIT_SETS."""
    import random

    from models import CANDIDATE_NODES

    names = {s["station"] for s in json.loads(DRAW_ANCHORS.read_text())["stations"]}
    pool = sorted(names - {n.name for n in CANDIDATE_NODES})
    return random.Random(DRAW_SEED).sample(pool, 3)


# --------------------------------------------------------------------------
# Answer-key matchers (Phase 1: does the item reach the worklist?)
# --------------------------------------------------------------------------


def _unregistered(area_id: str) -> list[dict]:
    with UNREGISTERED_CSV.open(encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["area_id"] == area_id]


def _refs(candidate: dict) -> set[str]:
    """Every record ref a candidate covers: its own, plus each member of a
    conflated competitor (a POI may sit in a cluster led by another record)."""
    refs = set(candidate["record_ref"].split("|"))
    for r in candidate.get("records") or [candidate.get("record") or {}]:
        refs.update(m["ref"] for m in r.get("members", []))
    return refs


def _by_ref(*refs: str):
    def match(wl: dict) -> list[str]:
        return [c["candidate_id"] for c in wl["candidates"] if _refs(c) & set(refs)]
    return match


def _by_title(title: str):
    def match(wl: dict) -> list[str]:
        return [c["candidate_id"] for c in wl["candidates"]
                if (c.get("record", {}).get("wikipedia_titles") or {}).get("dewiki") == title]
    return match


def _pair(a: str, b: str):
    def match(wl: dict) -> list[str]:
        out = []
        for c in wl["candidates"]:
            if c["module"] != "poi_duplicate":
                continue
            members = [{m["ref"] for m in r["members"]} for r in c["records"]]
            if (a in members[0] and b in members[1]) or (b in members[0] and a in members[1]):
                out.append(c["candidate_id"])
        return out
    return match


def _missing_near(points: list[tuple[float, float]], within_m: float, need: int):
    """missing_entity candidates within within_m of the verified points;
    caught if they cover at least `need` of the points."""
    def match(wl: dict) -> list[str]:
        hits, covered = set(), 0
        for lat, lon in points:
            near = [c["candidate_id"] for c in wl["candidates"] if c["module"] == "missing_entity"
                    and haversine_m(lat, lon, c["record"]["lat"], c["record"]["lon"]) <= within_m]
            covered += bool(near)
            hits.update(near)
        return sorted(hits) if covered >= need else []
    return match


def _missing_near_coverage(points: list[tuple[float, float]], within_m: float, wl: dict) -> int:
    return sum(
        any(c["module"] == "missing_entity" and haversine_m(lat, lon, c["record"]["lat"], c["record"]["lon"]) <= within_m
            for c in wl["candidates"])
        for lat, lon in points)


def answer_key_items() -> list[dict]:
    """The key's items with a Phase 1 matcher each. Records are named as
    in comparison/audit_answer_key.md."""
    d4 = [(float(r["lat"]), float(r["lon"])) for r in _unregistered("warschauer_strasse")]
    h2 = [(float(r["lat"]), float(r["lon"])) for r in _unregistered("brandenburger_tor")]
    return [
        {"id": "D1", "set": "development", "node": "warschauer_strasse", "primary": True,
         "match": _by_title("Berghain")},
        {"id": "D2", "set": "development", "node": "warschauer_strasse", "primary": True,
         "match": _by_ref("ihk:1126051271504")},
        {"id": "D3", "set": "development", "node": "warschauer_strasse", "primary": True,
         "match": _by_ref("ihk:146410147338")},
        {"id": "D4", "set": "development", "node": "warschauer_strasse", "primary": True,
         "match": _missing_near(d4, 100.0, 1)},
        {"id": "D5", "set": "development", "node": "warschauer_strasse", "primary": False,
         "match": _by_ref("ihk:1146940271134", "ihk:1315585271284")},
        {"id": "H1", "set": "holdout", "node": "brandenburger_tor", "primary": True,
         "match": _by_ref("ihk:17303106124")},
        {"id": "H2", "set": "holdout", "node": "brandenburger_tor", "primary": True,
         "match": _missing_near(h2, 50.0, 3), "coverage": (h2, 50.0)},
        {"id": "H3", "set": "holdout", "node": "brandenburger_tor", "primary": True,
         "match": _by_title("Reichstagsgebäude")},
        {"id": "H4", "set": "holdout", "node": "brandenburger_tor", "primary": True,
         "match": _by_ref("overture:311992c2-4d9a-425d-a613-c22c9838dfa2")},
        {"id": "H5", "set": "holdout", "node": "brandenburger_tor", "primary": True,
         "match": _by_ref("overture:e99aea2b-c4e5-4e25-a80a-0f62d57d87e2")},
        {"id": "H6", "set": "holdout", "node": "brandenburger_tor", "primary": True,
         "match": _pair("node/5912146257", "overture:5120cae2-6961-4b33-89ac-f53f02d99819")},
    ]


def candidate_recall(worklists: dict[str, dict], sets: tuple[str, ...] = ("development", "holdout")) -> list[dict]:
    rows = []
    for item in answer_key_items():
        if item["set"] not in sets:
            continue
        wl = worklists[item["node"]]
        hits = item["match"](wl)
        row = {"id": item["id"], "set": item["set"], "primary": item["primary"], "reached": bool(hits),
               "candidates": hits}
        if "coverage" in item:
            pts, within = item["coverage"]
            row["coverage"] = f"{_missing_near_coverage(pts, within, wl)}/{len(pts)} buildings within {within:.0f} m"
        rows.append(row)
    return rows


def load_worklists(directory: Path = WORKLIST_DIR) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))}


# Rule changes made after seeing development results, appended to the
# generated report. Holdout results are measured once per frozen version.
RULE_LOG = """
## Rule log

| Version | Change | Seen before the change | Effect |
| --- | --- | --- | --- |
| `audit_candidates_v1` | The plan's rules as written (§2) | — | Development 3 / 4: D4 missed. The building has 24 small IHK registrations within 40 m (485 people at class midpoints for 77,751 m² of floor area), so "no registration within 40 m" excludes it |
| `audit_candidates_v2` | `missing_entity`: a building of ≥ 5,000 m² floor area also qualifies when the IHK headcount within 40 m is below 1 per 100 m² (offices hold 1 workplace per 15–25 m²); residential, parking and church buildings excluded from the size rule | Development worklist only. Holdout recall had not been computed; the whole answer key had been read while writing the plan | Development 4 / 4. Holdout measured once, on v2. Re-run with the v1 rule afterwards: H2 coverage unchanged (9 / 22 buildings), so the holdout result does not depend on the change |

**Caveats.** H4 reaches the worklist through the non-food word "steakhouse", the same word the conflation_v2 rule uses (Amendment 1: in-sample twice). The thresholds in `audit_candidates.py` are the plan's; the person writing them had read the answer key, which the blocklist test (`tests/test_audit_prompt.py`) limits for the word list but cannot rule out for the rule design.
"""


def write_candidate_recall(worklists: dict[str, dict], path: Path = CANDIDATE_RECALL_MD) -> str:
    rows = candidate_recall(worklists, ("development", "holdout"))
    lines = [
        "# Data-Plausibility Audit — Phase 1: Candidate Recall",
        "",
        f"{datetime.now(timezone.utc).date().isoformat()} · Generated by `python3 src/audit_eval.py candidates` "
        "from the worklists in [audit_runs/worklists/](audit_runs/worklists/). No LLM involved: this is how many "
        "answer-key items ([audit_answer_key.md](audit_answer_key.md)) reach the worklist at all. An item that "
        "is not listed here cannot be found by the auditor.",
        "",
        "## Answer-key items",
        "",
        "| ID | Set | Primary | Reaches worklist | Via |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        via = ", ".join(f"`{c}`" for c in r["candidates"][:4]) + (" …" if len(r["candidates"]) > 4 else "")
        if "coverage" in r:
            via = (via + "; " if via else "") + r["coverage"]
        lines.append(f"| {r['id']} | {r['set']} | {'yes' if r['primary'] else 'secondary'} | "
                     f"{'**yes**' if r['reached'] else 'no'} | {via or '—'} |")
    for set_name in ("development", "holdout"):
        prim = [r for r in rows if r["set"] == set_name and r["primary"]]
        lines.append("")
        lines.append(f"- **{set_name}**: {sum(r['reached'] for r in prim)} / {len(prim)} primary items reach the worklist")
    lines += ["", RULE_LOG.strip()]
    lines += ["", "## Worklist size per node", "",
              "| Set | Node | concentration | large_registration | missing_entity | poi_category | poi_duplicate | Total |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    order = [n for nodes in AUDIT_SETS.values() for n in nodes]
    for wl in sorted(worklists.values(), key=lambda w: order.index(w["name"]) if w["name"] in order
                     else order.index(w["area_id"]) if w["area_id"] in order else 99):
        c = wl["counts"]
        lines.append(f"| {wl.get('set', '')} | {wl['name']} | {c['concentration']} | {c['large_registration']} | "
                     f"{c['missing_entity']} | {c['poi_category']} | {c['poi_duplicate']} | {len(wl['candidates'])} |")
    text = "\n".join(lines) + "\n"
    path.write_text(text)
    return text


if __name__ == "__main__":
    if sys.argv[1:] == ["candidates"]:
        print(write_candidate_recall(load_worklists()))
    else:
        print(__doc__)
