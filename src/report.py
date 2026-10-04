"""Day 5: render the sourced shortlist report and record the run.

Pipeline position (project_plan_v3.md workflow): "Compute area
comparison results" -> "Generate sourced report" -> "Verify figures &
citations". Scoring and sensitivity come from scoring.py; this module
turns them into templates/report_template.md, verifies every cited
figure against area_metrics, and writes one analysis_runs row holding
the store hypothesis, inputs, snapshot ids, weights, model version,
Day 4 tool log, and report path.

Differentiation hypotheses are rule-generated from the metrics (fixed
thresholds below), not free-form text -- each carries the figures it
rests on, so the report never states a claim the data can't trace.

Run directly:
    python3 report.py
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from string import Template

from geo import haversine_m
from metrics_pipeline import latest_snapshot_id
from models import CANDIDATE_NODES, DEFAULT_TIME_WINDOWS
from poi_taxonomy import DIRECT_FORMAT_CATEGORIES as DIRECT_FORMATS
from scoring import (
    BUSINESS_FIT_CRITERIA,
    DEFAULT_WEIGHTS,
    EQ_ADEQUATE,
    PEOPLE_UNIT,
    PILLARS,
    SCORING_MODEL_VERSION,
    UNSCORED_DIMENSIONS,
    WEEKDAY,
    _get,
    score_all,
    sensitivity_analysis,
    competition_structure_check,
    day_type_check,
    visitor_conversion_check,
    worker_onsite_check,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "site_selection.db"
TEMPLATE_PATH = PROJECT_ROOT / "templates" / "report_template.md"
REPORTS_DIR = PROJECT_ROOT / "reports"
AGENT_LOG_DIR = PROJECT_ROOT / "db" / "raw" / "agent_log"
STORE_HYPOTHESIS_PATH = PROJECT_ROOT / "store_hypothesis.md"

# A node that tops at least this share of uniform random weightings is
# a genuine contender for #1, whatever the baseline weights say.
PRIORITY_TOP_SHARE = 0.20

# Differentiation-rule thresholds.
# Peak-competition criterion score (0-100) at or above which a peak is
# called under-supplied for its demand, and at or below which saturated.
PEAK_UNDERSUPPLIED_SCORE = 60
PEAK_SATURATED_SCORE = 20
# Format-mix rules need at least this many competitors to say anything.
MIN_FORMAT_SAMPLE = 5
# A demand segment holding at least this share of a node's people per
# day sets its lead offer.
SEGMENT_LEAD_SHARE = 0.40
OFFICE_SHARE_NOTABLE = 0.50
# Share-weighted annual pageviews of night/evening venues at which the
# late-evening flow is called out (~ a top-10 Berlin club or arena).
NIGHT_DRAW_NOTABLE = 100_000
FORMAT_DOMINANT_SHARE = 0.50
NEAR_DIRECT_COMPETITOR_M = 200  # roughly the walk from a station exit to the next block

PILLAR_LABELS = {"demand": "Demand (people/day)", "competition": "Competition per 10k people"}

# What each peak's competitor set holds and what to lead with, by peak.
PEAK_RULES = {
    "morning_peak_competition": ("7–9 peak", "bakeries, cafés, supermarkets and fuel shops",
                                 "breakfast / coffee-to-go as the lead commuter offer",
                                 "coffee and pastry alone won't differentiate; compete on speed and the fuller basket"),
    "evening_peak_competition": ("16–19 peak", "spätis, fast food, supermarkets and fuel shops",
                                 "ready meals and the after-work top-up basket",
                                 "presence alone won't differentiate; compete on fresh / hot food range"),
    "weekend_day_competition": ("weekend 10–19", "food formats open (Sunday law applied)",
                                "the Sunday basket — a station shop may trade when most shops must close",
                                "weekend daytime is crowded; differentiate on range, not opening"),
    "weekend_night_competition": ("weekend 22–02", "spätis, fast food and fuel shops",
                                  "late-night hours and an after-club / after-event range",
                                  "the night is already served; compete on quality and speed"),
}
# Weekend people at least this multiple of weekday people = weekend-led node.
WEEKEND_LED_RATIO = 1.2


def _fmt(v: float | None, digits: int = 1) -> str:
    return "n/a" if v is None else f"{v:,.{digits}f}"


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def _weights_str(w: dict[str, float]) -> str:
    return " / ".join(f"{PILLAR_LABELS[p]} {w[p]:.0%}" for p in PILLARS)


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


def _store_hypothesis() -> dict:
    """Parse the 'Store Assumptions' table from store_hypothesis.md so the
    analysis_runs row records exactly what the document says, rather than
    a second hand-copied version of it."""
    text = STORE_HYPOTHESIS_PATH.read_text()
    section = text.split("## Store Assumptions", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("|")][2:]
    assumptions = {}
    for line in rows:
        cells = [c.strip() for c in line.strip("|").split("|")]
        assumptions[cells[0]] = cells[1]
    return {
        "source": str(STORE_HYPOTHESIS_PATH.relative_to(PROJECT_ROOT)),
        "assumptions": assumptions,
        "time_windows": [
            {"label": w.label.value, "start_hour": w.start_hour, "end_hour": w.end_hour} for w in DEFAULT_TIME_WINDOWS
        ],
    }


def _latest_agent_log() -> tuple[str | None, list]:
    logs = sorted(AGENT_LOG_DIR.glob("*.json"))
    if not logs:
        return None, []
    return str(logs[-1].relative_to(PROJECT_ROOT)), json.loads(logs[-1].read_text())


def _pois(conn: sqlite3.Connection, area_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT name, category, lat, lon, opening_hours_raw FROM observations "
        "WHERE area_id = ? AND entity_type = 'poi' AND snapshot_id = ?",
        (area_id, latest_snapshot_id(conn, area_id, "poi")),
    ).fetchall()
    return [{"name": r[0], "category": r[1], "lat": r[2], "lon": r[3], "hours": r[4]} for r in rows]


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def verify_citations(conn: sqlite3.Connection, results: dict[str, dict]) -> list[dict]:
    """Re-read every metric_id a criterion cites and confirm it still
    exists, belongs to the right area, and holds the value scoring used.
    Raises on any mismatch -- a report is never written on top of a
    figure that doesn't trace."""
    citations = []
    for area_id, r in results.items():
        for c in r["criteria"]:
            for metric_id in c["metric_ids"]:
                row = conn.execute(
                    "SELECT area_id, metric_name, period_label, metric_value, source_snapshot_ids_json "
                    "FROM area_metrics WHERE metric_id = ?",
                    (metric_id,),
                ).fetchone()
                if row is None or row[0] != area_id:
                    raise AssertionError(f"{area_id}/{c['key']}: cited metric_id {metric_id} does not trace")
                cached = next(m for m in r["metrics"].values() if m["metric_id"] == metric_id)
                if cached["value"] != row[3]:
                    raise AssertionError(f"{area_id}/{c['key']}: metric_id {metric_id} value changed during run")
                citations.append(
                    {
                        "area_id": area_id,
                        "criterion": c["key"],
                        "metric_id": metric_id,
                        "metric_name": row[1],
                        "period_label": row[2],
                        "value": row[3],
                        "snapshot_ids": json.loads(row[4]),
                    }
                )
    return citations


# --------------------------------------------------------------------------
# Narrative sections (rule-based)
# --------------------------------------------------------------------------


def _differentiation(conn: sqlite3.Connection, node, r: dict) -> list[str]:
    m = r["metrics"]
    crit = {c["key"]: c for c in r["criteria"]}
    out = []

    total_row = _get(m, "competitor_count_total", "current")
    if total_row is None or total_row["is_missing"]:
        return [f"No competitor data for this node ({(total_row or {}).get('missing_reason', 'not computed')}) — "
                "no differentiation hypothesis can be drawn."]
    total = total_row["value"]

    for key, (window, formats, lead, saturated) in PEAK_RULES.items():
        c = crit[key]
        if c["score"] is None:
            continue
        per = "weekend day" if c["day_type"] == "weekend" else "weekday"
        figures = (f"{c['count']:.0f} {formats} serve {window.replace(' peak', '')} for ~{c['people']:,.0f} people "
                   f"per {per} ({c['raw_value']:.1f} per {PEOPLE_UNIT:,})")
        if c["score"] >= PEAK_UNDERSUPPLIED_SCORE:
            out.append(f"**{window[0].upper()}{window[1:]} under-supplied for its demand** — {figures}. Test {lead}.")
        elif c["score"] <= PEAK_SATURATED_SCORE:
            out.append(f"**{window[0].upper()}{window[1:]} saturated for its demand** — {figures}. "
                       f"{saturated[0].upper()}{saturated[1:]}.")

    for p in _pois(conn, node.area_id):
        if p["category"] in DIRECT_FORMATS and p["hours"] == "24/7":
            d = haversine_m(node.lat, node.lon, p["lat"], p["lon"])
            if d <= NEAR_DIRECT_COMPETITOR_M:
                out.append(
                    f"**24/7 direct-format competitor near the anchor** — {p['name']} ({p['category']}) is {d:.0f}m "
                    f"away and never closes. Differentiate on fresh-food quality and speed, not availability."
                )

    fast_food = _get(m, "competitor_count_fast_food", "current")["value"]
    breakfast = _get(m, "competitor_count_bakery", "current")["value"] + _get(m, "competitor_count_cafe", "current")["value"]
    if total >= MIN_FORMAT_SAMPLE and fast_food / total >= FORMAT_DOMINANT_SHARE:
        out.append(
            f"**Ready-to-eat set is fast-food-led** — {fast_food:.0f} of {total:.0f} competitors are fast food. "
            f"Fresh, lighter grab-and-go (sandwiches, onigiri, salads) is the plausible gap."
        )
    if total >= MIN_FORMAT_SAMPLE and breakfast / total >= FORMAT_DOMINANT_SHARE:
        out.append(
            f"**Breakfast is contested** — {breakfast:.0f} of {total:.0f} competitors are bakeries or cafés. "
            f"Coffee and pastry alone won't differentiate; the convenience basket (emergency items, "
            f"late hours, meal deals) has to."
        )

    people = crit["daily_people"]
    seg = people.get("breakdown") or {}
    if people["raw_value"]:
        shares = {k: v / people["raw_value"] for k, v in seg.items() if v is not None}
        office_share = _get(m, "office_business_share", "current")
        office_share = office_share["value"] if office_share and not office_share["is_missing"] else None
        if shares.get("visitors", 0) >= SEGMENT_LEAD_SHARE:
            out.append(
                f"**Visitor-led catchment** — ~{seg['visitors']:,.0f} of ~{people['raw_value']:,.0f} people/day "
                f"({shares['visitors']:.0%}) are estimated visitors. Test grab-and-go water, snacks and "
                f"souvenir-adjacent impulse lines; visitor flow builds after 9–10, not at the commuter peak."
            )
        if shares.get("workers", 0) >= SEGMENT_LEAD_SHARE:
            out.append(
                f"**Office-commuter base** — ~{seg['workers']:,.0f} people work within 400m ({shares['workers']:.0%} "
                f"of people/day; {_pct(office_share)} of registrations office-type). Test a weekday lunch meal-deal range."
            )
        if shares.get("residents", 0) >= SEGMENT_LEAD_SHARE:
            out.append(
                f"**Residential catchment** — ~{seg['residents']:,.0f} residents within 400m ({shares['residents']:.0%} "
                f"of people/day). Daily top-up basket and weekend trade matter here, not only commuter peaks."
            )
    wd, we = crit["daily_people"], crit["daily_people_weekend"]
    if wd["raw_value"] and we["raw_value"]:
        ratio = we["raw_value"] / wd["raw_value"]
        if ratio >= WEEKEND_LED_RATIO:
            vis = (we.get("breakdown") or {}).get("visitors")
            out.append(
                f"**Weekend-led demand** — ~{we['raw_value']:,.0f} people on a weekend day vs. ~{wd['raw_value']:,.0f} on a "
                f"weekday ({ratio:.1f}×), driven by visitors ({_fmt(vis, 0)} per weekend day). Staff and stock for "
                f"Friday–Sunday; the weekday commuter base alone understates this node."
            )
        elif ratio <= 1 / WEEKEND_LED_RATIO:
            out.append(
                f"**Weekday-led demand** — ~{wd['raw_value']:,.0f} people on a weekday vs. ~{we['raw_value']:,.0f} on a "
                f"weekend day ({1 / ratio:.1f}×). A commuter format; weekend trade will be thin."
            )
    night = _get(m, "night_venue_draw_index")
    if night and not night["is_missing"] and night["value"] >= NIGHT_DRAW_NOTABLE:
        out.append(
            f"**Late-evening visitor flow** — clubs and event venues are reached via this station "
            f"({night['value']:,.0f} share-weighted annual pageviews). Extended or 24h hours and a pre/after-event "
            f"drinks range are the lever."
        )

    if not out:
        out.append("No rule-based differentiation signal fired for this node.")
    return out


def _under_mapping_flags(results: dict[str, dict]) -> dict[str, str]:
    flags = {}
    for area_id, r in results.items():
        c = r.get("mapping_consistency")
        if c and c["flagged"]:
            flags[area_id] = (
                f"Possible under-mapping: {c['osm']:.0f} mapped fresh-food competitors (OSM + Overture) vs. {c['ihk_gastro']} "
                f"IHK-registered Gastronomie businesses in the same 400m ({c['ratio']:.1%}, under half the best "
                f"node's {c['best_ratio']:.1%}). This node's competition score may partly be a mapping artifact."
            )
    return flags


def _evidence_gap_events(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Agent evidence-gap flags raised on the area's *current* data: a flag
    recorded before the area's latest competitor snapshot was fetched is
    about superseded data (e.g. the v1 POI set) and is left out."""
    rows = conn.execute(
        "SELECT area_id, description, recorded_at FROM development_events WHERE event_type = 'evidence_gap' ORDER BY event_id"
    ).fetchall()
    current_since: dict[str, str | None] = {}
    out: dict[str, list[str]] = {}
    for area_id, desc, recorded_at in rows:
        if area_id not in current_since:
            snap = latest_snapshot_id(conn, area_id, "poi")
            current_since[area_id] = None if snap is None else conn.execute(
                "SELECT fetched_at FROM source_snapshots WHERE snapshot_id = ?", (snap,)).fetchone()[0]
        since = current_since[area_id]
        if since is not None and recorded_at < since:
            continue
        if desc not in out.setdefault(area_id, []):
            out[area_id].append(desc)
    return out


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _priority_call(area_id: str, r: dict, sens: dict) -> str:
    contender = sens["monte_carlo"]["top_share"][area_id] >= PRIORITY_TOP_SHARE
    eq = r["evidence_quality"]
    if contender and eq["score"] >= EQ_ADEQUATE:
        return "Prioritize for field diligence"
    if contender:
        return "Prioritize — verify evidence first"
    return "Lower priority (control node)"


def render(conn: sqlite3.Connection, run_id: int, run_at: datetime) -> tuple[str, dict]:
    names = {n.area_id: n.name for n in CANDIDATE_NODES}
    area_ids = [n.area_id for n in CANDIDATE_NODES]
    results = score_all(conn, area_ids, DEFAULT_WEIGHTS, now=run_at)
    sens = sensitivity_analysis(results, DEFAULT_WEIGHTS)
    citations = verify_citations(conn, results)
    mc = sens["monte_carlo"]
    order = sens["base_rank"]

    under_mapped = _under_mapping_flags(results)

    # §2 summary
    summary_rows = []
    for a in order:
        r = results[a]
        eq = r["evidence_quality"]
        summary_rows.append(
            f"| {names[a]} | {_fmt(r['business_fit'])} | {_fmt(eq['score'])} ({eq['tier']}) | "
            f"{_pct(mc['top_share'][a])} | {_priority_call(a, r, sens)} |"
        )

    contenders = [a for a in order if mc["top_share"][a] >= PRIORITY_TOP_SHARE]
    never_top = [a for a in order if mc["top_share"][a] == 0]
    data_flips = []
    vflip = visitor_conversion_check(results, DEFAULT_WEIGHTS)
    if vflip and any(vflip[l]["rank"][0] != order[0] for l in ("low", "high")):
        data_flips.append("the visitor-conversion range")
    wflip = worker_onsite_check(results, DEFAULT_WEIGHTS)
    if any(wflip[l]["rank"][0] != order[0] for l in ("low", "high")):
        data_flips.append("the verified worker range")
    if sens["top_rank_robust"] and data_flips:
        headline = (
            f"**{names[order[0]]}** ranks first under every weighting tested, but not across "
            f"{' or '.join(data_flips)}: at the low end, {names[order[1]]} is first. The order rests on the demand "
            f"estimate more than on the weights — measured footfall would settle it."
        )
    elif sens["top_rank_robust"]:
        headline = f"**{names[order[0]]}** ranks first under every weighting tested and is the clear diligence priority."
    else:
        headline = (
            f"**No single winner survives the sensitivity analysis.** "
            f"{' and '.join(names[a] for a in contenders)} trade places depending on whether demand or competition "
            f"is weighted more heavily — this is a strategic choice for the client, not something the data settle. "
        )
        if never_top:
            headline += (
                f"{' and '.join(names[a] for a in never_top)} ranks first in none of the "
                f"{mc['draws']:,} random weightings and is the clearest deprioritization."
            )

    # §3 criteria
    header = "| Pillar | Criterion | Scale (0 → 100) | " + " | ".join(names[a] for a in order) + " |"
    sep = "| --- | --- | --- | " + " | ".join("---" for _ in order) + " |"
    lines = [header, sep]
    for c in BUSINESS_FIT_CRITERIA:
        cells = []
        for a in order:
            cr = next(x for x in results[a]["criteria"] if x["key"] == c.key)
            if cr["raw_value"] is None:
                cells.append(f"missing — {cr['missing_reason']}")
            elif c.per_demand:
                cells.append(f"{cr['count']:.0f} ÷ {cr['people'] / PEOPLE_UNIT:.2f} → {cr['raw_value']:.1f} → **{cr['score']:.0f}**")
            elif cr.get("breakdown"):
                b = cr["breakdown"]
                cells.append(f"{b['residents']:,.0f} + {b['workers']:,.0f} + {b['visitors']:,.0f} = "
                             f"{cr['raw_value']:,.0f} → **{cr['score']:.0f}**")
            else:
                raw = _pct(cr["raw_value"]) if c.unit == "share" else _fmt(cr["raw_value"], 0 if c.unit in ("count", "persons", "m", "pageviews") else 1)
                cells.append(f"{raw} → **{cr['score']:.0f}**")
        scale = f"{c.worst:,g} → {c.best:,g} {c.unit}" + (" (log)" if c.log_scale else "") + (f", in-pillar weight ×{c.weight:g}" if c.weight != 1 else "")
        lines.append(f"| {PILLAR_LABELS[c.pillar]} | {c.label} | {scale} | " + " | ".join(cells) + " |")
    for p in PILLARS:
        lines.append(
            f"| **{PILLAR_LABELS[p]}** | pillar score | weight {DEFAULT_WEIGHTS[p]:.0%} | "
            + " | ".join(f"**{_fmt(results[a]['pillars'][p])}**" for a in order)
            + " |"
        )
    lines.append(
        "| **Business fit** | weighted | | " + " | ".join(f"**{_fmt(results[a]['business_fit'])}**" for a in order) + " |"
    )
    criteria_table = "\n".join(lines)
    criteria_table += "\n\nCriterion rationale:\n\n" + "\n".join(
        f"- *{c.label}* — {c.rationale}" for c in BUSINESS_FIT_CRITERIA
    )

    # Evidence components
    comp_keys = sorted({k for a in order for k in results[a]["evidence_quality"]["components"]})
    ev_lines = ["| Component | " + " | ".join(names[a] for a in order) + " |", "| --- | " + " | ".join("---" for _ in order) + " |"]
    for k in comp_keys:
        cells = []
        for a in order:
            comp = results[a]["evidence_quality"]["components"].get(k)
            cells.append("n/a" if comp is None else f"{comp['value']:.0%} — {comp['detail']}")
        ev_lines.append(f"| {k.replace('_', ' ')} | " + " | ".join(cells) + " |")
    ev_lines.append(
        "| **Evidence quality** | "
        + " | ".join(f"**{_fmt(results[a]['evidence_quality']['score'])}** ({results[a]['evidence_quality']['tier']})" for a in order)
        + " |"
    )
    evidence_table = "\n".join(ev_lines)

    # §4 sensitivity
    sc_lines = ["| Scenario | Weights | Ranking | " + " | ".join(names[a] for a in order) + " |", "| --- | --- | --- | " + " | ".join("---" for _ in order) + " |"]
    for s in sens["scenarios"]:
        sc_lines.append(
            f"| {s['name']} | {_weights_str(s['weights'])} | {' > '.join(names[a] for a in s['rank'])} | "
            + " | ".join(_fmt(s["fits"][a]) for a in order)
            + " |"
        )
    oat_lines = ["| Pillar | Factor | Resulting weights | Ranking | #1 changed? |", "| --- | --- | --- | --- | --- |"]
    for o in sens["one_at_a_time"]:
        oat_lines.append(
            f"| {PILLAR_LABELS[o['pillar']]} | ×{o['factor']} | {_weights_str(o['weights'])} | "
            f"{' > '.join(names[a] for a in o['rank'])} | {'**yes**' if o['top_changed'] else 'no'} |"
        )
    mc_lines = ["| Node | Ranked #1 | Mean rank |", "| --- | --- | --- |"]
    for a in order:
        mc_lines.append(f"| {names[a]} | {_pct(mc['top_share'][a])} | {mc['mean_rank'][a]:.2f} |")
    minmax_rank = " > ".join(names[a] for a in sens["minmax_check"]["rank"])
    if sens["minmax_check"]["rank"] == order:
        minmax_rank += " — same order as the anchored baseline, so the ranking is not an artifact of the anchor choice"
    else:
        minmax_rank += " — **differs** from the anchored baseline; the anchor values themselves are driving part of the result"

    dcheck = day_type_check(results, DEFAULT_WEIGHTS)
    lines_d = ["| Day-type weighting | Ranking | " + " | ".join(names[a] for a in order) + " |",
               "| --- | --- | " + " | ".join("---" for _ in order) + " |",
               "| calendar 5 : 2 (baseline) | " + " > ".join(names[a] for a in order) + " | "
               + " | ".join(_fmt(results[a]["business_fit"]) for a in order) + " |"]
    for label, text in (("equal", "weekday = weekend"), ("weekday_only", "weekday only"), ("weekend_only", "weekend only")):
        dd = dcheck[label]
        lines_d.append(f"| {text} | {' > '.join(names[a] for a in dd['rank'])} | "
                       + " | ".join(_fmt(dd["fits"][a]) for a in order) + " |")
    day_check = "\n".join(lines_d)

    vcheck = visitor_conversion_check(results, DEFAULT_WEIGHTS)
    if vcheck is None:
        visitor_check = "No visitor fit on record — check not run."
    else:
        lines_v = [f"| Visitors per pageview | Ranking | " + " | ".join(names[a] for a in order) + " |",
                   "| --- | --- | " + " | ".join("---" for _ in order) + " |",
                   f"| {vcheck['base_k']:.1f} (fit, baseline) | {' > '.join(names[a] for a in order)} | "
                   + " | ".join(_fmt(results[a]['business_fit']) for a in order) + " |"]
        for label in ("low", "high"):
            v = vcheck[label]
            lines_v.append(f"| {v['k']:.1f} ({label}, {'P25' if label == 'low' else 'P75'} of the fit) | "
                           f"{' > '.join(names[a] for a in v['rank'])} | "
                           + " | ".join(_fmt(v['fits'][a]) for a in order) + " |")
        same = all(vcheck[l]["rank"] == order for l in ("low", "high"))
        visitor_check = "\n".join(lines_v) + "\n\n" + (
            "The ranking holds across the interquartile range of the visitor conversion."
            if same else
            "**The ranking changes within the interquartile range of the visitor conversion** — the visitor "
            "estimate is the weakest link in demand; treat the order as provisional until visitor footfall is counted."
        )

    wcheck = worker_onsite_check(results, DEFAULT_WEIGHTS)
    lines_w = ["| Workers counted | Ranking | " + " | ".join(names[a] for a in order) + " |",
               "| --- | --- | " + " | ".join("---" for _ in order) + " |",
               "| verified on-site, nearest station = this node (baseline) | " + " > ".join(names[a] for a in order) + " | "
               + " | ".join(f"{_fmt(_get(results[a]['metrics'], 'worker_headcount_estimate', 'current')['value'], 0)} → "
                            f"{_fmt(results[a]['business_fit'])}" for a in order) + " |"]
    for label, text in (("low", "verified on-site, low"), ("high", "verified on-site, high"),
                        ("in_circle", "verified, whole 400m circle (no station assignment)"),
                        ("as_registered", "as registered (no verification, whole circle)")):
        w = wcheck[label]
        lines_w.append(f"| {text} | {' > '.join(names[a] for a in w['rank'])} | "
                       + " | ".join(f"{_fmt(w['workers'][a], 0)} → {_fmt(w['fits'][a])}" for a in order) + " |")
    same_w = all(wcheck[l]["rank"] == order for l in ("low", "high"))
    worker_check = "\n".join(lines_w) + "\n\n" + (
        "The ranking holds across the verified on-site ranges."
        if same_w else
        "**The ranking changes within the verified on-site ranges** — confirm the large employers' on-site "
        "headcounts before relying on the order."
    )

    scheck = competition_structure_check(results, DEFAULT_WEIGHTS)
    lines_s = ["| Competition measured as | Ranking | " + " | ".join(names[a] for a in order) + " |",
               "| --- | --- | " + " | ".join("---" for _ in order) + " |",
               "| competitors per 10,000 people (baseline) | " + " > ".join(names[a] for a in order) + " | "
               + " | ".join(f"{_fmt(results[a]['pillars']['competition'])} → {_fmt(results[a]['business_fit'])} "
                            f"({_pct(mc['top_share'][a])} #1)" for a in order) + " |",
               "| competitors in the 400m circle (no demand denominator) | "
               + " > ".join(names[a] for a in scheck["rank"]) + " | "
               + " | ".join(f"{_fmt(scheck['pillars'][a]['competition'])} → {_fmt(scheck['fits'][a])} "
                            f"({_pct(scheck['monte_carlo']['top_share'][a])} #1)" for a in order) + " |"]
    abs_anchors = "; ".join(
        f"{c.label.split(' competitors')[0]} {scheck['anchors'][c.key][0]:g} → {scheck['anchors'][c.key][1]:g}"
        for c in BUSINESS_FIT_CRITERIA if c.key in scheck["anchors"])
    same_s = scheck["rank"] == order and all(t == order[0] for t in scheck["scenario_tops"].values())
    structure_check = "\n".join(lines_s) + "\n\n" + (
        f"Cells: competition pillar → business fit (share of {mc['draws']:,} random weightings ranked #1). "
        f"Absolute anchors, P90 → P10 of the same 139 stations' raw counts: {abs_anchors}.\n\n"
    ) + (
        "Same order, and the same #1 in every named scenario: the ranking is not an artifact of dividing "
        "competition by demand."
        if same_s else
        "**The ranking changes when competition is not divided by demand** — part of the result comes from "
        "demand entering the score twice; read the order as conditional on the per-head competition measure."
    )

    flips = [o for o in sens["one_at_a_time"] if o["top_changed"]]
    if sens["top_rank_robust"]:
        sensitivity_reading = "the #1 position holds under every scenario, perturbation, and ≥80% of random weightings."
    else:
        parts = []
        if flips:
            parts.append(
                "#1 flips under "
                + ", ".join(f"{PILLAR_LABELS[o['pillar']].lower()} ×{o['factor']}" for o in flips)
            )
        scen_tops = sorted({names[s["rank"][0]] for s in sens["scenarios"]})
        parts.append(f"named scenarios put {' or '.join(scen_tops)} first")
        parts.append(f"the baseline fit spread across all three nodes is {_fmt(sens['fit_spread'])} points")
        sensitivity_reading = (
            "; ".join(parts)
            + ". The ranking between the contenders turns on how absolute demand is weighed against "
            "competition per unit of that demand. Present both, with the weight that decides between them, "
            "rather than a single #1."
        )

    # §5 differentiation
    diff_parts = []
    for a in order:
        node = next(n for n in CANDIDATE_NODES if n.area_id == a)
        diff_parts.append(f"**{names[a]}**\n\n" + "\n".join(f"- {h}" for h in _differentiation(conn, node, results[a])))
    differentiation = "\n\n".join(diff_parts)

    # §6 evidence gaps
    gap_events = _evidence_gap_events(conn)
    gap_lines = []
    for a in order:
        items = list(gap_events.get(a, []))
        if a in under_mapped:
            items.append(under_mapped[a])
        for c in results[a]["criteria"]:
            if c["raw_value"] is None:
                items.append(f"Scoring input missing: {c['label']} — {c['missing_reason']}")
        unverified = _get(results[a]["metrics"], "worker_large_unverified_count", "current")
        if unverified and not unverified["is_missing"] and unverified["value"] > 0:
            items.append(
                f"{unverified['value']:.0f} IHK registration(s) of 500+ employees in the circle have no verified "
                f"on-site headcount and are counted at their size-class midpoint — likely an overstatement "
                f"(a registered seat can carry a whole company's headcount)."
            )
        anchor_share = _get(results[a]["metrics"], "anchor_plr_catchment_share", "current")
        if anchor_share and not anchor_share["is_missing"]:
            items.append(
                f"Anchor Planungsraum covers only {anchor_share['value']:.0%} of the 400m circle — resident demand "
                f"rests on areal apportionment across neighbouring Planungsräume, not a direct count."
            )
        gap_lines.append(f"**{names[a]}**\n\n" + ("\n".join(f"- {i}" for i in items) if items else "- None beyond the cross-node gaps below."))
    gap_lines.append(
        "**All nodes**\n\n"
        "- 7-Eleven catchment radius: no company-specific figure is public (400m is the industry convention).\n"
        "- Reitan Group's reported evaluation of a German 7-Eleven entry is confirmed only by trade media; no follow-through confirmed.\n"
        "- REWE To Go (the closest domestic benchmark) station-store performance is not public.\n"
        "- Workers come from IHK Gewerbedaten, which covers commercial businesses only. Employers it misses were added by "
        "hand (`db/raw/calibration/worker_onsite_unregistered.csv`): Amazon at Warschauer Straße and, at Brandenburger Tor, "
        "the public sector — Bundestag buildings, embassies and a federal ministry, ~6,600 people (5,100–8,200), mostly from "
        "floor area at the Jakob-Kaiser-Haus density. Warschauer Straße's and Schönhauser Allee's public employers have not "
        "been inventoried the same way."
    )
    evidence_gaps = "\n\n".join(gap_lines)

    # §7 field checklist
    fc = []
    for a in order:
        items = [
            "Count footfall at the main station exits in both commute windows (weekday 7–9 and 16–19) and a Saturday midday baseline.",
            "Walk the 400m radius and reconcile the competitor list against OSM (new openings, closures, unmapped kiosks).",
        ]
        items += [e for e in gap_events.get(a, [])]
        if a in under_mapped:
            items.append("Priority: verify the competitor count on-site — OSM appears under-mapped relative to IHK registrations.")
        items.append("Check available 50–100㎡ storefronts within ~100m of an exit, asking rents, and station-landlord (DB InfraGO / BVG) leasing routes.")
        fc.append(f"**{names[a]}**\n\n" + "\n".join(f"- [ ] {i}" for i in items))
    fc.append(
        "**All nodes**\n\n"
        "- [ ] Confirm Sunday / holiday trading rules for a station-adjacent format under Berlin's Ladenöffnungsgesetz.\n"
        "- [ ] Interview REWE To Go or Reitan contacts on station-store economics and fresh-food supply cadence.\n"
        "- [ ] Validate the visitor estimates (Wikipedia pageviews converted at ~10 visitors per pageview) with an "
        "on-site count at Brandenburger Tor (weekend midday) and Warschauer Straße (Fri/Sat late evening) — "
        "visitors are the largest and least certain demand segment at both."
    )
    field_checklist = "\n\n".join(fc)

    # §9 sources
    snap_ids = sorted({s for c in citations for s in c["snapshot_ids"]})
    src_rows = conn.execute(
        f"SELECT snapshot_id, source_name, data_as_of_date, fetched_at, license, raw_file_ref FROM source_snapshots "
        f"WHERE snapshot_id IN ({','.join('?' * len(snap_ids))}) ORDER BY snapshot_id",
        snap_ids,
    ).fetchall()
    sources_table = "\n".join(
        f"| #{r[0]} | {r[1]} | {r[2]} | {r[3][:10]} | {r[4] or 'not recorded'} | "
        f"{f'`{r[5]}`' if r[5] else 'no raw file on record'} |"
        for r in src_rows
    )
    sources_table += (
        "\n| — | DB InfraGO *Stationspreisliste 2026* (Preisklasse) | 2026-09-08 | manual | — | "
        "[PDF](https://www.dbinfrago.com/resource/blob/13518698/1cd204bc2c7a98b2490822ee6fc200ad/Stationspreisliste-2026-data.pdf) |"
    )

    cit_lines = ["| Node | Criterion | metric_id | Metric | Period | Value | Snapshots |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for c in citations:
        cit_lines.append(
            f"| {names[c['area_id']]} | {c['criterion']} | {c['metric_id']} | `{c['metric_name']}` | {c['period_label']} | "
            f"{_fmt(c['value'], 3)} | {', '.join(f'#{s}' for s in c['snapshot_ids'])} |"
        )

    text = Template(TEMPLATE_PATH.read_text()).substitute(
        run_date=run_at.date().isoformat(),
        run_id=run_id,
        model_version=SCORING_MODEL_VERSION,
        headline=headline,
        summary_table="\n".join(summary_rows),
        fit_spread=_fmt(sens["fit_spread"]),
        top_gap=_fmt(
            results[order[0]]["business_fit"] - results[order[1]]["business_fit"]
            if len(order) > 1 and results[order[1]]["business_fit"] is not None
            else None
        ),
        baseline_weights=_weights_str(DEFAULT_WEIGHTS),
        criteria_table=criteria_table,
        unscored_dimensions="\n".join(f"- {d}" for d in UNSCORED_DIMENSIONS),
        evidence_table=evidence_table,
        scenario_table="\n".join(sc_lines),
        oat_table="\n".join(oat_lines),
        mc_draws=f"{mc['draws']:,}",
        mc_seed=mc["seed"],
        mc_table="\n".join(mc_lines),
        minmax_rank=minmax_rank,
        visitor_check=visitor_check,
        worker_check=worker_check,
        day_check=day_check,
        structure_check=structure_check,
        sensitivity_reading=sensitivity_reading,
        differentiation=differentiation,
        evidence_gaps=evidence_gaps,
        field_checklist=field_checklist,
        sources_table=sources_table,
        citation_check=f"{len(citations)} citations verified",
        citations_table="\n".join(cit_lines),
    )

    # Last guard: nothing un-rendered or non-numeric leaked into the text.
    if re.search(r"\$[a-z_]+|\bNone\b|\bnan\b", text):
        raise AssertionError("report contains an unrendered placeholder, None, or nan")

    payload = {
        "results": {
            a: {k: v for k, v in r.items() if k != "metrics"} for a, r in results.items()
        },
        "sensitivity": sens,
        "citations": citations,
        "snapshot_ids": snap_ids,
    }
    return text, payload


def run(db_path: Path = DB_PATH, run_at: datetime | None = None) -> Path:
    """run_at defaults to now. Freshness is judged against it, so a
    reproducibility re-run must pass the original run's timestamp."""
    run_at = run_at or datetime.now(timezone.utc)
    stamp = run_at.strftime("%Y%m%dT%H%M%SZ")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    log_ref, tool_log = _latest_agent_log()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        run_id = conn.execute(
            """
            INSERT INTO analysis_runs
                (run_at, store_hypothesis_json, area_ids_json, snapshot_ids_json, weights_json,
                 model_version, tool_log_json, report_ref, notes)
            VALUES (?, ?, ?, '[]', ?, ?, ?, NULL, NULL)
            """,
            (
                run_at.isoformat(),
                json.dumps(_store_hypothesis(), ensure_ascii=False),
                json.dumps([n.area_id for n in CANDIDATE_NODES]),
                json.dumps(DEFAULT_WEIGHTS),
                SCORING_MODEL_VERSION,
                json.dumps({"source": log_ref, "entries": tool_log}, ensure_ascii=False),
            ),
        ).lastrowid

        text, payload = render(conn, run_id, run_at)

        report_path = REPORTS_DIR / f"site_report_run{run_id}_{stamp}.md"
        scores_path = REPORTS_DIR / f"site_scores_run{run_id}_{stamp}.json"
        report_path.write_text(text)
        scores_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))

        sens = payload["sensitivity"]
        conn.execute(
            "UPDATE analysis_runs SET snapshot_ids_json = ?, report_ref = ?, notes = ? WHERE run_id = ?",
            (
                json.dumps(payload["snapshot_ids"]),
                str(report_path.relative_to(PROJECT_ROOT)),
                json.dumps(
                    {
                        "scores_ref": str(scores_path.relative_to(PROJECT_ROOT)),
                        "base_rank": sens["base_rank"],
                        "top_rank_robust": sens["top_rank_robust"],
                        "citations_verified": len(payload["citations"]),
                    }
                ),
                run_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    for a in sens["base_rank"]:
        r = payload["results"][a]
        print(
            f"[{a}] fit={_fmt(r['business_fit'])} evidence={_fmt(r['evidence_quality']['score'])} "
            f"({r['evidence_quality']['tier']}) top_share={sens['monte_carlo']['top_share'][a]:.0%}"
        )
    print(f"Top rank robust: {sens['top_rank_robust']}")
    print(f"Report: {report_path.relative_to(PROJECT_ROOT)} (analysis_runs #{run_id})")
    return report_path


if __name__ == "__main__":
    run()
