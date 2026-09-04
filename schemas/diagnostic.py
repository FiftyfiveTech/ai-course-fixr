"""The diagnostic report schema (FIXR-021, FIXR-018).

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

When the evidence is too thin to produce a hypothesis, `DiagnosticReport` carries
a `ClarifyRequest` instead (FIXR-018). The two are mutually exclusive: a report
with both set is a construction error.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class ClarifyRequest(BaseModel):
    """Returned instead of hypotheses when evidence is too thin to diagnose (FIXR-018).

    The `check` must be specific and actionable — not "please provide more information"
    but "run `df -h` to show current disk usage per partition". A generic ask fails the
    acceptance criterion: the requested check must be the *right* one for the context.

    Fields
    ------
    reason       : why the evidence is insufficient — names the specific gap, not a
                   generic "not enough context".
    check        : the one additional diagnostic step to perform. Imperative, concrete,
                   and tied to the evidence already present.
    evidence_ids : the ids of the EvidenceRecords that prompted this request. Allows the
                   provenance validator to confirm this report is grounded.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    check: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class DiagnosticReport(BaseModel):
    """The triage output: observed facts kept structurally apart from the
    hypotheses inferred from them.

    When evidence is thin, `clarify_request` is set and `hypotheses` is empty.
    The two are mutually exclusive — a report with both is a construction error.
    """

    model_config = ConfigDict(extra="forbid")

    observed_evidence: list[EvidenceRecord] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    clarify_request: ClarifyRequest | None = None

    @model_validator(mode="after")
    def _clarify_xor_hypotheses(self) -> "DiagnosticReport":
        """A clarify_request and non-empty hypotheses cannot coexist in one report."""
        if self.clarify_request is not None and self.hypotheses:
            raise ValueError(
                "clarify_request and hypotheses are mutually exclusive: "
                "set one or the other, not both."
            )
        return self
