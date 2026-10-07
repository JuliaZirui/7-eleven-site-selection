"""Leakage guard for the data-plausibility audit (plan_llm_data_audit.md §6,
Amendment 2).

Fails if anything the auditor is given as instructions -- the system
prompt and few-shot examples (src/audit_llm.py, Phase 2) and the
non-food word list (src/audit_candidates.py) -- contains something that
identifies an answer-key item:

  - terms (substring match): full entity names, record ids and raw values
    from the key's item rows, and the names those records resolve to;
  - words (whole-word match, up to 2 inflection letters): every word of
    >= 4 letters in those names ("steakhouse", "maredo", "zollernhof",
    "berghain", ...), minus ALLOWED_WORDS.

Entity names come from the key itself and from the records it points to:
POI refs -> their names in db/site_selection.db (read only); IHK ids ->
identified_as in worker_onsite_verified.csv; D4 and H2 -> the node's rows
in worker_onsite_unregistered.csv. A new key item is covered without
editing this test. The prompt may describe issue *types* only."""

from __future__ import annotations

import csv
import importlib
import importlib.util
import json
import re
import sqlite3
import unittest

import helpers  # noqa: F401  (sets sys.path for src/)

import audit_candidates
import audit_eval

PROJECT_ROOT = helpers.PROJECT_ROOT
CALIBRATION = PROJECT_ROOT / "db" / "raw" / "calibration"
DB = PROJECT_ROOT / "db" / "site_selection.db"

# Too generic to identify an item as a substring term.
_IGNORED_TERMS = {"cafe", "bakery", "overture", "public-sector"}
# Words of the entity names the instructions must be able to use: the
# setting, the issue-type vocabulary of the pre-registered plan
# ("company-wide headcount", "office building", "public sector",
# "registered elsewhere"), street-name parts, legal forms, and filler from
# the tables' annotations. Nothing here names a place.
ALLOWED_WORDS = frozenset(
    """berlin germany company office offices building public registered café gmbh straße platz
    year main likely incl unnamed unidentified""".split()
)
# Nodes whose hand-added employers are answer-key items (D4, H2).
_UNREGISTERED_ITEM_NODES = ("warschauer_strasse", "brandenburger_tor")


def _item_rows() -> list[list[str]]:
    rows = []
    for line in audit_eval.ANSWER_KEY.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\| [DH]\d", line):
            rows.append([c.strip() for c in line.split("|")[1:-1]])
    return rows


def entity_names() -> set[str]:
    """Every name an answer-key item goes by."""
    names: set[str] = set()
    refs: set[str] = set()
    for cells in _item_rows():
        record = cells[1]
        for tok in re.findall(r"`([^`]+)`", record):
            refs.add(tok)
            if not re.fullmatch(r"[\w:/.-]*\d[\w:/.-]*", tok):  # a title, not an id
                names.add(tok)
        names.update(re.findall(r"[A-ZÄÖÜ][\wäöüß]+(?:-[A-ZÄÖÜ]?[\wäöüß]+)+(?: \d+)?", record))
    with (CALIBRATION / "worker_onsite_verified.csv").open(encoding="utf-8") as f:
        names.update(r["identified_as"] for r in csv.DictReader(f) if r["opendata_id"] in refs)
    with (CALIBRATION / "worker_onsite_unregistered.csv").open(encoding="utf-8") as f:
        names.update(r["name"] for r in csv.DictReader(f) if r["area_id"] in _UNREGISTERED_ITEM_NODES)
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        for ref in refs:
            names.update(r[0] for r in conn.execute(
                "SELECT DISTINCT name FROM observations WHERE external_ref = ? AND name IS NOT NULL", (ref,)))
    finally:
        conn.close()
    return names


def answer_key_terms() -> set[str]:
    """Substring terms: ids, full names, raw values (lower-cased)."""
    terms: set[str] = set()
    for cells in _item_rows():
        record, raw_value = cells[1], cells[3]
        for tok in re.findall(r"`([^`]+)`", record):
            terms.add(tok.lower())
            terms.update(t.lower() for t in re.split(r"[:\s]", tok) if len(t) >= 4)
        for num in re.findall(r"\d[\d,.]*", raw_value):
            if len(num.replace(",", "").replace(".", "")) >= 3:
                terms.add(num)
    terms.update(n.lower() for n in entity_names())
    return terms - _IGNORED_TERMS


def answer_key_words() -> set[str]:
    """Whole-word terms: words of >= 4 letters in the entity names."""
    words = {w for n in entity_names() for w in re.findall(r"[^\W\d_]{4,}", n.lower())}
    return words - ALLOWED_WORDS


def leaks(text: str, terms: set[str], words: set[str]) -> list[str]:
    lowered = text.lower()
    tokens = set(re.findall(r"[^\W\d_]+", lowered))
    found = {t for t in terms if t in lowered}
    found |= {w for w in words for tok in tokens if tok.startswith(w) and len(tok) - len(w) <= 2}
    return sorted(found)


def auditor_inputs() -> dict[str, str]:
    """Everything that becomes part of the auditor's instructions. From
    Phase 2, src/audit_llm.py must expose SYSTEM_PROMPT and
    FEW_SHOT_EXAMPLES (checked by test_audit_llm_exposes_its_instructions)."""
    inputs = {"NON_FOOD_NAME_WORDS": " ".join(sorted(audit_candidates.NON_FOOD_NAME_WORDS))}
    if importlib.util.find_spec("audit_llm") is not None:
        llm = importlib.import_module("audit_llm")
        inputs["SYSTEM_PROMPT"] = llm.SYSTEM_PROMPT
        inputs["FEW_SHOT_EXAMPLES"] = json.dumps(llm.FEW_SHOT_EXAMPLES, ensure_ascii=False)
    return inputs


class BlocklistTest(unittest.TestCase):
    def test_key_terms_and_words_are_read(self):
        terms, words = answer_key_terms(), answer_key_words()
        for t in ("berghain", "17303106124", "tamara-danz-straße 13", "6,123", "zollernhof"):
            self.assertIn(t, terms)
        for w in ("steakhouse", "maredo", "zollernhof", "berghain", "bank", "tower", "haus", "embassy"):
            self.assertIn(w, words)

    def test_word_match_is_whole_word(self):
        words = {"edge", "side", "steakhouse"}
        self.assertEqual(leaks("general knowledge, outside the circle", set(), words), [])
        self.assertEqual(leaks("Two STEAKHOUSES and an edge case", set(), words), ["edge", "steakhouse"])

    def test_audit_llm_exposes_its_instructions(self):
        if importlib.util.find_spec("audit_llm") is None:
            self.skipTest("src/audit_llm.py not written yet (Phase 2)")
        self.assertIn("SYSTEM_PROMPT", auditor_inputs())

    def test_no_answer_key_term_in_auditor_inputs(self):
        terms, words = answer_key_terms(), answer_key_words()
        for name, text in auditor_inputs().items():
            self.assertEqual(leaks(text, terms, words), [], f"{name} contains answer-key terms")


if __name__ == "__main__":
    unittest.main()
