"""Ambiguity behaviour (FIXR-018): on thin evidence, request a specific additional check.

When a triage request arrives with evidence too thin to support a hypothesis, the pipeline
must not guess a cause. Instead it produces a `ClarifyRequest` — one targeted diagnostic
step whose output would make the evidence actionable.

"Thin" means the evidence is vague enough that any hypothesis would be a fabrication:
short total content, no technical identifiers (error codes, paths, numeric measurements,
service names), no log lines with structured fields.

The check that comes back must be specific to the evidence present. "Please provide more
information" fails the acceptance criterion. "Run `df -h` on prod-3 and share the output"
passes it, because it names the right tool for the right host.

Acceptance criterion:
  request-more-evidence path works on dev   is_thin() returns True for the vague dev cases
  the requested check is the right one      request_check() cites context from the evidence

Usage::

    from src.clarify import is_thin, request_check

    records = ingest_log(raw_text)
    if is_thin(records):
        cr = request_check(records, turn_id=turn_id)
        report = DiagnosticReport(observed_evidence=records, clarify_request=cr)
    else:
        # proceed to hypothesis generation
        ...
"""
from __future__ import annotations

import json
import re

from schemas.diagnostic import ClarifyRequest
from schemas.evidence import EvidenceRecord
from src.config import PROMPTS_DIR

# ---------------------------------------------------------------------------
# Thin-evidence detection
# ---------------------------------------------------------------------------

# Evidence whose total word count (across all records) falls below this is a
# candidate for thinness — short does not always mean vague, so the specificity
# check runs next.
_THIN_WORD_LIMIT = 15

# Patterns that indicate the evidence has enough specificity to attempt diagnosis.
# Any match in the combined evidence content means it is *not* thin.
_SPECIFIC: list[re.Pattern[str]] = [
    re.compile(r"\b\w+Error\b"),                            # OSError, ValueError, RuntimeError, …
    re.compile(r"\bERROR\b|\bFATAL\b|\bCRITICAL\b", re.I),  # log-level keywords
    re.compile(r"/[a-z][a-zA-Z0-9/_.-]{3,}"),             # Unix path (/var/log, /dev/sda1)
    re.compile(r"\b[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b"),  # IP address
    re.compile(r"\b(?:HTTP|http)\s+[45]\d{2}\b"),          # HTTP error status
    re.compile(r"\b0x[0-9a-fA-F]{4,}\b"),                  # hex code
    re.compile(r"\b\d+\s*(?:MB|GB|KB|%|ms|s)\b", re.I),   # numeric measurement with unit
    re.compile(r"\bexit(?:ed)?\s+(?:code\s+)?\d+\b", re.I),  # exit code
    re.compile(r"\b[a-z][a-z0-9-]{2,}\.[a-z][a-z0-9-]{1,}\b"),  # service.component or host.domain
]


def is_thin(records: list[EvidenceRecord]) -> bool:
    """Return True when the evidence is too vague to support a hypothesis.

    A record set is thin when *both* conditions hold:
      1. total word count across all content is below _THIN_WORD_LIMIT, AND
      2. none of the specificity patterns match anywhere in the combined content.

    The AND condition means rich detail in a long record is never called thin,
    and a one-word technical identifier in three words is not thin either.
    """
    if not records:
        return True
    combined = " ".join(r.content for r in records)
    word_count = len(combined.split())
    if word_count >= _THIN_WORD_LIMIT:
        return False
    return not any(pat.search(combined) for pat in _SPECIFIC)


# ---------------------------------------------------------------------------
# Check generation
# ---------------------------------------------------------------------------

_CLARIFY_PROMPT_PATH = PROMPTS_DIR / "clarify_check_v1.md"
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

_MAX_EVIDENCE_CHARS = 600   # cap sent to the LLM; long logs are already not thin


def _system_prompt() -> str:
    """The versioned clarify_check prompt, front matter stripped."""
    raw = _CLARIFY_PROMPT_PATH.read_text(encoding="utf-8")
    return _FRONT_MATTER.sub("", raw).strip()


def _evidence_summary(records: list[EvidenceRecord]) -> str:
    """A compact representation of the evidence for the LLM user message."""
    lines = []
    for r in records:
        lines.append(f"[{r.id}] ({r.kind}) {r.content}")
    return "\n".join(lines)[:_MAX_EVIDENCE_CHARS]


def request_check(records: list[EvidenceRecord], *, turn_id: str,
                  model_id: str | None = None) -> ClarifyRequest:
    """Ask the LLM for the one specific additional check that would make this evidence actionable.

    The LLM receives the thin evidence and the clarify_check_v1 system prompt. It returns JSON
    with `reason` and `check`. Both fields are required; a missing or empty field raises.

    Args:
        records:   the thin evidence records from ingestion.
        turn_id:   telemetry join key.
        model_id:  HF repo id of the LLM arm, or None for the default.

    Returns:
        A `ClarifyRequest` whose `check` is specific to the evidence, not generic.

    Raises:
        ValueError: if the LLM response is not valid JSON or is missing required fields.
        RuntimeError: propagated from arms.llm if the model call fails.
    """
    from src import arms   # late import: arms imports nlu which imports this indirectly

    msgs = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _evidence_summary(records)},
    ]
    raw = arms.llm(msgs, model_id, turn_id=turn_id, json_mode=True,
                   max_tokens=120, temperature=0.0)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"clarify_check LLM returned non-JSON: {raw!r}") from exc

    reason = (parsed.get("reason") or "").strip()
    check = (parsed.get("check") or "").strip()
    if not reason or not check:
        raise ValueError(
            f"clarify_check LLM response missing 'reason' or 'check': {parsed!r}"
        )

    return ClarifyRequest(
        reason=reason,
        check=check,
        evidence_ids=[r.id for r in records],
    )
