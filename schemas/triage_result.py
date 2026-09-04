"""Structured triage output schema (FIXR-022).

`TriageResult` is the validated structured output of one triage run — the LLM's
classification of the incident evidence into a disposition and a safety class.

Fields are separated from `DiagnosticReport` deliberately: triage classification
(RESOLVE/ESCALATE/ABSTAIN) is a different level of inference than a diagnostic hypothesis.
A triage result answers "what should the system do?"; a hypothesis answers "what caused it?".
The two may coexist in a full incident response, but they are different types so neither can
accidentally carry the other's fields.

`extra="forbid"` on both classes means an LLM that adds an invented field (confidence on a
TriageResult, disposition on a Hypothesis) is caught at construction time, not at report time.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Disposition = Literal["RESOLVE", "ESCALATE", "ABSTAIN"]

SafetyClass = Literal["safe", "pii", "compliance", "financial", "breach", "harm"]

# Severity order for display and tie-breaking. Higher index = higher severity.
SAFETY_SEVERITY: dict[SafetyClass, int] = {
    "safe": 0,
    "pii": 1,
    "financial": 2,
    "compliance": 3,
    "breach": 4,
    "harm": 5,
}


class TriageResult(BaseModel):
    """The structured output of one triage classification.

    Fields
    ------
    disposition   : what the system should do — RESOLVE, ESCALATE, or ABSTAIN.
    safety_class  : the highest-severity safety concern in the evidence, or "safe" when none.
    reasoning     : one sentence explaining the primary signal that drove the disposition.
                    Required; the safeguard step reads this to confirm the model cited a real signal.
    evidence_ids  : the ids of the EvidenceRecords this result rests on. Set by the caller from
                    the ingested records — the LLM does not mint ids.
    """

    model_config = ConfigDict(extra="forbid")

    disposition: Disposition
    safety_class: SafetyClass
    reasoning: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
