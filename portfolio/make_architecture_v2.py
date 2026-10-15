"""Architecture flowchart in the portfolio style (accent-bar cards, numbered
steps, green LLM / agent boxes, legend card). Same flow and content as
make_architecture_simple.py. Output: portfolio/architecture_v2.{svg,html}.

    python3 portfolio/make_architecture_v2.py
"""
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent
W, H = 1100, 790
BG = "#e7edf5"
NAVY, BLUE, LBLUE, INK, MUTED = "#0b2545", "#1a6fd1", "#6fa3e3", "#1c2b3a", "#5b6470"
GREEN, GREEN_INK = "#7cc243", "#1d3a0c"
ARROW = "#8ea7c7"
LX, LW = 40, 480          # left column
RX, RW = 620, 446         # right column
LC = LX + LW / 2

p: list[str] = []


def text(x, y, s, size, fill, weight=400, anchor="start"):
    p.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
             f'text-anchor="{anchor}">{escape(s)}</text>')


def card(x, y, w, h, accent, fill="#ffffff"):
    p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}"/>')
    p.append(f'<rect x="{x}" y="{y}" width="6" height="{h}" fill="{accent}"/>')


def step(y, h, num, title, lines, fill="#ffffff", accent=BLUE, tcol=BLUE, bcol=INK, pill=BLUE):
    card(LX, y, LW, h, accent, fill)
    p.append(f'<rect x="{LX + 26}" y="{y + 14}" width="38" height="22" rx="11" fill="{pill}"/>')
    text(LX + 45, y + 29.5, num, 11.5, "#ffffff", 700, "middle")
    text(LX + 76, y + 32, title, 21, tcol, 700)
    for i, line in enumerate(lines):
        text(LX + 26, y + 56 + i * 18, line, 14.5, bcol)


def side(y, h, title, lines, accent=LBLUE):
    card(RX, y, RW, h, accent)
    text(RX + 26, y + 33, title, 21, BLUE, 700)
    for i, line in enumerate(lines):
        text(RX + 26, y + 57 + i * 19, line, 14.5, INK)


def llm(y, h, title, lines, pill=None):
    p.append(f'<rect x="{RX}" y="{y}" width="{RW}" height="{h}" rx="12" fill="{GREEN}"/>')
    text(RX + 26, y + 34, title, 21, GREEN_INK, 700)
    for i, line in enumerate(lines):
        text(RX + 26, y + 60 + i * 19, line, 14.5, GREEN_INK)
    if pill:
        py = y + 60 + len(lines) * 19
        p.append(f'<rect x="{RX + 26}" y="{py}" width="270" height="24" rx="12" fill="#ffffff"/>')
        text(RX + 26 + 135, py + 16.5, pill, 11.5, GREEN_INK, 700, "middle")


def arrow(pts, label=None, at=None, anchor="start"):
    """Line ending exactly on the target edge, with an open chevron tip
    (stroke only, so even a short segment stays visible). Labels sit in
    free space beside the line, without a background patch."""
    d = "M " + " L ".join(f"{x} {y}" for x, y in pts)
    p.append(f'<path d="{d}" fill="none" stroke="{ARROW}" stroke-width="1.8" stroke-linejoin="round" '
             f'marker-end="url(#a)"/>')
    if label:
        x, y = at
        text(x, y, label, 11.5, MUTED, 400, anchor)


p.append(f'<rect width="{W}" height="{H}" fill="{BG}"/>')

# ---------------------------------------------------------------- left column
card(LX, 20, LW, 68, NAVY, "#e8f0fa")
text(LX + 26, 49, "Site candidates + store concept", 21, NAVY, 700)
text(LX + 26, 73, "3 Berlin transit nodes · all-day convenience store", 14.5, INK)

SH = 80
step(116, SH, "01", "Collect & clean", ["Fetch, dedupe and merge POIs across providers;", "store as dated SQLite snapshots."])
step(224, SH, "02", "Metrics", ["People per day (residents + workers + visitors);", "competitors open per day."])

cy, hw, hh = 378, 228, 44
p.append(f'<polygon points="{LC},{cy - hh} {LC + hw},{cy} {LC},{cy + hh} {LC - hw},{cy}" fill="#ffffff" '
         f'stroke="{BLUE}" stroke-width="2"/>')
text(LC, cy - 5, "Data gap that could", 17.5, BLUE, 700, "middle")
text(LC, cy + 18, "change the result?", 17.5, BLUE, 700, "middle")

step(462, SH, "03", "Scoring", ["Business fit and evidence quality,", "scored separately."])
step(570, SH, "04", "Sensitivity analysis", ["Inputs first: visitors, workers, under-mapping,", "both at once; then weights."])
step(678, SH + 4, "05", "Outputs", ["Sourced report: diligence ranking,", "evidence gaps and field-research checklist."],
     fill=BLUE, accent=BLUE, tcol="#ffffff", bcol="#ffffff", pill=NAVY)

# ---------------------------------------------------------------- right column
side(20, 68, "Open public data", ["OSM, Overture, LOR, IHK, Wikipedia, GTFS"])
llm(124, 150, "LLM data audit · Claude", ["Rules list records that look wrong.",
                                          "Claude checks each one with web search.",
                                          "Code guard: an analyst accepts or rejects."],
    pill="Never changes a number itself")
llm(322, 114, "Follow-up agent", ["Policy: A rule / B impact-ordered (default) / C Claude",
                                  "Code guard: allowed tools, max 4 calls per run",
                                  "Action: refetch data, or flag a field check"])
side(462, SH, "139 reference stations", ["P10 / P90 anchors for demand and competition"])

# legend card
p.append(f'<rect x="{RX}" y="570" width="{RW}" height="192" fill="#ffffff"/>')
text(RX + 26, 600, "READING THE FLOW", 12.5, BLUE, 700)
for i, (fill, stroke, lab, sub) in enumerate((
        ("#e8f0fa", "#9db6d6", "Input", "Site brief and public data"),
        ("#ffffff", "#9db6d6", "Python pipeline", "Collection, metrics and evaluation"),
        (GREEN, GREEN, "LLM / agent", "Data audit and targeted follow-up"),
        (BLUE, BLUE, "Output", "Sourced screening report"))):
    y = 618 + i * 36
    p.append(f'<rect x="{RX + 26}" y="{y}" width="16" height="16" fill="{fill}" stroke="{stroke}" stroke-width="1.3"/>')
    text(RX + 54, y + 12, lab, 13.5, NAVY, 700)
    text(RX + 54, y + 29, sub, 12, MUTED)

# ---------------------------------------------------------------- arrows
GAP = (LX + LW + RX) / 2        # centre of the gutter between the columns
arrow([(LC, 88), (LC, 116)])
arrow([(LC, 196), (LC, 224)])
arrow([(LC, 304), (LC, cy - hh)])
arrow([(LC, cy + hh), (LC, 462)], "no / budget spent", (LC + 12, 446))
arrow([(LC, 542), (LC, 570)])
arrow([(LC, 650), (LC, 678)])
arrow([(850, 88), (850, 100), (470, 100), (470, 116)], "fetch", (862, 97))
arrow([(LX + LW, 150), (RX, 150)], "suspect records", (GAP, 142), "middle")
arrow([(RX, 260), (LX + LW, 260)], "accepted fixes", (GAP, 252), "middle")
arrow([(LC + hw, cy), (RX, cy)], "yes", ((LC + hw + RX) / 2, cy - 8), "middle")
arrow([(850, 322), (850, 316), (470, 316), (470, 304)], "refetch → recompute", (660, 309), "middle")
arrow([(RX, 502), (LX + LW, 502)], "scales", (GAP, 494), "middle")

defs = (f'<defs><marker id="a" viewBox="0 0 10 10" refX="9.2" refY="5" markerWidth="9" markerHeight="9" '
        f'markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M1.5 1.5 L9 5 L1.5 8.5" fill="none" '
        f'stroke="{ARROW}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>')
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
       f'font-family="Helvetica Neue, Helvetica, Arial, sans-serif">{defs}' + "".join(p) + "</svg>\n")
(OUT / "architecture_v2.svg").write_text(svg, encoding="utf-8")
(OUT / "architecture_v2.html").write_text(
    "<!doctype html><html><head><meta charset='utf-8'><title>Architecture</title><style>"
    f"@page {{ size: {W}px {H}px; margin: 0; }} html,body {{ margin:0; }} svg {{ display:block; }}"
    "</style></head><body>" + svg + "</body></html>", encoding="utf-8")
print("written")
