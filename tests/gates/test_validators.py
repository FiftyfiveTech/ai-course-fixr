"""FIXR-024: provenance validator + PII check gate tests.

Done when: both assertions live here and each FAILS on a planted violation.

Two validators, two self-tests:
  1. Provenance: a dangling eid (cited but never minted) raises DanglingEidError.
  2. PII:        a caption containing a credit card / SSN / email raises PiiDetectedError.

Each section follows the same pattern as test_no_leakage.py:
  - Happy path: clean input passes.
  - Planted violation: bad input must be caught (proves the check works).
"""
import pytest

from src.validators.provenance import (
    DanglingEidError,
    check_provenance,
    validate_provenance,
)
from src.validators.pii import PiiDetectedError, check_pii, scan_pii


# ===========================================================================
# Provenance validator
# ===========================================================================

class TestProvenanceHappyPath:

    def test_all_cited_eids_minted(self):
        minted = {"ev-txt-aabbcc112233", "ev-aud-ddeeff445566"}
        cited  = ["ev-txt-aabbcc112233"]
        validate_provenance(cited, minted)  # must not raise

    def test_empty_cited_passes(self):
        minted = {"ev-txt-aabbcc112233"}
        validate_provenance([], minted)

    def test_empty_both_passes(self):
        validate_provenance([], set())

    def test_duplicate_cited_is_not_a_violation(self):
        minted = {"ev-txt-aabbcc112233"}
        cited  = ["ev-txt-aabbcc112233", "ev-txt-aabbcc112233"]
        validate_provenance(cited, minted)

    def test_check_provenance_returns_empty_on_clean(self):
        minted = {"ev-img-001122334455"}
        assert check_provenance(["ev-img-001122334455"], minted) == []


class TestProvenancePlantedViolation:
    """These tests prove the validator catches real defects — if they fail, the check is broken."""

    def test_dangling_eid_raises(self):
        """Planted: one cited eid was never minted."""
        minted = {"ev-txt-aabbcc112233"}
        cited  = ["ev-txt-aabbcc112233", "ev-img-DANGLING0000"]
        with pytest.raises(DanglingEidError) as exc_info:
            validate_provenance(cited, minted)
        assert "ev-img-DANGLING0000" in str(exc_info.value)

    def test_all_cited_dangling_raises(self):
        """Planted: no cited eid was minted — fully ungrounded response."""
        minted = {"ev-txt-aabbcc112233"}
        cited  = ["ev-img-GHOST000001", "ev-aud-GHOST000002"]
        with pytest.raises(DanglingEidError) as exc_info:
            validate_provenance(cited, minted)
        assert len(exc_info.value.dangling) == 2

    def test_check_provenance_returns_dangling(self):
        minted = {"ev-txt-aabbcc112233"}
        result = check_provenance(["ev-txt-aabbcc112233", "ev-img-DANGLING0000"], minted)
        assert result == ["ev-img-DANGLING0000"]

    def test_error_carries_structured_fields(self):
        """DanglingEidError exposes .dangling, .cited, .minted for downstream logging."""
        minted = {"ev-txt-aabbcc112233"}
        cited  = ["ev-img-DANGLING0000"]
        with pytest.raises(DanglingEidError) as exc_info:
            validate_provenance(cited, minted)
        err = exc_info.value
        assert err.dangling == {"ev-img-DANGLING0000"}
        assert err.minted   == minted


# ===========================================================================
# PII validator
# ===========================================================================

class TestPiiHappyPath:

    def test_clean_caption_passes(self):
        check_pii("The user clicked the Submit button on the checkout page.", "clean.png")

    def test_offline_stub_passes(self):
        stub = "[offline stub] arm not called (NVIDIA_API_KEY unset). screenshot input 'x.png' (1024 bytes); extracted text unavailable offline."
        check_pii(stub, "x.png")

    def test_scan_returns_empty_on_clean(self):
        assert scan_pii("No sensitive data here.") == []


class TestPiiPlantedViolation:
    """These tests prove the validator catches real PII — if they fail, the check is broken."""

    def test_credit_card_detected(self):
        """Planted: 16-digit credit card number in caption."""
        caption = "Form field shows card number 4111 1111 1111 1111 in autofill."
        with pytest.raises(PiiDetectedError) as exc_info:
            check_pii(caption, "checkout.png")
        assert "credit_card" in str(exc_info.value)

    def test_ssn_detected(self):
        """Planted: SSN in form field caption."""
        caption = "Profile page displays SSN: 123-45-6789 in the tax section."
        with pytest.raises(PiiDetectedError) as exc_info:
            check_pii(caption, "profile.png")
        assert "ssn" in str(exc_info.value)

    def test_email_detected(self):
        """Planted: unredacted email address in caption."""
        caption = "Notification shows recipient john.doe@example.com in the To field."
        with pytest.raises(PiiDetectedError) as exc_info:
            check_pii(caption, "email_app.png")
        assert "email" in str(exc_info.value)

    def test_scan_returns_all_hits(self):
        """scan_pii returns every match, not just the first."""
        caption = "Email: alice@example.com, Card: 4111 1111 1111 1111"
        hits = scan_pii(caption)
        names = {h[0] for h in hits}
        assert "email" in names
        assert "credit_card" in names

    def test_error_carries_structured_hits(self):
        """PiiDetectedError exposes .hits and .source for downstream logging."""
        caption = "SSN visible: 987-65-4321"
        with pytest.raises(PiiDetectedError) as exc_info:
            check_pii(caption, "ssn_form.png")
        err = exc_info.value
        assert err.source == "ssn_form.png"
        assert any(name == "ssn" for name, _ in err.hits)
