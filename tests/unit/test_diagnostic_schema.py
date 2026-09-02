"""FIXR-021 acceptance: evidence cannot carry confidence.

The evidence/hypothesis boundary is enforced by the type, not by convention.
These tests prove the confidence field does not exist on the evidence record
and that trying to attach one is rejected — while a Hypothesis carries it.
"""

import pytest
from pydantic import ValidationError

from schemas.diagnostic import DiagnosticReport, Hypothesis
from schemas.evidence import EvidenceRecord


def _evidence() -> EvidenceRecord:
    return EvidenceRecord.build(
        kind="text",
        raw=b"OSError: [Errno 28] No space left on device",
        content="OSError: [Errno 28] No space left on device",
        source="app.log",
        origin="offline-stub",
        live=False,
    )


def test_evidence_type_has_no_confidence_field():
    """The field does not exist on the evidence type (the FIXR-021 gate)."""
    assert "confidence" not in EvidenceRecord.model_fields


def test_evidence_rejects_a_confidence_value():
    """extra='forbid' makes the boundary load-bearing: a confidence handed to
    an evidence record raises, it is not silently dropped."""
    with pytest.raises(ValidationError):
        EvidenceRecord(
            id="ev-txt-1a2b3c4d5e6f",
            kind="text",
            source="app.log",
            content="OSError: [Errno 28] No space left on device",
            origin="offline-stub",
            live=False,
            confidence=0.7,
        )


def test_hypothesis_carries_confidence():
    """The other side of the boundary: an inference is allowed confidence."""
    assert "confidence" in Hypothesis.model_fields
    h = Hypothesis(
        statement="disk full on the log volume",
        confidence=0.7,
        supported_by=["ev-txt-1a2b3c4d5e6f"],
    )
    assert h.confidence == 0.7


def test_hypothesis_confidence_is_bounded_0_to_1():
    with pytest.raises(ValidationError):
        Hypothesis(statement="impossible certainty", confidence=1.7)


def test_report_keeps_evidence_and_hypotheses_separate():
    ev = _evidence()
    report = DiagnosticReport(
        observed_evidence=[ev],
        hypotheses=[
            Hypothesis(
                statement="disk full on the log volume",
                confidence=0.7,
                supported_by=[ev.id],
            )
        ],
    )
    # No confidence anywhere on the evidence side of the serialized report;
    # confidence present on the hypothesis side.
    assert "confidence" not in report.observed_evidence[0].model_dump()
    assert "confidence" in report.hypotheses[0].model_dump()
