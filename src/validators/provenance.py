"""Provenance validator (FIXR-024): a dangling evidence id is a defect.

A dangling eid is one that appears in a model response but was never minted
in the current request's evidence payload. It means the model cited a source
that does not exist — not a hallucination to tolerate, a defect to surface.

Usage::

    from src.validators.provenance import validate_provenance, DanglingEidError

    minted = {r.id for r in records}          # eids produced by ingestion
    cited  = response.get("evidence_ids", []) # eids the model cited

    validate_provenance(cited, minted)         # raises DanglingEidError if any dangle
"""
from __future__ import annotations


class DanglingEidError(ValueError):
    """Raised when a response cites an eid that was never minted in this request."""

    def __init__(self, dangling: set[str], cited: list[str], minted: set[str]):
        self.dangling = dangling
        self.cited = cited
        self.minted = minted
        names = ", ".join(sorted(dangling))
        super().__init__(
            f"Dangling evidence id(s): {names}. "
            f"Cited {sorted(cited)}, minted {sorted(minted)}. "
            f"A cited eid that was never minted is a defect, not a hallucination."
        )


def validate_provenance(cited: list[str], minted: set[str]) -> None:
    """Assert that every cited eid was minted in this request.

    Args:
        cited:  the evidence_ids list from the model response.
        minted: the set of eids produced by ingestion for this request.

    Raises:
        DanglingEidError: if any cited eid is not in `minted`.

    Notes:
        - An empty `cited` list passes (the model cited nothing — a separate
          concern from provenance; handled by the gate's own checks).
        - Duplicate cited eids are deduplicated before checking — citing the
          same eid twice is not a provenance violation.
    """
    dangling = set(cited) - minted
    if dangling:
        raise DanglingEidError(dangling, cited, minted)


def check_provenance(cited: list[str], minted: set[str]) -> list[str]:
    """Non-raising form: return the list of dangling eids (empty = clean).

    Use this when you want to collect violations rather than stop on the first.
    """
    return sorted(set(cited) - minted)
