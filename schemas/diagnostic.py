"""The diagnostic report schema (FIXR-021).

This is the type where the evidence/hypothesis boundary is made structural. An
`EvidenceRecord` (schemas/evidence.py) is a *fact* — what a note said, what the
audio transcribed to, what the screenshot read as — and by construction it
carries no confidence. A `Hypothesis` is an *inference* about cause, so it is
the one place a confidence number is allowed to live, and it must name the
evidence ids it rests on.

`DiagnosticReport` holds the two in separate lists. Keeping observed facts and
inferred causes in different types (not two flavours of one "finding") is what
stops a later prompt from quietly attaching a made-up confidence to something
that was merely observed: there is nowhere on an `EvidenceRecord` to put it, and
`extra="forbid"` turns the attempt into a construction error rather than a
value that rounds to something.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from schemas.evidence import EvidenceRecord


class Hypothesis(BaseModel):
    """A candidate cause. An inference, so — unlike an EvidenceRecord — it
    carries a confidence, and it must cite the evidence ids it rests on.

    Fields
    ------
    statement    : the proposed cause, in one line.
    confidence   : 0.0–1.0. How much the cited evidence supports the statement.
                   The bound is enforced; a value outside it is a construction
                   error, not a clamp.
    supported_by : the ids of the EvidenceRecords this hypothesis rests on. A
                   hypothesis with an empty list is a guess with no evidence —
                   allowed by the type, but visible as such.
    """

    model_config = ConfigDict(extra="forbid")

    statement: str
    confidence: float = Field(ge=0.0, le=1.0)
    supported_by: list[str] = Field(default_factory=list)


class DiagnosticReport(BaseModel):
    """The triage output: observed facts kept structurally apart from the
    hypotheses inferred from them."""

    model_config = ConfigDict(extra="forbid")

    observed_evidence: list[EvidenceRecord] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
