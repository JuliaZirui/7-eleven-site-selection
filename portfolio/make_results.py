"""Build portfolio/results.html: one A4-landscape page with three charts of the
case-study results. Data: portfolio/results_data.json (extract_results.py).

    python3 portfolio/make_results.py
"""
import json
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
D = json.loads((HERE / "results_data.json").read_text())

NODES = ["brandenburger_tor", "warschauer_strasse", "schoenhauser_allee"]
NAME = {"brandenburger_tor": "Brandenburger Tor", "warschauer_strasse": "Warschauer Straße",
        "schoenhauser_allee": "Schönhauser Allee"}
# Categorical slots 1-3 (validated all-pairs, light surface).
COL = {"brandenburger_tor": "var(--s1)", "warschauer_strasse": "var(--s2)", "schoenhauser_allee": "var(--s3)"}
SEG = [("residents", "Residents", "var(--g1)"), ("workers", "Workers", "var(--g2)"),
       ("visitors", "Visitors", "var(--g3)")]


# Axis text (titles and tick labels). None keeps each chart's own size/weight;
# the portfolio sets both to match its explanation text.
AXIS_SIZE = None
AXIS_WEIGHT = None


def ax(size, weight=400):
    return dict(size=AXIS_SIZE or size, weight=AXIS_WEIGHT or weight)


def fmt(n):
    return f"{n:,.0f}"


def t(x, y, s, size=11, anchor="start", weight=400, fill="var(--ink-2)", extra="", halo=False):
    h = ' stroke="var(--surface)" stroke-width="3.5" paint-order="stroke" stroke-linejoin="round"' if halo else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
            f'font-weight="{weight}" fill="{fill}"{h}{extra}>{escape(s)}</text>')


# ------------------------------------------------------------------ chart 1
def chart_city(day, x_title, H=None, W=530):
    """One panel per day type; both share the x and y scales. A narrow
    panel (W < 450) puts the scale note on its own line under the tag."""
    narrow = W < 450
    H = H or (282 if x_title else 256)
    L, R, T, B = 52, 18, (44 if narrow else 28), (50 if x_title else 24)
    pw, ph = W - L - R, H - T - B
    xmax, ymax = 50000, 120
    X = lambda v: L + v / xmax * pw
    Y = lambda v: T + v / ymax * ph            # 0 competitors at the top: up = less competition
    wk = day == "weekday"
    p10x, p90x = D["anchors"]["people_weekday" if wk else "people_weekend"]
    p90y, p10y = D["anchors"]["per10k_weekday" if wk else "per10k_weekend"]
    o = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
         f'aria-label="{day}: 139 Berlin stations by people per day and competitors per 10,000 people">']
    o.append(f'<rect x="{L}" y="{T}" width="{pw}" height="{Y(p10y) - T:.1f}" fill="var(--band-good)"/>')
    o.append(f'<rect x="{L}" y="{Y(p90y):.1f}" width="{pw}" height="{T + ph - Y(p90y):.1f}" fill="var(--band-bad)"/>')
    for v in range(0, xmax + 1, 10000):
        o.append(f'<line x1="{X(v):.1f}" y1="{T}" x2="{X(v):.1f}" y2="{T + ph}" stroke="var(--grid)"/>')
        o.append(t(X(v), T + ph + 14, f"{v // 1000}k" if v else "0", anchor="middle", fill="var(--ink-3)", **ax(10)))
    for v in range(0, ymax + 1, 40):
        o.append(f'<line x1="{L}" y1="{Y(v):.1f}" x2="{L + pw}" y2="{Y(v):.1f}" stroke="var(--grid)"/>')
        o.append(t(L - 6, Y(v) + 3.5, str(v), anchor="end", fill="var(--ink-3)", **ax(10)))
    o.append(t(L + pw - 6, T + 12, "best 10%" if narrow else "best 10% (thinnest competition)", 9.5, "end", 600,
               "var(--ink-2)", halo=True))
    o.append(t(L + pw - 6, T + ph - 6, "worst 10%" if narrow else "worst 10% (most crowded)", 9.5, "end", 600,
               "var(--ink-2)", halo=True))
    for v, lab in ((p10x, "P10"), (p90x, "P90")):
        o.append(f'<line x1="{X(v):.1f}" y1="{T}" x2="{X(v):.1f}" y2="{T + ph}" stroke="var(--ink-3)" '
                 f'stroke-dasharray="3 3" stroke-width="1"/>')
        o.append(t(X(v) + 3, T + ph - 6, lab, 9.5, fill="var(--ink-3)"))
    # panel tag
    o.append(f'<rect x="{L}" y="3" width="{78 if wk else 156}" height="19" rx="9.5" fill="var(--navy)"/>')
    o.append(t(L + 10, 16.5, "Weekday" if wk else "Weekend / holiday day", 10.5, weight=700, fill="#ffffff"))
    o.append(t(L if narrow else L + (88 if wk else 166), 36 if narrow else 16.5,
               f"scale P10–P90: {p10x:,}–{p90x:,} people; {p10y:g}–{p90y:g} competitors per 10k", 9.5,
               fill="var(--ink-3)"))
    if x_title:
        o.append(t(L + pw / 2, H - 12, "Estimated people per day within 400 m  →  more demand", anchor="middle",
                   fill="var(--ink-2)", **ax(11, 600)))
    o.append(t(13, T + ph / 2, "Competitors per 10k people  ↑ fewer", anchor="middle", fill="var(--ink-2)",
               extra=f' transform="rotate(-90 13 {T + ph / 2})"', **ax(10, 600)))
    cand = set(NAME.values())
    kp, kc = ("people", "per10k") if wk else ("people_we", "per10k_we")
    for r in D["reference"]:
        if r["station"] in cand or kp not in r:
            continue
        o.append(f'<circle cx="{X(min(r[kp], xmax)):.1f}" cy="{Y(min(r[kc], ymax)):.1f}" r="3" '
                 f'fill="var(--ref)" stroke="var(--surface)" stroke-width="1"><title>{escape(r["station"])}: '
                 f'{fmt(r[kp])} people, {r[kc]:.1f} per 10k</title></circle>')
    pos = ({"brandenburger_tor": (-12, -1, "end") if narrow else (-11, -14, "end"), "warschauer_strasse": (12, 16, "start"),
            "schoenhauser_allee": (12, -4, "start")} if wk else
           {"brandenburger_tor": (12, 4, "start"), "warschauer_strasse": (12, -2, "start"),
            "schoenhauser_allee": (12, -10, "start")})
    for a in NODES:
        n = D["nodes"][a]
        px, pc = (n["people_weekday"], n["comp_per10k_weekday"]) if wk else (n["people_weekend"], n["comp_per10k_weekend"])
        cx, cy = X(px), Y(pc)
        o.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="7.5" fill="{COL[a]}" stroke="var(--surface)" stroke-width="2"/>')
        dx, dy, anc = pos[a]
        o.append(t(cx + dx, cy + dy, NAME[a], 11.5, anc, 700, "var(--ink-1)", halo=True))
        o.append(t(cx + dx, cy + dy + 13, f"{fmt(px)} people · {pc:.1f} per 10k", 10, anc, 400, "var(--ink-2)", halo=True))
    o.append(f'<line x1="{L}" y1="{T + ph}" x2="{L + pw}" y2="{T + ph}" stroke="var(--axis)"/>')
    o.append("</svg>")
    return "".join(o)


# ------------------------------------------------------------------ chart 2
SHORT = {"Baseline (weekday 5 : weekend 2)": "Baseline (5 : 2)", "Weekend / holiday only": "Weekend only",
         "Visitors per pageview: low (P25)": "Visitors low (P25)", "Visitors per pageview: high (P75)": "Visitors high (P75)",
         "Workers: low end of verified range": "Workers low", "Workers: high end of verified range": "Workers high",
         "Competitors scaled for under-mapping": "Under-mapping scaled", "Competition as absolute counts": "Absolute counts",
         "Both downsides at once": "Both downsides"}


def chart_stress(rh=22, W=520, narrow=False):
    """narrow: short row labels and no legend (the page carries a shared one)."""
    L, R, T = (112, 22, 14) if narrow else (206, 30, 26)
    rows = [(SHORT.get(l, l), f) for l, f in D["scenarios"]] if narrow else D["scenarios"]
    pw = W - L - R
    H = T + rh * len(rows) + 26
    X = lambda v: L + v / 100 * pw
    o = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
         f'aria-label="Business fit of the three nodes in each stress test">']
    # legend
    lx = L - 60
    for a in ([] if narrow else NODES):
        o.append(f'<circle cx="{lx + 5}" cy="9" r="5" fill="{COL[a]}"/>')
        o.append(t(lx + 14, 13, NAME[a], 10.5, fill="var(--ink-2)"))
        lx += 14 + len(NAME[a]) * 5.6 + 14
    for v in range(0, 101, 25):
        o.append(f'<line x1="{X(v):.1f}" y1="{T - 4}" x2="{X(v):.1f}" y2="{T + rh * len(rows)}" stroke="var(--grid)"/>')
        o.append(t(X(v), T + rh * len(rows) + 13, str(v), anchor="middle", fill="var(--ink-3)", **ax(10)))
    o.append(t(L + pw / 2, H - 1, "Business fit (0–100)", anchor="middle", fill="var(--ink-2)", **ax(10.5, 600)))
    for i, (label, fits) in enumerate(rows):
        y = T + rh * i + rh / 2
        joint = label.startswith("Both")  # full and short label
        if i == 3:   # day-type rows above, input stress tests below
            o.append(f'<line x1="8" y1="{y - rh / 2:.1f}" x2="{W - 8}" y2="{y - rh / 2:.1f}" stroke="var(--rule)" '
                     f'stroke-dasharray="2 3"/>')
        if i == 0 or joint:
            o.append(f'<rect x="4" y="{y - rh / 2 + 1}" width="{W - 8}" height="{rh - 2}" rx="4" fill="var(--row-hl)"/>')
        o.append(t(L - 8, y + 4, label, 9.5 if narrow else 10.5, "end", 700 if (i == 0 or joint) else 400, "var(--ink-1)"))
        vals = [fits[a] for a in NODES]
        o.append(f'<line x1="{X(min(vals)):.1f}" y1="{y}" x2="{X(max(vals)):.1f}" y2="{y}" stroke="var(--rule)" stroke-width="2"/>')
        for a in reversed(NODES):
            o.append(f'<circle cx="{X(fits[a]):.1f}" cy="{y}" r="5.5" fill="{COL[a]}" stroke="var(--surface)" '
                     f'stroke-width="1.5"><title>{NAME[a]}: {fits[a]}</title></circle>')
        if joint and narrow:   # last row: no room above, label to the right of the dots
            o.append(t(X(max(vals)) + 10, y + 3.5,
                       f"{fits[NODES[0]]:.1f} vs. {fits[NODES[1]]:.1f}", 9.5, "start", 700, "var(--ink-1)"))
        elif i == 0 or joint:
            for a in NODES[:2]:
                o.append(t(X(fits[a]), y - 8, f"{fits[a]:.1f}", 9.5, "middle", 700, "var(--ink-1)"))
    o.append("</svg>")
    return "".join(o), H


def gauge(W=520, narrow=False):
    H = 58 if narrow else 46
    L, R = (14, 22) if narrow else (206, 30)
    dy = 12 if narrow else 0
    pw = W - L - R
    lo, hi = 1, 7
    X = lambda v: L + (v - lo) / (hi - lo) * pw
    f = D["mapping"]["factors"]["brandenburger_tor"]
    be = D["mapping"]["break_even"]
    o = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="Under-count break-even">']
    if narrow:
        o.append(t(0, 10, "Competitor under-count at Brandenburger Tor", 9.5, "start", 700, "var(--ink-1)"))
    else:
        o.append(t(L - 10, 22, "Competitor under-count at BT", 10.5, "end", 700, "var(--ink-1)"))
    o.append(f'<rect x="{X(lo):.1f}" y="{15 + dy}" width="{pw}" height="10" rx="5" fill="var(--grid)"/>')
    o.append(f'<rect x="{X(lo):.1f}" y="{15 + dy}" width="{X(f) - X(lo):.1f}" height="10" rx="5" fill="var(--s1)"/>')
    o.append(f'<line x1="{X(be):.1f}" y1="{9 + dy}" x2="{X(be):.1f}" y2="{31 + dy}" stroke="var(--ink-1)" stroke-width="2"/>')
    o.append(t(X(f), 41 + dy, f"measured gap ×{f:.2f}", 9.5 if narrow else 10, "middle", 600, "var(--ink-2)"))
    o.append(t(X(be), 41 + dy, f"loses #1 at ×{be:.1f}", 9.5 if narrow else 10, "middle", 700, "var(--ink-1)"))
    for v in (1, 3, 5, 7):
        o.append(t(X(v), 8 + dy, f"×{v}", 9, "middle", fill="var(--ink-3)"))
    o.append("</svg>")
    return "".join(o)


# ------------------------------------------------------------------ chart 3
def chart_mix(bh=15, gap=4, ggap=11, W=520, narrow=False):
    """narrow: node name only on the weekday bar; 'weekend' on the second."""
    L, R, T = (110, 40, 22) if narrow else (206, 48, 22)
    pw = W - L - R
    xmax = 25000
    X = lambda v: L + v / xmax * pw
    o, y = [], T
    rows = []
    for a in NODES:
        for dt in ("weekday", "weekend"):
            rows.append((a, dt, y))
            y += bh + gap
        y += ggap - gap
    H = y + 18
    o.append(f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
             f'aria-label="People per day by segment, weekday and weekend">')
    lx = 0 if narrow else L
    for key, lab, c in SEG:
        o.append(f'<rect x="{lx}" y="3" width="11" height="11" rx="2" fill="{c}"/>')
        o.append(t(lx + 16, 13, lab, 10.5, fill="var(--ink-2)"))
        lx += 16 + len(lab) * 6 + 18
    for v in range(0, xmax + 1, 5000):
        o.append(f'<line x1="{X(v):.1f}" y1="{T - 3}" x2="{X(v):.1f}" y2="{y - ggap + gap}" stroke="var(--grid)"/>')
        o.append(t(X(v), y - ggap + gap + 13, f"{v // 1000}k" if v else "0", anchor="middle", fill="var(--ink-3)",
                   **ax(10)))
    for a, dt, yy in rows:
        seg = D["nodes"][a][dt]
        total = sum(seg.values())
        if dt == "weekday":
            o.append(t(L - 8, yy + bh - 3, NAME[a] if narrow else f"{NAME[a]} · weekday",
                       9.5 if narrow else 10.5, "end", 700, "var(--ink-1)"))
        x0 = L
        for key, lab, c in SEG:
            v = seg[key]
            w = X(v) - L
            if w <= 0:
                continue
            o.append(f'<rect x="{x0:.1f}" y="{yy}" width="{max(w - 2, 0.8):.1f}" height="{bh}" fill="{c}">'
                     f'<title>{NAME[a]} {dt}: {lab.lower()} {fmt(v)}</title></rect>')
            x0 += w
        o.append(t(x0 + 4, yy + bh - 3, f"{total / 1000:.1f}k", 10, fill="var(--ink-2)"))
    # day labels
    for a, dt, yy in rows:
        if dt == "weekend":
            o.append(t(L - 8, yy + bh - 3, "weekend" if narrow else "weekend day", 9 if narrow else 9.5, "end", fill="var(--ink-3)"))
    o.append("</svg>")
    return "".join(o)


def main():
    n = D["nodes"]
    bt, ws, sa = (n[a] for a in NODES)
    share = lambda node, k: node["weekday"][k] / sum(node["weekday"].values())
    joint = dict(D["scenarios"])["Both downsides at once"]

    stress_svg, _ = chart_stress(rh=41)
    html = f"""<!doctype html>
    <html lang="en"><head><meta charset="utf-8">
    <title>Case Study Results</title>
    <style>
      @page {{ size: A4 landscape; margin: 0; }}
      :root {{
        --surface:#ffffff; --ink-1:#0b0b0b; --ink-2:#3d3c39; --ink-3:#7a7974;
        --grid:#ebeae6; --axis:#b9b8b2; --rule:#cfcdc6; --ref:#c9c8c2; --row-hl:#f3f2ee;
        --band-good:#eef4fc; --band-bad:#f5f4f1;
        --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a;
        --g1:#4a3aa7; --g2:#e87ba4; --g3:#eda100;
        --navy:#0b2545;
      }}
      * {{ box-sizing:border-box; }}
      html,body {{ margin:0; background:var(--surface); }}
      body {{ font-family:"Helvetica Neue",Helvetica,Arial,sans-serif; color:var(--ink-1);
             -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
      .page {{ width:297mm; height:210mm; padding:9mm 11mm 7mm; position:relative; overflow:hidden; page-break-after:always; }}
      .page:last-child {{ page-break-after:auto; }}
      .pair {{ display:grid; grid-template-columns:1fr 1fr; gap:8mm; }}
      .kicker {{ font-size:8pt; letter-spacing:.12em; text-transform:uppercase; color:#13807a; font-weight:700; }}
      h1 {{ font-size:16pt; line-height:1.2; margin:1mm 0 4mm; color:var(--navy); }}
      .panel {{ border-top:2pt solid var(--navy); padding-top:2mm; }}
      .num {{ display:inline-block; background:var(--navy); color:#fff; font-size:8pt; font-weight:700;
              padding:.2mm 1.6mm; border-radius:1mm; margin-right:1.6mm; }}
      h2 {{ font-size:11.5pt; line-height:1.25; margin:0 0 .6mm; color:var(--navy); }}
      .sub {{ font-size:8.6pt; color:var(--ink-2); margin:0 0 3mm; line-height:1.35; }}
      svg {{ display:block; font-family:inherit; max-width:100%; height:auto; }}
      .foot {{ position:absolute; left:11mm; right:11mm; bottom:5mm; font-size:6.8pt; color:var(--ink-3);
               border-top:.5pt solid var(--grid); padding-top:1mm; display:flex; gap:8mm; justify-content:space-between; }}
      .pg {{ white-space:nowrap; }}
    </style></head>
    <body>
    <div class="page">
      <div class="kicker">Case study results (1 / 2) · three Berlin transit nodes · analysis run #26</div>
      <h1>Against 139 Berlin stations, Brandenburger Tor pairs high demand with some of the city's thinnest competition,
          on weekdays and at weekends</h1>
      <div class="panel">
        <h2><span class="num">1</span>Measured against 139 Berlin stations, not just against each other</h2>
        <p class="sub">Grey dots: inner-Berlin S/U-Bahn stations; bands and dashed lines: each day type's P10 / P90, the ends of
          the scoring scale. On both day types Brandenburger Tor is in the city's least contested tenth
          ({bt['comp_per10k_weekday']:.1f} / {bt['comp_per10k_weekend']:.1f} competitors per 10,000 people) and Schönhauser Allee in
          the most crowded tenth ({sa['comp_per10k_weekday']:.1f} / {sa['comp_per10k_weekend']:.1f}). At weekends Brandenburger Tor's
          {fmt(bt['people_weekend'])} people per day exceed the city's P90, because its visitors do not fall away.</p>
        <div class="pair">
          {chart_city('weekday', True, H=540)}
          {chart_city('weekend', True, H=540)}
        </div>
      </div>
      <div class="foot"><span>Business fit (0–100): demand and competition, each scaled to the P10 / P90 of 139 inner-Berlin S/U stations,
        weighted 50 / 50; weekday (Wednesday) and weekend / holiday day (Saturday–Sunday average) scored separately and combined 5 : 2.
        Demand = residents (MSS 2025) + workers (IHK, verified on-site headcounts) + visitors (Wikipedia pageviews, ≈10 visitors per
        pageview). Competitors = bakeries, cafés, supermarkets, spätis, fast food and fuel shops open that day (OSM + Overture).
        Hypothetical case; ranks sites for diligence priority, does not forecast revenue.</span><span class="pg">1 / 2</span></div>
    </div>
    <div class="page">
      <div class="kicker">Case study results (2 / 2) · three Berlin transit nodes · analysis run #26</div>
      <h1>Brandenburger Tor ranks first in every stress test, and each node draws a different crowd</h1>
      <div class="pair">
        <div class="panel">
          <h2><span class="num">2</span>The ranking survives its least certain inputs</h2>
          <p class="sub">Business fit when each uncertain input is pushed to its plausible limit. Brandenburger Tor stays first
            in all {len(D['scenarios'])} rows, on either day type alone and with both downsides at once
            ({joint['brandenburger_tor']:.1f} vs. {joint['warschauer_strasse']:.1f}).</p>
          {stress_svg}
          {gauge()}
        </div>
        <div class="panel">
          <h2><span class="num">3</span>Each node draws a different crowd</h2>
          <p class="sub">Estimated people per day within 400 m. Visitors are {share(bt, 'visitors'):.0%} of Brandenburger Tor's
            weekday demand, the figure to verify first; workers are {share(ws, 'workers'):.0%} at Warschauer Straße, residents
            {share(sa, 'residents'):.0%} at Schönhauser Allee.</p>
          {chart_mix(bh=40, gap=8, ggap=34)}
        </div>
      </div>
      <div class="foot"><span>Business fit (0–100): demand and competition, each scaled to the P10 / P90 of 139 inner-Berlin S/U stations,
        weighted 50 / 50; weekday (Wednesday) and weekend / holiday day (Saturday–Sunday average) scored separately and combined 5 : 2.
        Demand = residents (MSS 2025) + workers (IHK, verified on-site headcounts) + visitors (Wikipedia pageviews, ≈10 visitors per
        pageview). Competitors = bakeries, cafés, supermarkets, spätis, fast food and fuel shops open that day (OSM + Overture).
        Hypothetical case; ranks sites for diligence priority, does not forecast revenue.</span><span class="pg">2 / 2</span></div>
    </div>
    </body></html>
    """
    (HERE / "results.html").write_text(html, encoding="utf-8")
    print("written")


if __name__ == "__main__":
    main()

