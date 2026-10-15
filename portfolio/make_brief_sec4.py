"""Replace section 4 (manual vs. tool) of portfolio/project_brief.html with
KPI tiles and two small charts.

Sources for every figure:
  comparison/manual_vs_tool.md      32 min inventory, ~5 min fetch, 16 vs 31 found, hours 94% vs 31%
  comparison/manual_protocol.md     full desk pass expected 60-120 min per node (9 steps)
  reports/day6_test_log_*.md        metrics + report ~2 s for three nodes
  calibrate_anchors (cached run)    139-station competitor counts in 1.5 s
  comparison/audit_findings.md      16-20 min scan per station; 46 issues; 57/58 flags, 38 missed by scan;
                                    $0.25-1.25 per node
    python3 portfolio/make_brief_sec4.py
"""
import math
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL, MAN = "#4a3aa7", "#eda100"
INK1, INK2, INK3, GRID = "#0b0b0b", "#3d3c39", "#7a7974", "#ebeae6"


def t(x, y, s, size=9, anchor="start", weight=400, fill=INK2):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
            f'font-weight="{weight}" fill="{fill}">{escape(s)}</text>')


def human(sec):
    if sec < 60:
        return f"{sec:g} s"
    if sec < 3600:
        return f"{sec / 60:.0f} min"
    return f"{sec / 3600:.0f} h"


# ---------------------------------------------------------------- time chart (log scale)
def time_chart():
    W, L, R, T = 350, 116, 40, 12
    rows = [
        ("Competitor inventory,", "one node", 1920, None, 1, "measured"),
        ("Desk pass, three nodes", "(9 steps each)", 3 * 3600, 6 * 3600, 302, "manual: protocol estimate"),
        ("Competitor inventory,", "139 stations", 139 * 1920, None, 1.5, "manual: 32 min × 139"),
    ]
    gh, bh = 34, 11
    H = T + gh * len(rows) + 22
    lo, hi = 0, math.log10(400000)
    X = lambda s: L + (math.log10(s) - lo) / (hi - lo) * (W - L - R)
    o = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Time for the same task, log scale">']
    for s, lab in ((1, "1 s"), (60, "1 min"), (3600, "1 h"), (86400, "1 day")):
        o.append(f'<line x1="{X(s):.1f}" y1="{T - 4}" x2="{X(s):.1f}" y2="{T + gh * len(rows) - 6}" stroke="{GRID}"/>')
        o.append(t(X(s), T + gh * len(rows) + 5, lab, 8, "middle", fill=INK3))
    o.append(t(L + (W - L - R) / 2, H - 2, "Time for the same task (log scale)", 8.5, "middle", 600, INK2))
    for i, (l1, l2, man, man_hi, tool, note) in enumerate(rows):
        y = T + gh * i
        o.append(t(L - 8, y + 9, l1, 8.6, "end", 700, INK1))
        o.append(t(L - 8, y + 19, l2, 8.6, "end", 700, INK1))
        o.append(t(L - 8, y + 28, note, 7.2, "end", fill=INK3))
        # manual bar (+ range whisker)
        o.append(f'<rect x="{L}" y="{y}" width="{X(man) - L:.1f}" height="{bh}" rx="2" fill="{MAN}"/>')
        if man_hi:
            o.append(f'<line x1="{X(man):.1f}" y1="{y + bh / 2}" x2="{X(man_hi):.1f}" y2="{y + bh / 2}" stroke="{MAN}" stroke-width="2"/>')
            o.append(f'<line x1="{X(man_hi):.1f}" y1="{y + 2}" x2="{X(man_hi):.1f}" y2="{y + bh - 2}" stroke="{MAN}" stroke-width="2"/>')
        mlab = f"{human(man)}–{human(man_hi)}" if man_hi else ("≈74 h" if man > 100000 else human(man))
        o.append(t(X(man_hi or man) + 4, y + 10, mlab, 8.5, weight=700, fill=INK1))
        # tool bar: at least 2 px so 1 s stays visible
        tw = max(X(tool) - L, 2.5)
        o.append(f'<rect x="{L}" y="{y + bh + 3}" width="{tw:.1f}" height="{bh}" rx="2" fill="{TOOL}"/>')
        tlab = "5 min fetch + 2 s" if tool == 302 else f"{tool:g} s"
        ratio = (man / tool)
        rnd = 10 ** max(int(math.log10(ratio)) - 1, 0)
        rlab = f"×{round(ratio / rnd) * rnd:,.0f} faster"
        o.append(t(L + tw + 4, y + bh + 13, f"{tlab}  ·  {rlab}", 8.5, weight=700, fill=TOOL))
    o.append("</svg>")
    return "".join(o)


# ---------------------------------------------------------------- touchpoints + quality
def right_chart():
    W = 330
    o = [f'<svg viewBox="0 0 {W} 128" width="100%" role="img" aria-label="What each method finds">']
    o.append(t(0, 10, "What each finds (Brandenburger Tor; audit at 3 unseen stations)", 8.8, weight=700, fill=INK1))
    rows = [("Competitors found", 16, 31, 35, "{:g}"), ("Opening hours known", 94, 31, 100, "{:g}%"),
            ("Data issues found", 46, 57, 60, "{:g}")]
    L, bw = 104, W - 104 - 40
    for i, (lab, m, tl, mx, f) in enumerate(rows):
        y = 20 + i * 31
        o.append(t(L - 6, y + 13, lab, 8.5, "end", 600, INK2))
        o.append(f'<rect x="{L}" y="{y}" width="{bw * m / mx:.1f}" height="10" rx="2" fill="{MAN}"/>')
        o.append(t(L + bw * m / mx + 4, y + 8.5, f.format(m), 8.5, weight=700, fill=INK1))
        o.append(f'<rect x="{L}" y="{y + 12}" width="{bw * tl / mx:.1f}" height="10" rx="2" fill="{TOOL}"/>')
        o.append(t(L + bw * tl / mx + 4, y + 20.5, f.format(tl), 8.5, weight=700, fill=TOOL))
    o.append(t(0, 124, "Issues: manual scan vs. AI flags the analyst confirmed (38 missed by the scan).", 7.6, fill=INK3))
    o.append("</svg>")
    return "".join(o)


tiles = [
    ("32 min → 1 s", "competitor inventory for one node", "×1,900"),
    ("74 h → 1.5 s", "competitor inventories for all 139 reference stations", "city scale"),
    ("9 → 2", "analyst touchpoints per node: 9 manual steps become 2 reviews (AI flags, field visits)", "labour"),
    ("€0", "marginal cost of a rerun; optional AI audit $0.25–1.25 per site", "cost"),
]
tile_html = "".join(
    f'<div class="kpi"><div class="kpi-tag">{escape(tag)}</div><div class="kpi-num">{escape(num)}</div>'
    f'<div class="kpi-txt">{escape(txt)}</div></div>' for num, txt, tag in tiles)

section = f'''<!-- 4 -->
  <div class="sec">
    <div class="sec-head"><span class="sec-num">4</span><span class="sec-title">Manual work vs. the tool</span></div>
    <p class="action">Seconds instead of hours per site, near-zero cost per rerun and city-wide reach: the analyst moves from collecting data to judging it.</p>
    <div class="kpis">{tile_html}</div>
    <div class="cmp">
      <div>{time_chart()}</div>
      <div>{right_chart()}</div>
    </div>
    <div class="legend-row"><span class="sw" style="background:{MAN}"></span>Manual desk research
      <span class="sw" style="background:{TOOL}"></span>Tool</div>
    <p class="small" style="margin-top:.4mm">One node, one analyst (the tool's author), one non-blind pass; multi-node manual times extrapolated,
      tool times measured (139 stations from cached data). Manual still wins on opening hours; disagreements became the field checklist.</p>
  </div>

  '''

css = '''
  .kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:2.5mm; margin:1mm 0 2mm; }
  .kpi { background:var(--tint); border-top:2pt solid #4a3aa7; padding:1.4mm 2mm 1.6mm; }
  .kpi-tag { font-size:6.4pt; letter-spacing:.08em; text-transform:uppercase; color:#4a3aa7; font-weight:700; }
  .kpi-num { font-size:14pt; font-weight:700; color:var(--navy); line-height:1.15; margin:.3mm 0 .4mm; white-space:nowrap; }
  .kpi-txt { font-size:7pt; color:var(--muted); line-height:1.25; }
  .cmp { display:grid; grid-template-columns:1.06fr 1fr; gap:5mm; align-items:start; }
  .cmp svg { display:block; font-family:inherit; }
  .legend-row { font-size:7.2pt; color:var(--muted); margin-top:.6mm; display:flex; align-items:center; gap:1.4mm; }
  .legend-row .sw { display:inline-block; width:3mm; height:2.2mm; border-radius:.5mm; margin-left:2mm; }
'''

p = HERE / "project_brief.html"
s = p.read_text(encoding="utf-8")
a, b = s.index("<!-- 4 -->"), s.index("<!-- 5 -->")
s = s[:a] + section + s[b:]
if ".kpis {" not in s:
    s = s.replace("</style>", css + "</style>", 1)
p.write_text(s, encoding="utf-8")
print("section 4 replaced")
