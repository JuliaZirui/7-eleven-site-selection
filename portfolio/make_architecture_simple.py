"""Build the simplified architecture flowchart: portfolio/architecture.{svg,html}.

Run: python3 portfolio/make_architecture_simple.py
"""
import sys
from html import escape
from pathlib import Path

# Optional: --scale F (text size multiplier) and --out NAME (file stem), for
# embedding the same diagram at a smaller rendered size (portfolio).
ARGS = dict(zip(sys.argv[1::2], sys.argv[2::2]))
F = float(ARGS.get("--scale", 1))
STEM = ARGS.get("--out", "architecture")
FL = 1 + (F - 1) * 0.4      # arrow labels grow less: they sit in narrow gaps

W, H = 1240, 1060
OUT = Path(__file__).resolve().parent
MX, MW = 60, 500          # main column
SX, SW = 680, 500         # side column
MC, SC = MX + MW / 2, SX + SW / 2

STYLE = {
    "step":  dict(fill="#ffffff", stroke="#b8bec6", title="#1f2933", body="#4b5563"),
    "data":  dict(fill="#f4f6f8", stroke="#b8bec6", title="#1f2933", body="#4b5563"),
    "ai":    dict(fill="#f1ebfb", stroke="#6b3fb3", title="#3f1d7a", body="#3b2a5c"),
    "start": dict(fill="#e8eef7", stroke="#1d4e89", title="#0b2545", body="#26364d"),
    "end":   dict(fill="#0b2545", stroke="#0b2545", title="#ffffff", body="#d6e0ee"),
}
parts: list[str] = []


def box(x, y, w, h, kind, title, lines=(), rx=10, sw=1.5):
    s = STYLE[kind]
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{s["fill"]}" '
                 f'stroke="{s["stroke"]}" stroke-width="{sw}"/>')
    n = len(lines)
    top = y + h / 2 - (n * 21 * F) / 2 + 5
    parts.append(f'<text x="{x + w / 2}" y="{top}" text-anchor="middle" font-size="{17 * F:.1f}" font-weight="700" '
                 f'fill="{s["title"]}">{escape(title)}</text>')
    for i, line in enumerate(lines):
        parts.append(f'<text x="{x + w / 2}" y="{top + 24 * F + i * 21 * F:.1f}" text-anchor="middle" font-size="{14 * F:.1f}" '
                     f'fill="{s["body"]}">{escape(line)}</text>')


def arrow(points, label=None, at=None, anchor="middle", color="#9aa3ad"):
    d = "M " + " L ".join(f"{x} {y}" for x, y in points)
    parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.8" marker-end="url(#a)"/>')
    if label:
        parts.append(f'<text x="{at[0]}" y="{at[1]}" text-anchor="{anchor}" font-size="{13.5 * FL:.1f}" '
                     f'fill="#4b5563">{escape(label)}</text>')


# title + legend
parts.append(f'<text x="60" y="40" font-size="{22 * min(F, 1.12):.1f}" font-weight="700" fill="#0b2545">'
             'Transit-Node Site Screening — how it works</text>')
lx = 760 if F == 1 else 1180 - sum(21 * F + len(t) * 7.4 * F + 22 for t in ("Input", "Python pipeline", "LLM / agent", "Output"))
for kind, text in (("start", "Input"), ("step", "Python pipeline"), ("ai", "LLM / agent"), ("end", "Output")):
    s = STYLE[kind]
    parts.append(f'<rect x="{lx}" y="27" width="15" height="15" rx="3" fill="{s["fill"]}" stroke="{s["stroke"]}"/>')
    parts.append(f'<text x="{lx + 21 * F}" y="40" font-size="{13 * F:.1f}" fill="#4b5563">{text}</text>')
    lx += 21 * F + len(text) * 7.4 * F + 22

# 1 input
box(MX, 72, MW, 80, "start", "Site candidates + store concept",
    ["3 Berlin transit nodes · all-day convenience store"])
box(SX, 72, SW, 80, "data", "Open public data",
    ["OSM, Overture, LOR, IHK, Wikipedia, GTFS"])

# 2 collect & clean
box(MX, 200, MW, 90, "step", "Collect & clean",
    ["fetch, dedupe, merge POIs across providers;", "store as dated SQLite snapshots"])
arrow([(MC, 152), (MC, 197)])
arrow([(SC, 152), (SC, 172), (MX + MW - 60, 172), (MX + MW - 60, 197)], "fetch", (SC - 18, 166), "end")

# LLM data audit (side)
box(SX, 220, SW, 176, "ai", "LLM data audit · Claude", [
    "Rules list records that look wrong;",
    "Claude checks each one with web search",
    "Code guard → an analyst accepts or rejects",
    "Never changes a number itself",
], sw=2.2)

# 3 metrics
box(MX, 338, MW, 90, "step", "Metrics",
    ["people per day (residents + workers + visitors);", "competitors open per day"])
arrow([(MC, 290), (MC, 335)])
arrow([(MX + MW, 245), (SX - 3, 245)], "suspect records", ((MX + MW + SX) / 2, 236))
arrow([(SX, 370), (MX + MW + 3, 370)], "accepted fixes", ((MX + MW + SX) / 2, 361))

# 4 decision
dy, dh = 478, 120
cx, cy = MC, dy + dh / 2
parts.append(f'<polygon points="{cx},{dy} {cx + 160},{cy} {cx},{dy + dh} {cx - 160},{cy}" fill="#ffffff" '
             f'stroke="#b8bec6" stroke-width="1.5"/>')
parts.append(f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" font-size="{15 * F:.1f}" fill="#1f2933">Data gap that</text>')
parts.append(f'<text x="{cx}" y="{cy + 16 * F:.1f}" text-anchor="middle" font-size="{15 * F:.1f}" fill="#1f2933">could change the result?</text>')
arrow([(MC, 428), (MC, dy - 3)])

# follow-up agent (side)
box(SX, 466, SW, 144, "ai", "Follow-up agent", [
    "Policy: A rule / B impact-ordered (default) / C Claude",
    "Code guard: allowed tools, max 4 calls per run",
    "Action: refetch data, or flag a field check",
], sw=2.2)
arrow([(cx + 160, cy), (SX - 3, cy)], "yes", ((cx + 160 + SX) / 2, cy - 9))
arrow([(SX + 120, 466), (SX + 120, 448), (MX + MW - 50, 448), (MX + MW - 50, 431)],
      "refetch → recompute", (SX + 112, 442), "end")

# 5 scoring
box(MX, 652, MW, 90, "step", "Scoring",
    ["business fit and evidence quality,", "scored separately"])
arrow([(MC, dy + dh), (MC, 649)], "no / budget spent", (MC + 10, 630), "start")
box(SX, 652, SW, 90, "data", "139 reference stations",
    ["P10 / P90 anchors for demand and competition"])
arrow([(SX, 697), (MX + MW + 3, 697)], "scales", ((MX + MW + SX) / 2, 688))

# 6 sensitivity
box(MX, 790, MW, 90, "step", "Sensitivity analysis",
    ["inputs first (visitors, workers, under-mapping,", "both at once), then weights"])
arrow([(MC, 742), (MC, 787)])

# 7 outputs
box(MX, 928, MW, 90, "end", "Outputs",
    ["sourced report: diligence ranking,", "evidence gaps, field-research checklist"])
arrow([(MC, 880), (MC, 925)])

defs = ('<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#9aa3ad"/></marker></defs>')
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
       f'font-family="Helvetica Neue, Helvetica, Arial, sans-serif">{defs}'
       f'<rect width="{W}" height="{H}" fill="#ffffff"/>' + "\n".join(parts) + "</svg>\n")
(OUT / f"{STEM}.svg").write_text(svg, encoding="utf-8")
(OUT / f"{STEM}.html").write_text(
    "<!doctype html><html><head><meta charset='utf-8'><title>Architecture</title><style>"
    f"@page {{ size: {W}px {H}px; margin: 0; }} html,body {{ margin:0; padding:0; background:#fff; }}"
    "svg { display:block; }</style></head><body>" + svg + "</body></html>", encoding="utf-8")
print("written")
