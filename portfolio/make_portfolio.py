"""Build portfolio/portfolio.html: the two-page A4 project portfolio.

Embeds the simplified architecture diagram (architecture_portfolio.svg, from
make_architecture_simple.py --scale 1.25 --out architecture_portfolio) and the
case-study charts from make_results.py, unchanged in design.

    python3 portfolio/make_portfolio.py
"""
import re
from html import escape
from pathlib import Path

import make_results as R

R.AXIS_SIZE, R.AXIS_WEIGHT = 9.3, 400   # = .note text (7 pt, regular)

HERE = Path(__file__).resolve().parent
D = R.D
N = D["nodes"]
bt, ws, sa = (N[a] for a in R.NODES)
share = lambda node, k: node["weekday"][k] / sum(node["weekday"].values())
joint = dict(D["scenarios"])["Both downsides at once"]

# ---------------------------------------------------------------- architecture
arch = (HERE / "architecture_v2.svg").read_text(encoding="utf-8")
# crop the drawing's own outer margin so its edges line up with the page grid
arch = arch.replace('viewBox="0 0 1100 790" width="1100" height="790"', 'viewBox="40 20 1026 742" width="100%"')
assert 'width="100%"' in arch

# ---------------------------------------------------------------- charts
city_wd = R.chart_city("weekday", True, H=336, W=350)
city_we = R.chart_city("weekend", True, H=336, W=350)
stress, _ = R.chart_stress(rh=16, W=340, narrow=True)
gauge = R.gauge(W=340, narrow=True)
mix = R.chart_mix(bh=16, gap=4, ggap=15, W=340, narrow=True)


def fluid(svg):
    """Let an inline chart scale to its card width (keeps its aspect ratio)."""
    return re.sub(r'(<svg[^>]*?) width="\d+(\.\d+)?" height="\d+(\.\d+)?"', r'\1 width="100%"', svg, count=1)


# ---------------------------------------------------------------- content
who = [("Retail expansion teams", "shortlisting sites in a new city"),
       ("Strategy consultants", "desk phase of market-entry work"),
       ("Location analysts", "commercial and public-sector site studies")]
why = [("Labour-intensive collection", "fragmented sources gathered and cross-checked by hand, site by site."),
       ("Misdirected follow-up", "verification effort goes to gaps that would not change the decision."),
       ("Inconsistent assessments", "different sources and criteria; results hard to compare or reproduce."),
       ("False precision", "a single ranking hides data gaps and untested assumptions."),
       ("Missing data read as zero", "no data for a site makes it look free of competition.")]
highlights = [
    ("Deterministic scoring, bounded AI",
     "Every score is computed deterministically. AI acts only within code-enforced budgets and guardrails; "
     "only reviewer-approved corrections reach the data."),
    ("City-wide benchmarking",
     "Scales (P10 / P90) are calibrated on 139 Berlin S/U-Bahn stations, so each site is measured against "
     "the whole city, not just the other two."),
    ("Evaluation-led AI adoption",
     "Both AI components passed pre-registered evaluations first. Rules matched the LLM on all 14 ordering "
     "decisions, so rules decide follow-ups and the LLM drafts field questions; the data audit met thresholds "
     "fixed in advance."),
    ("Production-grade engineering",
     "241 automated tests, fully reproducible offline runs, and failure handling with rule fallback."),
]
sites = [("brandenburger_tor", "Visitor-led city centre", "Best-known landmark station; government district"),
         ("warschauer_strasse", "Office + nightlife mix", "Mediaspree offices next to the RAW nightlife cluster"),
         ("schoenhauser_allee", "Residential daily shopping", "Prenzlauer Berg shopping street, dense residents")]
stack = ["Python", "SQL / SQLite", "API integration", "Anthropic Python SDK"]
eng = ["AI-assisted development with Claude Code", "LLM tool calling", "LLM evaluation", "Automated testing",
       "GitHub Actions CI"]

who_html = "".join(f'<div class="who"><div class="who-t">{escape(a)}</div><div class="who-s">{escape(b)}</div></div>'
                   for a, b in who)
why_html = "".join(f'<li><b>{escape(a)}:</b> {escape(b)}</li>' for a, b in why)
hl_html = "".join(f'<div class="card hl"><div class="hl-n">0{i}</div><div class="hl-t">{escape(t)}</div>'
                  f'<div class="hl-x">{escape(x)}</div></div>' for i, (t, x) in enumerate(highlights, 1))
site_rows = "".join(
    f'<tr><td><span class="dot" style="background:{R.COL[a]}"></span><b>{escape(R.NAME[a])}</b></td>'
    f'<td>{escape(p)}</td><td>{escape(r)}</td></tr>' for a, p, r in sites)
band_sites = "".join(
    f'<div><span class="dot" style="background:{R.COL[a]}"></span><b>{escape(R.NAME[a])}</b> · {escape(p)}</div>'
    for a, p in (("brandenburger_tor", "visitor-led centre"), ("warschauer_strasse", "office + nightlife"),
                 ("schoenhauser_allee", "residential")))
chips = lambda xs, cls: "".join(f'<span class="chip {cls}">{escape(x)}</span>' for x in xs)

FOOT = '<div class="foot"><span>github.com/JuliaZirui/7-eleven-site-selection</span><span>PAGE / 2</span></div>'

CSS = """
@page { size: A4; margin: 0; }
:root {
  --bg:#e7edf5; --card:#ffffff; --blue:#1565c0; --blue-2:#1a73e8; --blue-3:#5e9ee8; --navy:#0b2545;
  --teal:#13807a; --muted:#5b6470;
  /* chart tokens (make_results.py) */
  --surface:#ffffff; --ink-1:#0b0b0b; --ink-2:#3d3c39; --ink-3:#7a7974; --grid:#ebeae6; --axis:#b9b8b2;
  --rule:#cfcdc6; --ref:#c9c8c2; --row-hl:#f3f2ee; --band-good:#eef4fc; --band-bad:#f5f4f1;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --g1:#4a3aa7; --g2:#e87ba4; --g3:#eda100;
}
* { box-sizing:border-box; }
html, body { margin:0; background:var(--bg); }
body { font-family:"Avenir Next","Helvetica Neue",Helvetica,Arial,sans-serif; color:var(--navy); font-size:7.6pt;
       line-height:1.32; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
.page { width:210mm; height:297mm; padding:0 7mm 7mm; position:relative; overflow:hidden; display:flex; flex-direction:column; background:var(--bg);
        page-break-after:always; }
.page:last-child { page-break-after:auto; }
svg { display:block; font-family:"Helvetica Neue",Helvetica,Arial,sans-serif; }

/* header band */
.band { margin:0 -7mm; padding:0 7mm; height:40mm; display:flex; flex-direction:column; justify-content:center; color:#fff; position:relative; overflow:hidden;
        background:linear-gradient(115deg,#0a2a63 0%,#0f4a9e 55%,#1d74d6 100%); }
.band .shape { position:absolute; inset:0; }
.band-in { position:relative; display:flex; justify-content:space-between; align-items:center; gap:8mm; }
.band .sub2 { font-size:12pt; font-weight:500; letter-spacing:.02em; margin-top:.4mm; }
.band .side { font-size:8pt; line-height:1.4; letter-spacing:.02em; max-width:80mm; opacity:.95; }
.band h1 { font-weight:500; font-size:19pt; line-height:1.15; letter-spacing:.02em; margin:0 0 1.6mm; max-width:120mm; }
.band .sub { font-size:9pt; opacity:.92; letter-spacing:.02em; max-width:118mm; }
.pill-wrap { text-align:center; min-width:58mm; }
.pill { display:inline-block; background:#fff; color:var(--blue); border-radius:5mm; padding:1.6mm 4.5mm;
        font-size:8.5pt; letter-spacing:.06em; font-weight:500; }
.pill-note { font-size:7.5pt; margin-top:1.6mm; letter-spacing:.05em; opacity:.92; }
.band.slim { padding:3.4mm 9mm 3mm; }
.sites-band { text-align:left; font-size:7.8pt; line-height:1.55; margin-top:1.6mm; display:inline-block; letter-spacing:.02em; }
.sites-band .dot { box-shadow:0 0 0 1.2pt rgba(255,255,255,.85); }
/* crowd chart in greys (segments told apart by shade and the 2 px gaps) */
.duo { --g1:#3f444b; --g2:#8b929b; --g3:#c9ced5; }
.band.slim h1 { font-size:13pt; margin:0; max-width:none; }

/* section labels + cards */
.label { color:var(--blue); font-weight:700; font-size:10pt; letter-spacing:.04em; margin:2.6mm 0 1.4mm;
         display:flex; justify-content:space-between; align-items:baseline; }
.label small { font-weight:500; font-size:7.4pt; color:var(--muted); letter-spacing:.02em; }
.card { background:var(--card); padding:3mm 3.4mm; }
.cap { color:var(--blue); font-weight:700; font-size:7.2pt; letter-spacing:.09em; text-transform:uppercase; margin-bottom:1.2mm; }
.h2 { font-size:9.2pt; font-weight:700; color:var(--navy); margin:0 0 .6mm; }
.note { color:var(--muted); font-size:7pt; line-height:1.3; margin:0 0 1.6mm; }

/* situation */
.sit { display:grid; grid-template-columns:62mm 1fr; gap:2.5mm; }
.who-col { display:grid; gap:1.6mm; }
.who { background:var(--blue-2); color:#fff; padding:1.6mm 3.2mm; text-align:right; }
.who:nth-child(2) { background:var(--blue-3); }
.who-t { font-size:10pt; font-weight:700; letter-spacing:.02em; }
.who-s { font-size:7.4pt; opacity:.95; letter-spacing:.03em; }
.why ul { margin:0; padding-left:3.6mm; }
.why li { margin:.5mm 0 1mm; }

/* highlights */
.hls { display:grid; grid-template-columns:repeat(4,1fr); gap:2.5mm; }
.hl { padding:2.6mm 3mm 3mm; border-top:2.2pt solid var(--blue-2); }
.hl-n { font-size:15pt; font-weight:700; color:var(--blue-2); line-height:1; }
.hl-t { font-weight:700; font-size:8.1pt; margin:1mm 0 .8mm; color:var(--navy); line-height:1.2; }
.hl-x { font-size:7pt; color:#34404f; line-height:1.3; }

/* case study */
.setup { display:grid; grid-template-columns:66mm 1fr; gap:2.5mm; }
table.sites { width:100%; border-collapse:collapse; font-size:7pt; }
table.sites td { padding:.7mm 1.2mm; border-bottom:.5pt solid #e3e8ef; vertical-align:top; }
.dot { display:inline-block; width:2.4mm; height:2.4mm; border-radius:50%; margin-right:1.3mm; vertical-align:-.2mm; }
.pair { display:grid; grid-template-columns:1fr 1fr; gap:3mm; }
.duo { display:grid; grid-template-columns:1fr 1fr; gap:2.5mm; align-items:stretch; }
.callout { background:var(--blue-2); color:#fff; padding:2.4mm 3.4mm; font-size:8pt; line-height:1.4; letter-spacing:.02em;
           margin-top:2.2mm; text-align:center; }

/* manual vs tool */
.mvt { display:grid; grid-template-columns:repeat(4,1fr); gap:2.5mm; }
.mvt .card { padding:2mm 2.8mm 2.2mm; }
.big { display:flex; align-items:baseline; gap:1.6mm; flex-wrap:wrap; }
.big .m { font-size:15pt; font-weight:700; color:var(--navy); }
.big .ar { color:var(--muted); font-size:9pt; }
.big .t { font-size:15pt; font-weight:700; color:var(--teal); }
.sm { font-size:6.8pt; color:var(--muted); line-height:1.3; margin-top:.8mm; }
.bar { height:1.6mm; margin-top:1.2mm; background:#eef2f7; position:relative; }
.bar i { position:absolute; left:0; top:0; bottom:0; }
.flow { display:flex; gap:.8mm; align-items:center; margin:.6mm 0; font-size:6.5pt; }
.flow span { padding:.6mm 1.4mm; color:#fff; border-radius:.6mm; }
.flow em { font-style:normal; color:var(--muted); }
.n { background:var(--navy); } .tl { background:var(--teal); }
.lg { display:inline-block; width:2.2mm; height:2.2mm; border-radius:50%; margin:0 1mm 0 3mm; vertical-align:-.2mm; }
.techrow { display:flex; flex-wrap:wrap; gap:1mm 1.1mm; align-items:center; }
.techrow .k { font-weight:700; color:var(--blue); font-size:7.4pt; margin-right:1mm; letter-spacing:.03em; }
.chip { border-radius:3mm; padding:.6mm 1.9mm; font-size:6.6pt; font-weight:500; }
.chip.a { background:var(--blue-2); color:#fff; } .chip.b { background:#dfe9f7; color:var(--navy); }

.foot { position:absolute; left:7mm; right:7mm; bottom:0; height:7mm; align-items:center; display:flex; justify-content:space-between; gap:6mm;
        font-size:6.4pt; color:var(--muted); }
.foot span:last-child { white-space:nowrap; }
.arch { width:100%; }
.band { flex:none; }
.main { flex:1; display:flex; flex-direction:column; justify-content:space-between; min-height:0; padding-top:7mm; }
.main .label { margin-top:0; }
"""

SHAPES = ('<svg class="shape" viewBox="0 0 800 160" preserveAspectRatio="none" aria-hidden="true">'
          '<polygon points="330,0 470,0 560,160 420,160" fill="#ffffff" opacity=".05"/>'
          '<polygon points="560,40 640,0 800,0 800,160 640,160" fill="#ffffff" opacity=".07"/>'
          '<polygon points="660,160 730,70 800,120 800,160" fill="#ffffff" opacity=".10"/></svg>')

page1 = f"""
<section class="page">
  <div class="band">{SHAPES}
    <div class="band-in">
      <div>
        <h1>An AI-Assisted Research Workflow for Retail Expansion</h1>
        <div class="sub">Turns fragmented public data into traceable site-screening recommendations and targeted
          follow-up research.</div>
      </div>
    </div>
  </div>

  <main class="main"><div class="grp">
  <div class="label">1. Situation</div>
  <div class="sit">
    <div class="who-col">{who_html}</div>
    <div class="card why"><div class="cap">Why desk research falls short today</div><ul>{why_html}</ul></div>
  </div>

  </div><div class="grp">
  <div class="label">2. How it works <small>System architecture</small></div>
  <div class="arch">{arch}</div>
  </div>

  <div class="grp">
  <div class="label">Highlights</div>
  <div class="hls">{hl_html}</div>
  </div></main>
  {FOOT.replace("PAGE", "1")}
</section>"""

page2 = f"""
<section class="page">
  <div class="band">{SHAPES}
    <div class="band-in">
      <div>
        <h1>3. Case study</h1>
        <div class="sub2">7-Eleven Due Diligence: 3 Berlin Transit Nodes</div>
      </div>
      <div class="side">Hypothetical 7-Eleven convenience store: fresh food, coffee and daily goods, 50–100 m²
        at a station, trading all day.
        <div class="sites-band">{band_sites}</div></div>
    </div>
  </div>

  <main class="main">
  <div class="card">
    <div class="h2">Measured against 139 Berlin stations, not just against each other</div>
    <div class="note">Grey dots: inner-Berlin S/U-Bahn stations; bands and dashed lines: each day type's P10 / P90.
      Brandenburger Tor is in the city's least contested tenth on both day types
      ({bt['comp_per10k_weekday']:.1f} / {bt['comp_per10k_weekend']:.1f} competitors per 10,000 people); Schönhauser Allee
      in the most crowded tenth ({sa['comp_per10k_weekday']:.1f} / {sa['comp_per10k_weekend']:.1f}).</div>
    <div class="pair"><div>{fluid(city_wd)}</div><div>{fluid(city_we)}</div></div>
  </div>

  <div class="duo">
    <div class="card">
      <div class="h2">The ranking survives its least certain inputs</div>
      <div class="note">Brandenburger Tor is first in all {len(D['scenarios'])} stress tests, including both downsides at
        once ({joint['brandenburger_tor']:.1f} vs. {joint['warschauer_strasse']:.1f}).</div>
      {fluid(stress)}
    </div>
    <div class="card">
      <div class="h2">Each node draws a different crowd</div>
      <div class="note">People per day within 400 m (upper bar weekday, lower weekend). Visitors are
        {share(bt, 'visitors'):.0%} of Brandenburger Tor's weekday demand, the figure to verify first.</div>
      {fluid(mix)}
    </div>
  </div>
  <div class="grp">
  <div class="label">4. Manual vs. tool <small><span class="lg n"></span>Manual / analyst<span class="lg tl"></span>Tool</small></div>
  <div class="mvt">
    <div class="card"><div class="cap">×1,900 · Time</div>
      <div class="big"><span class="m">32 min</span><span class="ar">→</span><span class="t">1 s</span></div>
      <div class="sm">Competitor inventory for one node.</div></div>
    <div class="card"><div class="cap">City scale</div>
      <div class="big"><span class="m">74 h</span><span class="ar">→</span><span class="t">1.5 s</span></div>
      <div class="sm">Competitor inventories for all 139 reference stations.</div></div>
    <div class="card"><div class="cap">Labour</div>
      <div class="big"><span class="m">9</span><span class="ar">→</span><span class="t">2</span></div>
      <div class="sm">Analyst touchpoints per node: 9 manual steps become 2 reviews (AI flags, field visits).</div></div>
    <div class="card"><div class="cap">Cost</div>
      <div class="big"><span class="t">€0</span></div>
      <div class="sm">Marginal cost of a rerun; optional AI audit $0.25–1.25 per site.</div></div>
  </div>
  <div class="note" style="margin:1.2mm 0 0">Evidence: one manual benchmark (one analyst, one node, not blind). The
    139-station manual time is extrapolated at 32 min each; tool times are measured (139 stations from cached data).</div>

  <div class="card" style="margin-top:1.6mm; padding:1.6mm 3mm">
    <div class="techrow"><span class="k">Tech stack</span>{chips(stack, "a")}</div>
    <div class="techrow" style="margin-top:1.2mm"><span class="k">Engineering &amp; evaluation</span>{chips(eng, "b")}</div>
  </div>
  </div></main>
  {FOOT.replace("PAGE", "2")}
</section>"""

html = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Project Portfolio</title>'
        f'<style>{CSS}</style></head><body>{page1}{page2}</body></html>')
(HERE / "portfolio.html").write_text(html, encoding="utf-8")
print("written")
