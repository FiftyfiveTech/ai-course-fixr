"""FIXR-018: ambiguity behaviour — thin evidence requests a specific check, not a hypothesis.

Two acceptance criteria:

  request-more-evidence path works on dev   is_thin() returns True for vague dev-like inputs
                                            and False for specific technical evidence.

  the requested check is the right one      request_check() produces a ClarifyRequest whose
                                            check field is specific (context-tied), not generic.

The ClarifyRequest/DiagnosticReport schema tests verify the structural constraint:
  clarify_request and non-empty hypotheses are mutually exclusive.

No live model call. request_check() is tested by stubbing arms.llm at the same seam every
other unit test uses, so the real arms path (resolve, log_call, dispatch) runs and only
the HTTP call is replaced.
"""
import json

import pytest
from pydantic import ValidationError

from schemas.diagnostic import ClarifyRequest, DiagnosticReport, Hypothesis
from schemas.evidence import EvidenceRecord
from src import clarify as clarify_mod
from src.clarify import is_thin, request_check


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(content: str, kind="text") -> EvidenceRecord:
    return EvidenceRecord.build(
        kind=kind,
        raw=content.encode("utf-8"),
        content=content,
        source="test",
        origin="log:parsed",
        live=True,
    )


# ---------------------------------------------------------------------------
# ClarifyRequest schema
# ---------------------------------------------------------------------------


def test_clarify_request_requires_reason_and_check():
    with pytest.raises(ValidationError):
        ClarifyRequest(reason="", check="run df -h")        # empty reason
    with pytest.raises(ValidationError):
        ClarifyRequest(reason="too vague", check="")        # empty check


def test_clarify_request_accepts_valid_fields():
    cr = ClarifyRequest(
        reason="No service name or error code in evidence.",
        check="Run `systemctl status app` and share the output.",
        evidence_ids=["ev-txt-aabbccdd0011"],
    )
    assert cr.reason and cr.check
    assert "ev-txt-aabbccdd0011" in cr.evidence_ids


def test_clarify_request_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ClarifyRequest(reason="x", check="y", confidence=0.5)


# ---------------------------------------------------------------------------
# DiagnosticReport: clarify_request XOR hypotheses
# ---------------------------------------------------------------------------


def test_report_with_clarify_request_and_no_hypotheses_is_valid():
    ev = _rec("prod is down")
    report = DiagnosticReport(
        observed_evidence=[ev],
        clarify_request=ClarifyRequest(
            reason="Evidence too vague.",
            check="Run `systemctl status` on the affected host.",
            evidence_ids=[ev.id],
        ),
    )
    assert report.clarify_request is not None
    assert report.hypotheses == []


def test_report_with_hypotheses_and_no_clarify_is_valid():
    ev = _rec("OSError: [Errno 28] No space left on device")
    report = DiagnosticReport(
        observed_evidence=[ev],
        hypotheses=[Hypothesis(statement="disk full on log volume",
                               confidence=0.85, supported_by=[ev.id])],
    )
    assert report.clarify_request is None
    assert len(report.hypotheses) == 1


def test_report_rejects_both_clarify_and_hypotheses():
    ev = _rec("something failed")
    with pytest.raises(ValidationError):
        DiagnosticReport(
            observed_evidence=[ev],
            hypotheses=[Hypothesis(statement="disk full", confidence=0.5)],
            clarify_request=ClarifyRequest(
                reason="Too vague.",
                check="Run df -h.",
                evidence_ids=[ev.id],
            ),
        )


def test_report_with_neither_is_valid():
    """Empty report — no evidence, no hypotheses, no clarify — is allowed (e.g. stub path)."""
    report = DiagnosticReport()
    assert report.hypotheses == [] and report.clarify_request is None


# ---------------------------------------------------------------------------
# is_thin: thin-evidence detection
# ---------------------------------------------------------------------------


def test_empty_records_are_thin():
    assert is_thin([]) is True


def test_single_vague_line_is_thin():
    assert is_thin([_rec("prod is down")]) is True


def test_one_word_is_thin():
    assert is_thin([_rec("help")]) is True


def test_vague_multi_word_under_limit_is_thin():
    assert is_thin([_rec("something went wrong on the server")]) is True


def test_error_class_name_makes_it_not_thin():
    assert is_thin([_rec("OSError: No space left on device")]) is False


def test_unix_path_makes_it_not_thin():
    assert is_thin([_rec("disk full on /var/log")]) is False


def test_http_error_code_makes_it_not_thin():
    assert is_thin([_rec("getting HTTP 500 errors")]) is False


def test_hex_code_makes_it_not_thin():
    assert is_thin([_rec("exit 0xc000007b on startup")]) is False


def test_numeric_measurement_makes_it_not_thin():
    assert is_thin([_rec("/dev/sda1 at 100%")]) is False


def test_long_evidence_is_not_thin_even_without_specifics():
    """Word count alone is enough — a detailed description in plain English is not thin."""
    long_text = "the application has been experiencing intermittent slowness for the past several hours with no clear pattern emerging from user reports across multiple regions"
    assert is_thin([_rec(long_text)]) is False


def test_multiple_short_vague_records_are_thin():
    records = [_rec("prod down"), _rec("help"), _rec("errors")]
    assert is_thin(records) is True


def test_multiple_records_where_one_is_specific_are_not_thin():
    records = [_rec("prod down"), _rec("OSError in /var/log/app.log")]
    assert is_thin(records) is False


# ---------------------------------------------------------------------------
# request_check: LLM call, with stubbed arms.llm
# ---------------------------------------------------------------------------


def _fake_llm_response(reason: str, check: str):
    """Return a fake arms.llm that produces the given JSON."""
    payload = json.dumps({"reason": reason, "check": check})
    def fake_llm(msgs, model_id=None, *, turn_id, **kwargs):
        return payload
    return fake_llm


def test_request_check_returns_clarify_request(monkeypatch):
    from src import arms
    monkeypatch.setattr(arms, "llm", _fake_llm_response(
        reason="No service name or error output in evidence.",
        check="Run `systemctl status app` on the affected host and share the output.",
    ))
    records = [_rec("prod is down")]
    cr = request_check(records, turn_id="t1")
    assert isinstance(cr, ClarifyRequest)


def test_request_check_reason_is_set(monkeypatch):
    from src import arms
    monkeypatch.setattr(arms, "llm", _fake_llm_response(
        reason="No service name or error code in evidence.",
        check="Run `systemctl status` on the affected host.",
    ))
    cr = request_check([_rec("prod is down")], turn_id="t1")
    assert cr.reason


def test_request_check_check_is_specific_not_generic(monkeypatch):
    """The check must not be the exact generic fallback phrase — it must be context-tied."""
    from src import arms
    monkeypatch.setattr(arms, "llm", _fake_llm_response(
        reason="No disk path or usage figure in evidence.",
        check="Run `df -h` on prod-3 to show current disk usage per partition.",
    ))
    cr = request_check([_rec("disk problems on prod-3")], turn_id="t1")
    generic = "please provide more information"
    assert generic.lower() not in cr.check.lower(), "check must not be the generic fallback"
    assert cr.check  # non-empty


def test_request_check_evidence_ids_match_records(monkeypatch):
    from src import arms
    monkeypatch.setattr(arms, "llm", _fake_llm_response(
        reason="Too vague.",
        check="Run `df -h`.",
    ))
    records = [_rec("prod down"), _rec("disk issues")]
    cr = request_check(records, turn_id="t1")
    assert cr.evidence_ids == [r.id for r in records]


def test_request_check_raises_on_non_json_response(monkeypatch):
    from src import arms
    monkeypatch.setattr(arms, "llm", lambda *a, **k: "not json at all")
    with pytest.raises(ValueError, match="non-JSON"):
        request_check([_rec("prod is down")], turn_id="t1")


def test_request_check_raises_on_missing_check_field(monkeypatch):
    from src import arms
    monkeypatch.setattr(arms, "llm", lambda *a, **k: json.dumps({"reason": "vague"}))
    with pytest.raises(ValueError, match="missing"):
        request_check([_rec("prod is down")], turn_id="t1")


def test_request_check_raises_on_missing_reason_field(monkeypatch):
    from src import arms
    monkeypatch.setattr(arms, "llm", lambda *a, **k: json.dumps({"check": "run df -h"}))
    with pytest.raises(ValueError, match="missing"):
        request_check([_rec("prod is down")], turn_id="t1")


# ---------------------------------------------------------------------------
# Integration: thin evidence flows through to ClarifyRequest in a report
# ---------------------------------------------------------------------------


def test_thin_evidence_produces_clarify_report(monkeypatch):
    from src import arms
    monkeypatch.setattr(arms, "llm", _fake_llm_response(
        reason="No specific error output or service name in evidence.",
        check="Run `journalctl -xe --since '10 minutes ago'` and share the output.",
    ))
    records = [_rec("something is broken")]
    assert is_thin(records)
    cr = request_check(records, turn_id="t2")
    report = DiagnosticReport(observed_evidence=records, clarify_request=cr)
    assert report.clarify_request is not None
    assert not report.hypotheses
