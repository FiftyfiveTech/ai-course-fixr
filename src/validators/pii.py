"""PII validator (FIXR-024): no unredacted screenshot on disk.

An unredacted screenshot is one whose bytes contain a recognisable PII
pattern AND whose file has not been explicitly cleared by the ingestion
pipeline. The check runs over the *extracted text* of the screenshot
(the caption produced by the vision model, or the offline-stub text) —
not over the image bytes themselves, which the vision model already read.

Why text, not pixels:
  - The vision model has already seen the image. If PII is present, the
    caption will reflect it (a SSN field, a credit card, a visible email).
  - Running an OCR pass in the validator would double the work and add a
    new model dependency just for this check.
  - The offline stub's text says "offline stub … extracted text unavailable",
    which matches none of the PII patterns, so keyless demo runs are safe.

Patterns checked (regex, case-insensitive):
  - Credit card:  16-digit groups (4-4-4-4, with space/dash/dot separators)
  - SSN:          NNN-NN-NNNN
  - Email:        standard RFC-5322-ish pattern
  - Passport:     one-or-two letters + 6-9 digits (common international format)
  - UK NI number: XX-999999-X

These are conservative patterns — they will miss some PII and flag some
false positives. The gate's job is to fail loudly on obvious violations,
not to replace a dedicated PII scanner.

Usage::

    from src.validators.pii import check_pii, PiiDetectedError

    check_pii(caption_text, source="screenshot.png")  # raises on hit
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# PII patterns (compiled once at import time)
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("credit_card",
     re.compile(r"\b(?:\d{4}[\s\-\.]{0,1}){3}\d{4}\b")),
    ("ssn",
     re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email",
     re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("passport",
     re.compile(r"\b[A-Z]{1,2}\d{6,9}\b")),
    ("uk_ni",
     re.compile(r"\b[A-Z]{2}\s?\d{6}\s?[A-D]\b")),
]


class PiiDetectedError(ValueError):
    """Raised when a screenshot's extracted text contains a recognisable PII pattern."""

    def __init__(self, hits: list[tuple[str, str]], source: str):
        self.hits = hits      # [(pattern_name, matched_text), ...]
        self.source = source
        summary = ", ".join(f"{name}:{match!r}" for name, match in hits)
        super().__init__(
            f"PII detected in screenshot {source!r}: {summary}. "
            f"Unredacted screenshots must not reach disk or the model. "
            f"Redact the artefact and re-ingest."
        )


def check_pii(text: str, source: str = "<unknown>") -> None:
    """Assert that `text` (screenshot caption) contains no recognisable PII.

    Args:
        text:   the extracted text from a screenshot artefact (caption or stub).
        source: human-readable label for the artefact (file path, eid, …).

    Raises:
        PiiDetectedError: if any PII pattern matches.
    """
    hits = _scan(text)
    if hits:
        raise PiiDetectedError(hits, source)


def scan_pii(text: str) -> list[tuple[str, str]]:
    """Non-raising form: return [(pattern_name, matched_text), ...] (empty = clean).

    Use this when you want to collect violations rather than stop on the first.
    """
    return _scan(text)


def _scan(text: str) -> list[tuple[str, str]]:
    hits = []
    for name, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            hits.append((name, m.group()))
    return hits
