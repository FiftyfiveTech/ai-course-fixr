"""The evidence record (FIXR-005).

One intake path turns three input kinds — a typed note, a spoken report, a screenshot — into the
*same* record shape, so everything downstream reads evidence without caring how it arrived. This
module is the one place an evidence id is minted, and the one place the record's fields are
declared.

Two rules the type enforces, both load-bearing for the rest of the project:

  the id is stable   `ev-<kind>-<sha256(raw input bytes)[:12]>`. The hash is over the RAW input —
                     the text, the audio file, the image file — and never over the model's output,
                     so re-transcribing the same audio (whisper is not bit-deterministic) or
                     re-reading the same screenshot yields the *same* id. Identical inputs collapse
                     to one record; that is dedupe, not a collision.

  evidence carries no confidence   `extra="forbid"` plus the absence of the field means a
                     `confidence=` on an evidence record is a construction *error*, not a value
                     that rounds to something. Confidence lives on a hypothesis, which is a
                     different type in a different phase (FIXR-021). Keeping the boundary in the
                     type is what stops a later prompt from quietly attaching a made-up number to a
                     thing that was merely observed.
"""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict

# The input kinds this path accepts, and the short prefix each one's id carries so an id says at a
# glance what it is. `screenshot` is imaged, so its prefix is `img`.
Kind = Literal["text", "audio", "screenshot"]
_PREFIX = {"text": "txt", "audio": "aud", "screenshot": "img"}


def mint_id(kind: Kind, raw: bytes) -> str:
    """-> the stable evidence id for `kind` over the raw input bytes. The one mint site.

    A callable rather than a method because the id has to exist *before* the record does — the
    ingest path hashes the bytes it read off disk (or the encoded text) and hands the id in. Hashing
    the raw input, not the extracted text, is the whole reason two runs of the same screenshot are
    one piece of evidence and not two.
    """
    if kind not in _PREFIX:
        raise ValueError(f"unknown evidence kind {kind!r} — expected one of {tuple(_PREFIX)}")
    digest = hashlib.sha256(raw).hexdigest()[:12]
    return f"ev-{_PREFIX[kind]}-{digest}"


class EvidenceRecord(BaseModel):
    """One observed input, normalised. No confidence — see the module docstring.

    Fields
    ------
    id       : the stable content-addressed id (mint_id). Identifies the *source artifact*.
    kind     : text | audio | screenshot.
    source   : where it came from, for a human — an inline label or a file path. Never hashed.
    content  : the text pulled out of the input. Verbatim text for a note, the transcript for
               audio, the model's reading for a screenshot, or a labelled placeholder when the
               extractor ran offline (see `origin`).
    origin   : which arm produced `content` (`<repo id>@<provider>`), or "offline-stub" when the
               free tier was not configured and a deterministic placeholder stood in.
    live     : True when a real model produced `content`; False when `origin` is the offline stub.
               A demo running from a keyless clean clone is honest about which paths actually ran.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: Kind
    source: str
    content: str
    origin: str
    live: bool

    @classmethod
    def build(cls, kind: Kind, raw: bytes, content: str, source: str, origin: str,
              live: bool) -> "EvidenceRecord":
        """Mint the id from `raw` and construct the record. The only intended constructor.

        Keeping `mint_id` behind the constructor means no caller sets `id` by hand, so the id is
        content-addressed by construction rather than by everyone remembering to hash the right
        thing.
        """
        return cls(id=mint_id(kind, raw), kind=kind, source=source, content=content,
                   origin=origin, live=live)
