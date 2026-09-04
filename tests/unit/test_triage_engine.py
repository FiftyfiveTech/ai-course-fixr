"""FIXR-022: triage engine — schema validation and safeguard override.

Tests the TriageResult schema and triage_engine.classify() with stubbed arms.llm.
No live LLM calls.
"""
import json

import pytest
from pydantic import ValidationError

from schemas.triage_result import TriageResult
from src import triage_engine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(content: str):
    from schemas.evidence import EvidenceRecord
    return EvidenceRecord.build(
        kind="text", raw=content.encode(), content=content,
        source="test", origin="text:verbatim", live=True,
    )


def _fake_llm(disposition, safety_class, reasoning="test reasoning"):
    payload = json.dumps({"disposition": disposition, "safety_class": safety_class,
                          "reasoning": reasoning})
    def fake(msgs, model_id=None, *, turn_id, **kwargs):
        return payload
    return fake


# ---------------------------------------------------------------------------
# TriageResult schema
# ---------------------------------------------------------------------------

def test_triage_result_valid():
    r = TriageResult(disposition="ESCALATE", safety_class="pii",
                     reasoning="PII visible", evidence_ids=["ev-txt-abc"])
    assert r.disposition == "ESCALATE"
    assert r.safety_class == "pii"


def test_triage_result_rejects_unknown_disposition():
    with pytest.raises(ValidationError):
        TriageResult(disposition="UNSURE", safety_class="safe", reasoning="x")


def test_triage_result_rejects_empty_reasoning():
    with pytest.raises(ValidationError):
        TriageResult(disposition="RESOLVE", safety_class="safe", reasoning="")


def test_triage_result_rejects_extra_fields():
    with pytest.raises(ValidationError):
        TriageResult(disposition="RESOLVE", safety_class="safe", reasoning="ok",
                     confidence=0.9)


def test_triage_result_all_dispositions():
    for d in ("RESOLVE", "ESCALATE", "ABSTAIN"):
        r = TriageResult(disposition=d, safety_class="safe", reasoning="ok")
        assert r.disposition == d


def test_triage_result_all_safety_classes():
    for sc in ("safe", "pii", "compliance", "financial", "breach", "harm"):
        r = TriageResult(disposition="RESOLVE", safety_class=sc, reasoning="ok")
        assert r.safety_class == sc


# ---------------------------------------------------------------------------
# classify() — primary path
# ---------------------------------------------------------------------------

def test_classify_returns_triage_result(monkeypatch):
    from src import arms
    monkeypatch.setattr(arms, "llm", _fake_llm("RESOLVE", "safe"))
    records = [_rec("Button click on checkout screen.")]
    result = triage_engine.classify(records, turn_id="t1")
    assert isinstance(result, TriageResult)
    assert result.disposition == "RESOLVE"
    assert result.safety_class == "safe"


def test_classify_escalate_sets_safety_class(monkeypatch):
    from src import arms
    monkeypatch.setattr(arms, "llm", _fake_llm("ESCALATE", "pii",
                                                reasoning="PII visible in form field"))
    records = [_rec("User email shown unredacted.")]
    result = triage_engine.classify(records, turn_id="t1")
    assert result.disposition == "ESCALATE"
    assert result.safety_class == "pii"


def test_classify_evidence_ids_set_from_records(monkeypatch):
    from src import arms
    monkeypatch.setattr(arms, "llm", _fake_llm("ABSTAIN", "safe",
                                                reasoning="missing order ID"))
    records = [_rec("Refund please, no order number.")]
    result = triage_engine.classify(records, turn_id="t1")
    assert result.evidence_ids == [r.id for r in records]


def test_classify_raises_on_non_json(monkeypatch):
    from src import arms
    monkeypatch.setattr(arms, "llm", lambda *a, **k: "not json")
    with pytest.raises(ValueError, match="non-JSON"):
        triage_engine.classify([_rec("something")], turn_id="t1")


def test_classify_raises_on_unknown_disposition(monkeypatch):
    from src import arms
    monkeypatch.setattr(arms, "llm", lambda *a, **k: json.dumps(
        {"disposition": "MAYBE", "safety_class": "safe", "reasoning": "hmm"}))
    with pytest.raises(ValueError, match="unknown disposition"):
        triage_engine.classify([_rec("something")], turn_id="t1")


# ---------------------------------------------------------------------------
# safeguard override
# ---------------------------------------------------------------------------

def test_safeguard_upgrades_resolve_to_escalate(monkeypatch):
    """Primary says RESOLVE; safeguard detects PII → should override to ESCALATE."""
    from src import arms
    call_count = {"n": 0}

    def fake_llm(msgs, model_id=None, *, turn_id, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Primary: RESOLVE
            return json.dumps({"disposition": "RESOLVE", "safety_class": "safe",
                               "reasoning": "looks fine"})
        # Safeguard: override
        return json.dumps({"override_disposition": "ESCALATE", "safety_class": "pii",
                           "reasoning": "PII found — §4 override."})

    monkeypatch.setattr(arms, "llm", fake_llm)
    records = [_rec("user@example.com visible in form")]
    result = triage_engine.classify(records, turn_id="t1")
    assert result.disposition == "ESCALATE"
    assert result.safety_class == "pii"
    assert call_count["n"] == 2  # both calls happened


def test_safeguard_does_not_downgrade_escalate(monkeypatch):
    """Primary says ESCALATE; safeguard null override → stays ESCALATE."""
    from src import arms
    call_count = {"n": 0}

    def fake_llm(msgs, model_id=None, *, turn_id, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return json.dumps({"disposition": "ESCALATE", "safety_class": "breach",
                               "reasoning": "data breach signal"})
        return json.dumps({"override_disposition": None, "safety_class": "breach",
                           "reasoning": "primary correct"})

    monkeypatch.setattr(arms, "llm", fake_llm)
    records = [_rec("account hacked, all data exposed")]
    result = triage_engine.classify(records, turn_id="t1")
    assert result.disposition == "ESCALATE"
    assert result.safety_class == "breach"


def test_safeguard_failure_does_not_block_primary(monkeypatch):
    """Safeguard parse failure → primary result stands."""
    from src import arms
    call_count = {"n": 0}

    def fake_llm(msgs, model_id=None, *, turn_id, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return json.dumps({"disposition": "RESOLVE", "safety_class": "safe",
                               "reasoning": "clear case"})
        return "safeguard broke"  # non-JSON

    monkeypatch.setattr(arms, "llm", fake_llm)
    records = [_rec("standard support request")]
    result = triage_engine.classify(records, turn_id="t1")
    assert result.disposition == "RESOLVE"  # primary stands
