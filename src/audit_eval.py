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


# The worklist module(s) that frame a record as the item's issue type.
TYPE_MODULES: dict[str, tuple[str, ...]] = {
    "fame_vs_attendance": ("concentration",),
    "company_wide_headcount": ("concentration", "large_registration"),
    "missing_entity": ("missing_entity",),
    "category_error": ("poi_category",),
    "duplicate": ("poi_duplicate",),
}
# Amendment 2: the candidate rules for these items were revised after
# their candidate recall had been seen. Reported apart from the rest, and
# the holdout's main evidence is H1-H3 (H4-H6 are in-sample for the
# conflation_v2 rules, Amendment 1).
CONTAMINATED = frozenset({"H4", "H5"})
HOLDOUT_MAIN = ("H1", "H2", "H3")


def answer_key_items() -> list[dict]:
    """The key's items with a Phase 1 matcher each. Records are named as
    in comparison/audit_answer_key.md."""
    d4 = [(float(r["lat"]), float(r["lon"])) for r in _unregistered("warschauer_strasse")]
    h2 = [(float(r["lat"]), float(r["lon"])) for r in _unregistered("brandenburger_tor")]
    return [
        {"id": "D1", "type": "fame_vs_attendance", "set": "development", "node": "warschauer_strasse", "primary": True,
         "match": _by_title("Berghain")},
        {"id": "D2", "type": "company_wide_headcount", "set": "development", "node": "warschauer_strasse", "primary": True,
         "match": _by_ref("ihk:1126051271504")},
        {"id": "D3", "type": "company_wide_headcount", "set": "development", "node": "warschauer_strasse", "primary": True,
         "match": _by_ref("ihk:146410147338")},
        {"id": "D4", "type": "missing_entity", "set": "development", "node": "warschauer_strasse", "primary": True,
         "match": _missing_near(d4, 100.0, 1)},
        {"id": "D5", "type": "company_wide_headcount", "set": "development", "node": "warschauer_strasse", "primary": False,
         "match": _by_ref("ihk:1146940271134", "ihk:1315585271284")},
        {"id": "H1", "type": "company_wide_headcount", "set": "holdout", "node": "brandenburger_tor", "primary": True,
         "match": _by_ref("ihk:17303106124")},
        {"id": "H2", "type": "missing_entity", "set": "holdout", "node": "brandenburger_tor", "primary": True,
         "match": _missing_near(h2, 50.0, 3), "coverage": (h2, 50.0)},
        {"id": "H3", "type": "fame_vs_attendance", "set": "holdout", "node": "brandenburger_tor", "primary": True,
         "match": _by_title("Reichstagsgebäude")},
        {"id": "H4", "type": "category_error", "set": "holdout", "node": "brandenburger_tor", "primary": True,
         "match": _by_ref("overture:311992c2-4d9a-425d-a613-c22c9838dfa2")},
        {"id": "H5", "type": "category_error", "set": "holdout", "node": "brandenburger_tor", "primary": True,
         "match": _by_ref("overture:e99aea2b-c4e5-4e25-a80a-0f62d57d87e2")},
        {"id": "H6", "type": "duplicate", "set": "holdout", "node": "brandenburger_tor", "primary": True,
         "match": _pair("node/5912146257", "overture:5120cae2-6961-4b33-89ac-f53f02d99819")},
    ]


def candidate_recall(worklists: dict[str, dict], sets: tuple[str, ...] = ("development", "holdout")) -> list[dict]:
    rows = []
    for item in answer_key_items():
        if item["set"] not in sets:
            continue
        wl = worklists[item["node"]]
        hits = item["match"](wl)
        typed = [h for h in hits if h.split(":", 1)[0] in TYPE_MODULES[item["type"]]]
        row = {"id": item["id"], "set": item["set"], "type": item["type"], "primary": item["primary"],
               "reached": bool(hits), "reached_as_type": bool(typed), "candidates": hits,
               "contaminated": item["id"] in CONTAMINATED}
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
| `audit_candidates_v2` | `missing_entity`: a building of ≥ 5,000 m² floor area also qualifies when the IHK headcount within 40 m is below 1 per 100 m² (offices hold 1 workplace per 15–25 m²); residential, parking and church buildings excluded from the size rule | Development worklist only. Holdout recall had not been computed; the whole answer key had been read while writing the plan | Development 4 / 4. Holdout measured once, on v2: 6 / 6 (H2 9 / 22 buildings; H4 via the word "steakhouse"; H5 via a same-name OSM building). Re-run with the v1 rule afterwards: H2 coverage unchanged |
| `audit_candidates_v3` | Non-food word list replaced by words derived from OSM tag values only (Amendment 2); the v2 list's examples came from answer items H4/H5 | **Holdout candidate recall on v2** (above) | H4 is no longer a `poi_category` candidate; its record reaches the worklist only inside a `poi_duplicate` pair. H5 unchanged. **Rules frozen from here** (Amendment 2): bug fixes only, each logged below |

**Caveats.** The thresholds in `audit_candidates.py` are the plan's; the person writing them had read the answer key, which the blocklist test (`tests/test_audit_prompt.py`) limits for the word list but cannot rule out for the rule design. H4 and H5 are contaminated (Amendment 2) and reported apart; H1–H3 are the holdout's main evidence.
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
        "| ID | Set | Type | Primary | Reaches worklist | In a module of its type | Via |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        via = ", ".join(f"`{c}`" for c in r["candidates"][:4]) + (" …" if len(r["candidates"]) > 4 else "")
        if "coverage" in r:
            via = (via + "; " if via else "") + r["coverage"]
        label = r["id"] + (" (contaminated)" if r["contaminated"] else "")
        lines.append(f"| {label} | {r['set']} | {r['type']} | {'yes' if r['primary'] else 'secondary'} | "
                     f"{'**yes**' if r['reached'] else 'no'} | {'yes' if r['reached_as_type'] else '**no**'} | "
                     f"{via or '—'} |")

    def tally(ids) -> str:
        sel = [r for r in rows if r["id"] in ids]
        return (f"{sum(r['reached'] for r in sel)} / {len(sel)} reach the worklist, "
                f"{sum(r['reached_as_type'] for r in sel)} / {len(sel)} in a module of their type")

    dev = [r["id"] for r in rows if r["set"] == "development" and r["primary"]]
    lines += ["",
              f"- **development** (primary): {tally(dev)}",
              f"- **holdout, main evidence H1–H3**: {tally(HOLDOUT_MAIN)}",
              f"- holdout H6 (in-sample for conflation_v2, Amendment 1): {tally(('H6',))}",
              f"- holdout H4–H5 (**contaminated**, Amendment 2): {tally(sorted(CONTAMINATED))}"]
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
