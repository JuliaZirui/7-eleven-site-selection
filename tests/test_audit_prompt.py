"""Leakage guard for the data-plausibility audit (plan_llm_data_audit.md §6).

Fails if anything the auditor is given as instructions -- the non-food
word list now; the system prompt and few-shot examples once Phase 2 adds
them -- contains an entity name, record id or raw value from
comparison/audit_answer_key.md. The prompt may describe issue *types*
only. Names are read from the key itself, so a new key item is covered
without editing this test."""

from __future__ import annotations

import re
import unittest

import helpers  # noqa: F401  (sets sys.path for src/)

import audit_candidates
import audit_eval

# Too generic to mean an answer-key item on their own.
_IGNORED = {"cafe", "bakery", "overture", "public-sector"}


def answer_key_terms() -> set[str]:
    """Lower-cased names, ids and raw values from the key's item rows:
    every backticked token, every capitalised place name, and the raw
    numbers (e.g. 707, 6,123)."""
    terms: set[str] = set()
    for line in audit_eval.ANSWER_KEY.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\| [DH]\d", line):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        record, raw_value = cells[1], cells[3]
        for tok in re.findall(r"`([^`]+)`", record):
            terms.add(tok.lower())
            terms.update(t.lower() for t in re.split(r"[:\s]", tok) if len(t) >= 4)
        for name in re.findall(r"[A-ZÄÖÜ][\wäöüß]+(?:-[A-ZÄÖÜ]?[\wäöüß]+)+(?: \d+)?", record):
            terms.add(name.lower())
        for num in re.findall(r"\d[\d,.]*", raw_value):
            if len(num.replace(",", "").replace(".", "")) >= 3:
                terms.add(num)
    return terms - _IGNORED


def auditor_inputs() -> dict[str, str]:
    """Everything that becomes part of the auditor's instructions."""
    return {"NON_FOOD_NAME_WORDS": " ".join(sorted(audit_candidates.NON_FOOD_NAME_WORDS))}


class BlocklistTest(unittest.TestCase):
    def test_key_terms_are_read(self):
        terms = answer_key_terms()
        # Spot-check that parsing picks up each kind of term.
        self.assertIn("berghain", terms)
        self.assertIn("17303106124", terms)
        self.assertIn("tamara-danz-straße 13", terms)
        self.assertIn("6,123", terms)

    def test_no_answer_key_term_in_auditor_inputs(self):
        terms = answer_key_terms()
        for name, text in auditor_inputs().items():
            lowered = text.lower()
            leaked = sorted(t for t in terms if t in lowered)
            self.assertEqual(leaked, [], f"{name} contains answer-key terms")


if __name__ == "__main__":
    unittest.main()
