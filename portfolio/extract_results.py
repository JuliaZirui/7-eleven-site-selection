"""Collect the figures for portfolio/results.html into portfolio/results_data.json.

Reads the database (opened read-only) with the project's own scoring
code, so every number matches the published report.
    python3 portfolio/extract_results.py <path/to/site_selection.db>
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import scoring as s  # noqa: E402
from models import CANDIDATE_NODES  # noqa: E402

conn = sqlite3.connect(f"file:{Path(sys.argv[1]).resolve()}?mode=ro", uri=True)  # never writes
ids = [n.area_id for n in CANDIDATE_NODES]
run_at = conn.execute("SELECT run_at FROM analysis_runs ORDER BY run_id DESC LIMIT 1").fetchone()[0]
res = s.score_all(conn, ids, s.DEFAULT_WEIGHTS, now=datetime.fromisoformat(run_at))
W = s.DEFAULT_WEIGHTS

def fits_of(d):
    return {a: round(v, 1) for a, v in d.items()}

scen = [("Baseline (weekday 5 : weekend 2)", fits_of({a: r["business_fit"] for a, r in res.items()}))]
dt = s.day_type_check(res, W)
scen.append(("Weekday only", fits_of(dt["weekday_only"]["fits"])))
scen.append(("Weekend / holiday only", fits_of(dt["weekend_only"]["fits"])))
v = s.visitor_conversion_check(res, W)
scen.append(("Visitors per pageview: low (P25)", fits_of(v["low"]["fits"])))
scen.append(("Visitors per pageview: high (P75)", fits_of(v["high"]["fits"])))
w = s.worker_onsite_check(res, W)
scen.append(("Workers: low end of verified range", fits_of(w["low"]["fits"])))
scen.append(("Workers: high end of verified range", fits_of(w["high"]["fits"])))
m = s.competitor_mapping_stress(res, W)
scen.append(("Competitors scaled for under-mapping", fits_of(m["fits"])))
c = s.competition_structure_check(res, W)
scen.append(("Competition as absolute counts", fits_of(c["fits"])))
j = s.joint_downside_check(res, W)
scen.append(("Both downsides at once", fits_of(j["fits"])))

nodes = {}
for a, r in res.items():
    crit = {x["key"]: x for x in r["criteria"]}
    nodes[a] = {
        "fit": round(r["business_fit"], 1),
        "evidence": round(r["evidence_quality"]["score"], 1),
        "weekday": crit["daily_people"]["breakdown"],
        "weekend": crit["daily_people_weekend"]["breakdown"],
        "people_weekday": crit["daily_people"]["raw_value"],
        "comp_per10k_weekday": crit["weekday_competition"]["raw_value"],
        "people_weekend": crit["daily_people_weekend"]["raw_value"],
        "comp_per10k_weekend": crit["weekend_competition"]["raw_value"],
    }

# 139 reference stations (calibration file used for the anchors)
cal = sorted((ROOT / "db/raw/calibration").glob("demand_anchors_*.json"))[-1]
ref = []
for st in json.load(open(cal))["stations"]:
    if st.get("residents") is None or st.get("workers") is None or st.get("visitors_weekday") is None:
        continue
    people = st["residents"] + st["workers"] + st["visitors_weekday"]
    comp = st.get("weekday_all_day_competitor_count")
    if not people or comp is None:
        continue
    row = {"station": st["station"], "people": round(people), "per10k": round(comp / people * 1e4, 2)}
    pw = st["residents"] + (st.get("workers_weekend") or 0) + (st.get("visitors_weekend") or 0)
    cw = st.get("weekend_all_day_competitor_count")
    if pw and cw is not None and st.get("workers_weekend") is not None and st.get("visitors_weekend") is not None:
        row.update(people_we=round(pw), per10k_we=round(cw / pw * 1e4, 2))
    ref.append(row)

anchors = {c.key: (c.worst, c.best) for c in s.BUSINESS_FIT_CRITERIA}
out = {
    "run_at": run_at,
    "nodes": nodes,
    "scenarios": scen,
    "mapping": {"factors": {a: round(f, 2) for a, f in m["factors"].items()},
                "break_even": round(m["leader_break_even_factor"], 1)},
    "joint_crossover": j["crossover_demand_weight"],
    "anchors": {"people_weekday": anchors["daily_people"], "per10k_weekday": anchors["weekday_competition"],
                "people_weekend": anchors["daily_people_weekend"], "per10k_weekend": anchors["weekend_competition"]},
    "reference": ref,
    "reference_file": cal.name,
}
(Path(__file__).resolve().parent / "results_data.json").write_text(json.dumps(out, indent=1, default=float))
print(len(ref), "reference stations;", scen)
print(out["anchors"], out["mapping"])
