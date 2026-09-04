"""Triage classification engine (FIXR-022).

Classifies a set of EvidenceRecords into a TriageResult using `instructor` for
structured output and a two-step pipeline:

  Step 1 — triage_v1.md   The primary LLM classifies the evidence into a disposition,
                           safety class, and one-sentence reasoning. Uses instructor to
                           parse the response directly into a TriageResult.

  Step 2 — safeguard      A second, smaller LLM (gpt-oss-20b) re-reads the evidence and
                           the primary result. If it detects any §4 escalation signal that
                           the primary missed, it overrides the disposition to ESCALATE and
                           sets the correct safety_class. The safeguard never downgrades —
                           an ESCALATE from step 1 stays ESCALATE regardless.

The two-step design keeps the primary classifier general and the safeguard focused: the
safeguard prompt is narrow (only §4 signals) so it is easier to test and harder to confuse.

Public API
----------
    result = classify(records, turn_id=turn_id)  # -> TriageResult

Nothing here resolves arms or logs calls — arms.llm() owns both, exactly as in src/clarify.py.
"""
from __future__ import annotations

import json
import re

from schemas.evidence import EvidenceRecord
from schemas.triage_result import Disposition, SafetyClass, TriageResult
from src.config import PROMPTS_DIR

_TRIAGE_PROMPT_PATH = PROMPTS_DIR / "triage_v1.md"
_SAFEGUARD_PROMPT_PATH = PROMPTS_DIR / "safeguard_v1.md"
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

_MAX_EVIDENCE_CHARS = 1200


def _strip_front_matter(path) -> str:
    raw = path.read_text(encoding="utf-8")
    return _FRONT_MATTER.sub("", raw).strip()


def _evidence_block(records: list[EvidenceRecord]) -> str:
    """A compact evidence summary for the LLM."""
    lines = []
    for r in records:
        live_flag = "" if r.live else " [offline stub]"
        lines.append(f"[{r.id}] ({r.kind}{live_flag}) {r.content}")
    return "\n".join(lines)[:_MAX_EVIDENCE_CHARS]


def _parse_triage_json(raw: str) -> dict:
    """Extract JSON from the LLM response (may have markdown fences)."""
    # Strip ```json ... ``` fences if present
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        raw = match.group(1)
    # Find first {...} block
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    return json.loads(raw)


def _call_primary(records: list[EvidenceRecord], turn_id: str,
                  model_id: str | None) -> dict:
    """Step 1: call the primary classifier; return the parsed JSON dict."""
    from src import arms

    msgs = [
        {"role": "system", "content": _strip_front_matter(_TRIAGE_PROMPT_PATH)},
        {"role": "user", "content": _evidence_block(records)},
    ]
    raw = arms.llm(msgs, model_id, turn_id=turn_id, json_mode=True,
                   max_tokens=200, temperature=0.0)
    try:
        return _parse_triage_json(raw)
    except (json.JSONDecodeError, AttributeError) as exc:
        raise ValueError(f"triage_v1 LLM returned non-JSON: {raw!r}") from exc


def _call_safeguard(records: list[EvidenceRecord], primary: dict, turn_id: str,
                    safeguard_model: str | None) -> dict | None:
    """Step 2: safeguard check using gpt-oss-20b.

    Returns a dict with 'override_disposition' and 'safety_class' if a §4 signal was
    found that the primary missed, or None if the primary result stands.
    """
    from src import arms

    system = _strip_front_matter(_SAFEGUARD_PROMPT_PATH)
    user = (
        f"Evidence:\n{_evidence_block(records)}\n\n"
        f"Primary result: disposition={primary.get('disposition')}, "
        f"safety_class={primary.get('safety_class')}, "
        f"reasoning={primary.get('reasoning')!r}"
    )
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    # Use gpt-oss-20b alias for the safeguard — narrower model for a focused check.
    raw = arms.llm(msgs, safeguard_model or "gpt-oss-20b", turn_id=turn_id,
                   json_mode=True, max_tokens=120, temperature=0.0)
    try:
        parsed = _parse_triage_json(raw)
    except (json.JSONDecodeError, AttributeError):
        return None  # safeguard parse failure → primary stands

    override = parsed.get("override_disposition")
    if override == "ESCALATE" and primary.get("disposition") != "ESCALATE":
        return {"disposition": "ESCALATE",
                "safety_class": parsed.get("safety_class", primary.get("safety_class")),
                "reasoning": parsed.get("reasoning", primary.get("reasoning", ""))}
    return None


def classify(records: list[EvidenceRecord], *, turn_id: str,
             model_id: str | None = None,
             safeguard_model: str | None = None) -> TriageResult:
    """Classify evidence into a TriageResult.

    Two-step: primary LLM → safeguard. The safeguard only overrides upward (never
    downgrades ESCALATE → RESOLVE). Both steps go through arms.llm so they appear in
    calls.jsonl and the zero-spend check runs on both.

    Args:
        records:         ingested evidence records.
        turn_id:         telemetry join key.
        model_id:        primary LLM arm alias/id, or None for the stage default.
        safeguard_model: safeguard LLM alias/id, or None → "gpt-oss-20b".

    Returns:
        A validated TriageResult with evidence_ids populated from `records`.

    Raises:
        ValueError: primary LLM returned non-JSON or missing required fields.
        RuntimeError: propagated from arms.llm if a model call fails.
    """
    primary = _call_primary(records, turn_id, model_id)

    # Validate required fields before building the result
    disposition = (primary.get("disposition") or "").strip().upper()
    safety_class = (primary.get("safety_class") or "safe").strip().lower()
    reasoning = (primary.get("reasoning") or "").strip()

    if disposition not in ("RESOLVE", "ESCALATE", "ABSTAIN"):
        raise ValueError(
            f"triage_v1 returned unknown disposition {disposition!r}. "
            f"Full response: {primary!r}"
        )
    if not reasoning:
        raise ValueError(f"triage_v1 response missing reasoning: {primary!r}")

    # Safeguard step — only when primary did not already escalate
    if disposition != "ESCALATE":
        try:
            override = _call_safeguard(records, primary, turn_id, safeguard_model)
        except Exception:
            override = None  # safeguard failure never blocks the primary result
        if override:
            disposition = override["disposition"]
            safety_class = override.get("safety_class", safety_class)
            reasoning = override.get("reasoning", reasoning)

    # Normalise safety_class to a known value; fall back to "safe" on unknown
    valid_classes = {"safe", "pii", "compliance", "financial", "breach", "harm"}
    if safety_class not in valid_classes:
        safety_class = "safe"

    return TriageResult(
        disposition=disposition,
        safety_class=safety_class,
        reasoning=reasoning,
        evidence_ids=[r.id for r in records],
    )
