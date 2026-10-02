"""Parser for the subset of the OSM `opening_hours` syntax that actually
appears in this project's POI observations (verified against every
distinct `opening_hours_raw` value in the Day 2 snapshot — see
site_candidates.md "Day 2 results"). Supports:

- `24/7`
- day selectors: single days, ranges (`Mo-Fr`), lists (`Fr,Sa`), and
  combinations (`Su,PH`), including `PH` (public holiday) as a token
- multiple rules per string, with OSM's separator semantics: after
  `;` a rule *replaces* the earlier hours of the days it names; after
  `,` it *adds* to them (e.g. `Mo-Fr 08:00-18:00; Fr 08:00-12:00`
  closes at 12:00 on Friday, not 18:00)
- `off` to mark a day explicitly closed
- overnight spans, where closing time <= opening time means "past
  midnight" (e.g. `09:00-01:00`). The after-midnight tail belongs to
  the rule of the day it started on, so replacing Friday's rule drops
  Friday night's tail into Saturday, but not Thursday night's tail
  into Friday

Not a general OSM opening_hours implementation (no `Mo-Fr,PH off`
exceptions beyond simple day selectors, no multiple time ranges per
day like `07:00-12:00,13:00-18:00`, no month/date-range rules). A
string containing *any* text outside recognized rules and separators
is treated as unparseable (returns None) rather than partially read —
same "never guess" rule as the rest of the pipeline.

`PH` (public holiday) tokens are recognized in day selectors but
dropped when building the weekly schedule: this project has no
holiday calendar, so a rule scoped to `PH` can't be mapped to a
specific weekday. It's a documented, bounded gap, not a silent one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Mo=0 .. Su=6, matching Python's own Monday-first weekday() convention.
WEEKDAY_TOKENS: tuple[str, ...] = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")

# Representative days used to evaluate "is this competitor open during
# window X" — a fixed, explainable choice rather than averaging over
# all 7 days. Wednesday avoids Monday/Friday edge effects (later
# opening, earlier closing) within the working week. Saturday, not
# Sunday, represents the weekend baseline: Germany's Ladenschlussgesetz
# keeps most non-food/non-kiosk shops closed on Sundays, which would
# understate weekend competition rather than describe a typical one.
REPRESENTATIVE_WEEKDAY = WEEKDAY_TOKENS.index("We")
REPRESENTATIVE_WEEKEND = WEEKDAY_TOKENS.index("Sa")

_DAY_TOKEN = r"Mo|Tu|We|Th|Fr|Sa|Su|PH"
_DAY_SELECTOR = rf"(?:{_DAY_TOKEN})(?:-(?:{_DAY_TOKEN}))?(?:,(?:{_DAY_TOKEN})(?:-(?:{_DAY_TOKEN}))?)*"
_TIME_RANGE = r"\d{2}:\d{2}-\d{2}:\d{2}"
_RULE_RE = re.compile(rf"(?P<days>{_DAY_SELECTOR})\s+(?P<time>{_TIME_RANGE}|off)")

# Everything between recognized rules must be a rule separator. An
# earlier version accepted a string once 60% of it matched, which
# silently read "Mo-Fr 07:00-12:00,13:00-18:00" as closing at noon and
# "Jan-Mar Mo-Fr ..." as year-round (caught by the Day 6 tests).
_SEPARATORS_ONLY_RE = re.compile(r"[\s;,]*")


@dataclass(frozen=True)
class ParsedHours:
    raw: str
    # weekday index -> tuple of (start_minute, end_minute) intervals,
    # both in [0, 1440); an interval never itself crosses midnight —
    # overnight spans are pre-split across the two affected days.
    schedule: dict[int, tuple[tuple[int, int], ...]]


def _parse_hhmm(text: str) -> int:
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


def _expand_days(days_str: str) -> list[int]:
    """'Mo-Th' -> [0,1,2,3]; 'Fr,Sa' -> [4,5]; 'PH' tokens dropped
    (see module docstring)."""
    indices: list[int] = []
    for token in days_str.split(","):
        token = token.strip()
        if token == "PH":
            continue
        if "-" in token:
            start_tok, end_tok = token.split("-")
            start_i, end_i = WEEKDAY_TOKENS.index(start_tok), WEEKDAY_TOKENS.index(end_tok)
            if end_i >= start_i:
                indices.extend(range(start_i, end_i + 1))
            else:
                indices.extend(list(range(start_i, 7)) + list(range(0, end_i + 1)))
        else:
            indices.append(WEEKDAY_TOKENS.index(token))
    return indices


def _build_schedule(rules: dict[int, list[tuple[int, int]]]) -> dict[int, tuple[tuple[int, int], ...]]:
    """Expand each day's own (start, end) rules into intervals that never
    cross midnight: an overnight rule gives its day [start, midnight)
    and the next day [midnight, end) — e.g. Mo 09:00-01:00 means open
    Monday 09:00 through Tuesday 01:00."""
    schedule: dict[int, list[tuple[int, int]]] = {day: [] for day in rules}
    for day, spans in rules.items():
        for start_min, end_min in spans:
            if end_min > start_min:
                schedule[day].append((start_min, end_min))
                continue
            schedule[day].append((start_min, 1440))
            if end_min > 0:  # a 00:00 close has no after-midnight tail
                schedule.setdefault((day + 1) % 7, []).append((0, end_min))
    return {d: tuple(sorted(v)) for d, v in schedule.items()}


def parse_opening_hours(raw: str | None) -> ParsedHours | None:
    """Returns None if `raw` is missing, empty, or falls outside the
    supported syntax subset — callers must treat that as "hours
    unknown", never as "closed"."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if raw == "24/7":
        return ParsedHours(raw=raw, schedule={d: ((0, 1440),) for d in range(7)})

    matches = list(_RULE_RE.finditer(raw))
    if not matches:
        return None
    if not _SEPARATORS_ONLY_RE.fullmatch(_RULE_RE.sub("", raw)):
        return None

    # Each day's own rules, before overnight expansion.
    rules: dict[int, list[tuple[int, int]]] = {}
    prev_end = 0
    for m in matches:
        additive = "," in raw[prev_end:m.start()]
        prev_end = m.end()
        days = _expand_days(m.group("days"))
        if not days:
            continue  # e.g. a PH-only selector — no weekday to attach it to
        time_str = m.group("time")
        for day in days:
            if not additive or day not in rules:
                rules[day] = []
            if time_str != "off":
                start_s, end_s = time_str.split("-")
                rules[day].append((_parse_hhmm(start_s), _parse_hhmm(end_s)))

    return ParsedHours(raw=raw, schedule=_build_schedule(rules))


def is_open_during(parsed: ParsedHours | None, weekday: int, start_hour: int, end_hour: int) -> bool:
    """Whether the POI has any open interval overlapping [start_hour,
    end_hour) on the given weekday (0=Mo..6=Su). A POI with unknown
    hours (parsed is None) is never counted as open — the caller is
    expected to track "hours unknown" as its own, separate count."""
    if parsed is None:
        return False
    window_start, window_end = start_hour * 60, end_hour * 60
    return any(
        iv_start < window_end and iv_end > window_start
        for iv_start, iv_end in parsed.schedule.get(weekday, ())
    )
