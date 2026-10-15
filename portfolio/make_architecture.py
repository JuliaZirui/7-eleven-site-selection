"""Build portfolio/architecture.svg (+ an HTML wrapper for PNG/PDF export).

Run: python3 portfolio/make_architecture.py
"""
from pathlib import Path
from html import escape

W, H = 1600, 985
OUT = Path(__file__).resolve().parent

STYLE = {
    "det":   dict(fill="#e8eef7", stroke="#1d4e89", title="#0b2545", body="#26364d"),
    "store": dict(fill="#eef0f2", stroke="#5b6470", title="#1f2933", body="#3c4650"),
    "llm":   dict(fill="#5b2fa6", stroke="#3f1d7a", title="#ffffff", body="#efe7fb"),
    "ai":    dict(fill="#f3edfc", stroke="#6b3fb3", title="#3f1d7a", body="#3b2a5c"),
    "guard": dict(fill="#fdeeee", stroke="#b42318", title="#7a1a12", body="#5c2a25"),
    "human": dict(fill="#fff3e0", stroke="#c77700", title="#7a4700", body="#5c3d12"),
    "out":   dict(fill="#0b2545", stroke="#0b2545", title="#ffffff", body="#d6e0ee"),
    "note":  dict(fill="#ffffff", stroke="#8a94a3", title="#3c4650", body="#4b5563"),
}

parts: list[str] = []


def box(x, y, w, h, kind, title, lines=(), dashed=False, tag=None, title_size=13.5, body_size=11):
    s = STYLE[kind]
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{s["fill"]}" '
                 f'stroke="{s["stroke"]}" stroke-width="1.6"{dash}/>')
    ty = y + 21
    parts.append(f'<text x="{x + 11}" y="{ty}" font-size="{title_size}" font-weight="700" fill="{s["title"]}">'
                 f'{escape(title)}</text>')
    if tag:
        tw = 7.2 * len(tag) + 12
        parts.append(f'<rect x="{x + w - tw - 7}" y="{y + 7}" width="{tw}" height="17" rx="8.5" '
                     f'fill="{tag[1] if isinstance(tag, tuple) else "#ffffff"}" opacity="0.95"/>')
    for i, line in enumerate(lines):
        parts.append(f'<text x="{x + 11}" y="{ty + 18 + i * 15}" font-size="{body_size}" fill="{s["body"]}">'
                     f'{line}</text>')


def pill(x, y, text, kind="det", size=11):
    s = STYLE[kind]
    w = int(len(text) * size * 0.56) + 22
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="24" rx="12" fill="{s["fill"]}" '
                 f'stroke="{s["stroke"]}" stroke-width="1.2"/>')
    parts.append(f'<text x="{x + w / 2}" y="{y + 16}" font-size="{size}" text-anchor="middle" '
                 f'fill="{s["title"]}" font-weight="600">{escape(text)}</text>')
    return x + w + 9


def arrow(points, color="#1d4e89", dashed=False, width=1.8, label=None, label_at=None, anchor="middle"):
    d = "M " + " L ".join(f"{px} {py}" for px, py in points)
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    marker = {"#1d4e89": "a-blue", "#6b3fb3": "a-purple", "#c77700": "a-amber", "#5b6470": "a-gray"}[color]
    parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{dash} '
                 f'marker-end="url(#{marker})"/>')
    if label:
        lx, ly = label_at
        parts.append(f'<text x="{lx}" y="{ly}" font-size="10.5" font-style="italic" fill="{color}" '
                     f'text-anchor="{anchor}">{escape(label)}</text>')


def lane(y, h, label, fill, lx=36):
    parts.append(f'<rect x="20" y="{y}" width="{W - 40}" height="{h}" rx="10" fill="{fill}"/>')
    parts.append(f'<text x="{lx}" y="{y + 19}" font-size="11.5" font-weight="700" letter-spacing="1.4" '
                 f'fill="#5b6470">{escape(label)}</text>')


# ---------------------------------------------------------------- header
parts.append('<text x="24" y="40" font-size="25" font-weight="700" fill="#0b2545">'
             'Transit-Node Site Screening — system architecture</text>')
parts.append('<text x="24" y="63" font-size="13.5" fill="#5b6470">A deterministic Python pipeline owns every '
             'score. Two Claude-powered components work inside guardrails: an LLM data audit and a bounded '
             'follow-up agent.</text>')
lx = 870
for kind, text in (("det", "Deterministic Python"), ("store", "Data store"), ("llm", "LLM (Claude)"),
                   ("guard", "Plain-code guard"), ("human", "Human in the loop"), ("out", "Output")):
    s = STYLE[kind]
    parts.append(f'<rect x="{lx}" y="78" width="14" height="14" rx="3" fill="{s["fill"]}" stroke="{s["stroke"]}"/>')
    parts.append(f'<text x="{lx + 19}" y="90" font-size="11" fill="#3c4650">{text}</text>')
    lx += 19 + len(text) * 6.0 + 14

# ---------------------------------------------------------------- lanes
lane(100, 110, "INPUTS", "#f6f8fb")
lane(222, 150, "DETERMINISTIC PIPELINE · PYTHON", "#f6f8fb", lx=160)
lane(384, 470, "AI / LLM / AGENT LAYER · ANTHROPIC CLAUDE", "#f8f5fd", lx=960)
lane(866, 104, "ENGINEERING & QUALITY", "#f6f8fb")

# ---------------------------------------------------------------- inputs
box(56, 126, 544, 74, "store", "Open public data — no paid APIs", (
    "OpenStreetMap (Overpass) · Overture Maps · Berlin LOR / MSS 2025 · IHK Gewerbedaten",
    "Wikidata + Wikipedia pageviews · VBB GTFS timetable · DB InfraGO station price classes"))
box(623, 126, 165, 74, "store", "Site input", ("store concept + 3", "candidate sites (JSON)"))
box(1001, 126, 165, 74, "det", "Calibration", ("139 Berlin stations →", "P10/P90 anchors"))
box(1190, 126, 354, 74, "out", "Outputs", (
    "Diligence ranking · business fit + evidence quality",
    "evidence gaps · field-research checklist"))

# ---------------------------------------------------------------- pipeline
BX = [56 + i * 189 for i in range(8)]
BY, BW, BH = 248, 165, 108
steps = [
    ("det", "Ingest", ["API clients with", "retries + mirror", "fallback"]),
    ("store", "Snapshot store", ["SQLite, append-only", "data-as-of ≠ fetched", "unknown ≠ 0"]),
    ("det", "Clean & conflate", ["dedupe · cross-provider", "POI conflation ·", "opening-hours parser"]),
    ("det", "Metrics", ["people per day =", "residents + workers +", "visitors; competitors"]),
    ("det", "Gap detection", ["missing · stale ·", "conflicting inputs", "that could change a call"]),
    ("det", "Scoring", ["anchored 0–100 scales", "business fit and", "evidence quality"]),
    ("det", "Sensitivity", ["inputs first:", "joint downside,", "then weights"]),
    ("out", "Report", ["sourced Markdown ·", "42 citations re-", "checked vs. the DB"]),
]
for i, (kind, title, lines) in enumerate(steps):
    box(BX[i], BY, BW, BH, kind, title, lines)
for i in range(7):
    arrow([(BX[i] + BW, BY + 54), (BX[i + 1] - 3, BY + 54)])

arrow([(138, 200), (138, BY - 3)])                     # sources -> ingest
arrow([(705, 200), (705, BY - 3)])                     # site input -> metrics
arrow([(1083, 200), (1083, BY - 3)])                   # calibration -> scoring
arrow([(1461, BY), (1461, 203)])                       # report -> outputs

# ---------------------------------------------------------------- panel 1: data audit
parts.append('<rect x="44" y="410" width="726" height="300" rx="9" fill="#ffffff" stroke="#6b3fb3" '
             'stroke-width="1.4"/>')
parts.append('<text x="300" y="433" font-size="14.5" font-weight="700" fill="#3f1d7a">'
             '① LLM data-plausibility audit</text>')
parts.append('<text x="538" y="433" font-size="12" fill="#6b3fb3">review only; never edits a number</text>')

box(66, 450, 200, 108, "det", "Candidate rules", [
    "Python lists likely-wrong records:", "whole-company headcount at", "one address · fame ≫ capacity ·",
    "missing employer · duplicates"], body_size=10.5)
box(296, 450, 220, 108, "llm", "Claude Sonnet 5.5", [
    "Messages API · tool use", "web search + web fetch", "strict-schema findings", "(report_finding), sources cited"],
    body_size=10.5)
box(546, 450, 204, 108, "guard", "Audit guard", [
    "validates every finding", "USD cap per run", "frozen prompt hash (SHA-256)", "required on unseen nodes"],
    body_size=10.5)
box(546, 588, 204, 100, "human", "Analyst review", [
    "accepts / rejects each flag", "in the review queue (CSV)", "audit_apply writes accepted", "rows only"],
    body_size=10.5)
box(296, 588, 220, 100, "store", "Correction tables", [
    "verified on-site headcounts ·", "unregistered employers ·", "venue attendance · POI", "exclusions"],
    body_size=10.5)
box(66, 588, 200, 100, "note", "Evaluated before adoption", [
    "answer key + holdout fixed", "before any code · 4/6 holdout", "issues caught · 57/58 flags", "confirmed at unseen stations"],
    dashed=True, body_size=10.5, title_size=12.5)

arrow([(230, 356), (230, 447)], label="stored records", label_at=(222, 400), anchor="end")
arrow([(266, 504), (293, 504)])
arrow([(516, 504), (543, 504)], color="#6b3fb3")
arrow([(648, 558), (648, 585)], color="#6b3fb3")
arrow([(546, 638), (519, 638)], color="#c77700")
arrow([(406, 588), (406, 572), (281, 572), (281, BY + BH + 3)], color="#5b6470",
      label="reviewed corrections", label_at=(289, 400), anchor="start")

# ---------------------------------------------------------------- panel 2: follow-up agent
parts.append('<rect x="800" y="410" width="756" height="300" rx="9" fill="#ffffff" stroke="#6b3fb3" '
             'stroke-width="1.4"/>')
parts.append('<text x="912" y="433" font-size="14.5" font-weight="700" fill="#3f1d7a">'
             '② Bounded follow-up agent</text>')
parts.append('<text x="1128" y="433" font-size="12" fill="#6b3fb3">decides which gaps get live calls; '
             'never sees a score</text>')

box(820, 450, 160, 108, "det", "Gap list", ["only gaps that could", "change a conclusion;", "no raw-count triggers"],
    body_size=10.5)
# pluggable policy container
parts.append('<rect x="1004" y="450" width="300" height="138" rx="7" fill="#f3edfc" stroke="#6b3fb3" '
             'stroke-width="1.6"/>')
parts.append('<text x="1015" y="471" font-size="13.5" font-weight="700" fill="#3f1d7a">Pluggable decision policy</text>')
for i, (kind, text) in enumerate((("det", "A · Rule baseline (detection order)"),
                                  ("det", "B · Impact-ordered rule — default"),
                                  ("llm", "C · Claude Opus 5.5 — tool-use loop"))):
    s = STYLE[kind]
    yy = 482 + i * 34
    parts.append(f'<rect x="1016" y="{yy}" width="276" height="27" rx="5" fill="{s["fill"]}" stroke="{s["stroke"]}"/>')
    parts.append(f'<text x="1028" y="{yy + 18}" font-size="11.5" font-weight="600" fill="{s["title"]}">{text}</text>')

box(1330, 450, 206, 138, "guard", "Executor guard", [
    "allowed tool only", "budget: 4 calls / run,", "2 per site", "no retry of a failed source",
    "LLM error → falls back to rule"], body_size=10.5)
box(1004, 612, 300, 76, "det", "Live tools (refetch)", [
    "refetch_osm_pois · refetch_ihk_gewerbedaten", "refetch_lor_population"], body_size=10.5)
box(1330, 612, 206, 76, "human", "Field flag", ["flag_field_research +", "LLM-written field question"],
    body_size=10.5)
box(820, 588, 160, 100, "note", "Evaluated", [
    "16 pre-registered", "scenarios: A 10/16 ·", "B 14/16 · C 16/16", "($0.019 per run)"],
    dashed=True, body_size=10.5, title_size=12.5)

arrow([(894, BY + BH), (894, 447)], label="gaps", label_at=(902, 400), anchor="start")
arrow([(980, 504), (1001, 504)])
arrow([(1304, 504), (1327, 504)], color="#6b3fb3")
arrow([(1330, 560), (1307, 560)], color="#6b3fb3", dashed=True)
arrow([(1400, 588), (1400, 600), (1154, 600), (1154, 609)])
arrow([(1470, 588), (1470, 609)], color="#c77700")
# field flag -> report checklist (right margin)
arrow([(1536, 650), (1568, 650), (1568, 330), (1547, 330)], color="#c77700")
parts.append('<text x="1582" y="490" font-size="10.5" font-style="italic" fill="#c77700" text-anchor="middle" '
             'transform="rotate(90 1582 490)">field checklist</text>')
# refetch -> new snapshot -> pipeline reruns (bottom + left margin)
arrow([(1154, 688), (1154, 724), (36, 724), (36, BY + 40), (BX[0] - 3, BY + 40)],
      label="refetch → new snapshot → pipeline reruns", label_at=(600, 740))

# ---------------------------------------------------------------- guardrails strip
parts.append('<rect x="44" y="760" width="1512" height="80" rx="9" fill="#ffffff" stroke="#b42318" '
             'stroke-width="1.3" stroke-dasharray="6 4"/>')
parts.append('<text x="60" y="782" font-size="13.5" font-weight="700" fill="#7a1a12">AI guardrails '
             '(both components)</text>')
x = 60
for t in ("AI never writes a score", "Plain-code guard between model and data",
          "Human approval for every data change", "Hard budgets: calls, web searches, USD",
          "Rule fallback on any API error"):
    x = pill(x, 796, t, "guard", size=11.5)

# ---------------------------------------------------------------- engineering lane
x = 44
for t, k in (("Python 3.9 / 3.12", "det"), ("SQLite · 6 tables", "store"),
             ("Anthropic Python SDK", "llm"), ("Claude Opus 5.5 · Sonnet 5.5", "llm"),
             ("Tool use · web search / fetch", "ai"), ("unittest · 241 tests", "det"),
             ("GitHub Actions CI", "det"), ("Offline reproducibility check", "det"),
             ("CLI: validate · demo · refresh", "det")):
    x = pill(x, 894, t, k, size=10.5)
x = 44
for t, k in (("Pre-registered AI evaluations", "ai"), ("Prompt-leakage test vs. answer key", "ai"),
             ("Mocked LLM client + one real-SDK round trip", "ai"), ("Geospatial: haversine, area-weighted catchments", "det"),
             ("Opening-hours parser (overnight spans)", "det")):
    x = pill(x, 928, t, k, size=10.5)

defs = """<defs>
  <marker id="a-blue" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#1d4e89"/></marker>
  <marker id="a-purple" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#6b3fb3"/></marker>
  <marker id="a-amber" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#c77700"/></marker>
  <marker id="a-gray" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#5b6470"/></marker>
</defs>"""

svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
       f'font-family="Helvetica Neue, Helvetica, Arial, sans-serif">\n{defs}\n'
       f'<rect width="{W}" height="{H}" fill="#ffffff"/>\n' + "\n".join(parts) + "\n</svg>\n")
(OUT / "architecture_detailed.svg").write_text(svg, encoding="utf-8")
(OUT / "architecture_detailed.html").write_text(
    "<!doctype html><html><head><meta charset='utf-8'><title>Architecture</title><style>"
    f"@page {{ size: {W}px {H}px; margin: 0; }} html,body {{ margin:0; padding:0; background:#fff; }}"
    "svg { display:block; }</style></head><body>" + svg + "</body></html>", encoding="utf-8")
print("written")
