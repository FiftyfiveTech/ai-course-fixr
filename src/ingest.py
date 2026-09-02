"""The one intake path (FIXR-005): three input kinds in, one evidence record shape out.

A typed note, a spoken report, and a screenshot arrive by different routes and through different
models, but they leave here as the *same* `EvidenceRecord` — a stable id, the text pulled out, and
an honest note of which arm produced it. Everything downstream reads evidence without caring which
of the three it was.

Where the id is minted, and over what, is `schemas/evidence.py`. Where a model call is logged is
`src/arms.py` -> `src/telemetry.py`. This module is only the routing and the one decision those two
do not make: whether the free tier is configured at all, and what to do when it is not.

The offline stub. Two of the three paths call a hosted model — whisper for audio, a VLM for a
screenshot — and a clean clone has no key. Rather than fail (which would make `make demo`
un-runnable without secrets) or fall back to a lesser model (which would be a silent substitution
of the kind arms.py is careful to make loud), the path stands in a *labelled placeholder* when the
credential is absent: the record still gets its real content-addressed id, `live` is False, and
`origin` says `offline-stub`. The demo runs end to end and is honest about which paths were real.
"""
from __future__ import annotations

import os
from pathlib import Path

from schemas.evidence import EvidenceRecord
from src import arms, nlu
from src.config import PROMPTS_DIR, SAMPLE_RATE, resolve

# The versioned instruction the screenshot reader runs. Loaded through nlu.load_prompt so the YAML
# front matter is stripped exactly the way every other prompt in the repo is — never inlined here.
SCREENSHOT_PROMPT = PROMPTS_DIR / "extract_screenshot_v1.md"

# Image container -> the media type the data: URI declares. Extension-keyed, with a magic-byte
# fallback for the two that actually turn up so a mislabelled extension does not lie to the model.
_MEDIA_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}


def _media_type(path: Path, raw: bytes) -> str:
    """-> the image media type for `path`, preferring what the bytes actually are."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return _MEDIA_BY_EXT.get(path.suffix.lower(), "application/octet-stream")


def _ollama_pulled(arm) -> bool:
    """-> True if the ollama daemon is up and `arm`'s model is pulled, so a real read can run.

    Non-raising by design: this decides *whether* to call, so a daemon that is down or a model that
    was never pulled is a reason to stand in the stub, not to crash the run. (arms.warm's
    load_ollama raises on the same conditions because it runs when a turn is already committed to the
    arm; here we are still choosing.)
    """
    import httpx

    root = arm.api_base.rsplit("/v1", 1)[0]
    try:
        r = httpx.get(f"{root}/api/tags", timeout=3)
        r.raise_for_status()
    except Exception:
        return False
    pulled = {m.get("name", "") for m in (r.json().get("models") or [])}
    return arm.provider_model in pulled


def _live(arm) -> bool:
    """-> True if `arm` can actually run right now, so a real call is worth making.

    Three cases, one question — "is the thing this arm needs actually here?":
      a keyed remote arm  needs its credential present (whisper on Groq).
      a local ollama arm  needs the daemon up and the model pulled (the vision VLM).
      an in-process arm   needs nothing external.

    Checked here rather than by catching the error a call would raise: catching would first write a
    failed call record to calls.jsonl for a call we chose not to make, and would not distinguish
    "not configured" (stub) from a genuine failure mid-run (which must surface).
    """
    if arm.key_env:
        return bool(os.environ.get(arm.key_env))
    if arm.provider == "ollama":
        return _ollama_pulled(arm)
    return True


def _stub(kind: str, source: str, raw: bytes, arm) -> str:
    """The labelled placeholder used when `arm` cannot run. Deterministic, and says why."""
    why = (f"{arm.key_env} unset" if arm.key_env
           else f"{arm.provider} unavailable — daemon down or model not pulled")
    return (f"[offline stub] {arm.id} not called ({why}). "
            f"{kind} input {source!r} ({len(raw)} bytes); extracted text unavailable offline.")


def ingest_text(text: str, *, source: str = "text:inline") -> EvidenceRecord:
    """A typed note or a pasted log line -> an evidence record. No model call: the text is the text.

    The id is over the UTF-8 bytes of the text, so the same note pasted twice is one piece of
    evidence. `origin` is `text:verbatim` and `live` is True — nothing was inferred, the content is
    exactly what came in.

    Raises:
        ValueError: if `text` is empty or whitespace-only — an empty log is not observable evidence.
    """
    if not text.strip():
        raise ValueError(
            f"ingest_text: text is empty — an empty log carries no observable evidence "
            f"(source={source!r}). Provide a non-empty note or log excerpt."
        )
    raw = text.encode("utf-8")
    return EvidenceRecord.build("text", raw, content=text, source=source,
                                origin="text:verbatim", live=True)


def _decode_audio(raw_path: Path):
    """-> the float32 mono 16 kHz array arms.stt expects, decoded from the audio file.

    Imported lazily: soundfile and torchaudio are heavy, and the text path must not pay for them.
    Mirrors the decode in tests/fixtures/README.md so `make demo` and that snippet hear the same
    thing.
    """
    import soundfile as sf
    import torch
    import torchaudio

    audio, sr = sf.read(str(raw_path), dtype="float32", always_2d=True)
    mono = torch.from_numpy(audio.mean(axis=1))
    return torchaudio.functional.resample(mono, sr, SAMPLE_RATE).numpy()


def ingest_audio(path, *, turn_id, model_id=None) -> EvidenceRecord:
    """A spoken report -> an evidence record, transcribed by the STT arm (whisper-large-v3-turbo).

    The id is over the RAW audio file bytes, so re-transcribing the same recording — which whisper
    does not do bit-for-bit — is still one piece of evidence. When GROQ_API_KEY is absent the
    transcript is the offline stub and `live` is False; the id is unchanged either way.

    Raises:
        ValueError: if the STT arm returns an empty transcript — silent or noise-only audio is
            not evidence. The caller must provide audio that contains speech.
    """
    path = Path(path)
    raw = path.read_bytes()
    arm = resolve("stt", model_id)
    if _live(arm):
        transcript = arms.stt(_decode_audio(path), model_id, turn_id=turn_id)
        if not transcript.strip():
            raise ValueError(
                f"ingest_audio: {arm.id} returned an empty transcript for {str(path)!r}. "
                f"The file may be silent, too short, or contain only noise. "
                f"Provide audio that contains speech."
            )
        return EvidenceRecord.build("audio", raw, content=transcript, source=str(path),
                                    origin=arm.id, live=True)
    return EvidenceRecord.build("audio", raw, content=_stub("audio", str(path), raw, arm),
                                source=str(path), origin="offline-stub", live=False)


def ingest_screenshot(path, *, turn_id, model_id=None) -> EvidenceRecord:
    """A screenshot -> an evidence record, read by the vision arm (Nemotron-Nano-VL).

    The id is over the RAW image file bytes. When NVIDIA_API_KEY is absent the content is the
    offline stub and `live` is False; the id is unchanged either way.

    Raises:
        ValueError: if the file's bytes are not a recognisable image format (no magic bytes match
            and no known extension). A corrupt or truncated file must not silently reach the model.
    """
    path = Path(path)
    raw = path.read_bytes()
    media = _media_type(path, raw)
    if media == "application/octet-stream":
        raise ValueError(
            f"ingest_screenshot: {path} is not a recognisable image format "
            f"(first 8 bytes: {raw[:8]!r}). "
            f"Provide a PNG, JPEG, WebP, GIF, or BMP file."
        )
    arm = resolve("vision", model_id)
    if _live(arm):
        prompt = nlu.load_prompt(SCREENSHOT_PROMPT)
        text = arms.vision(raw, media, prompt, model_id, turn_id=turn_id)
        return EvidenceRecord.build("screenshot", raw, content=text, source=str(path),
                                    origin=arm.id, live=True)
    return EvidenceRecord.build("screenshot", raw, content=_stub("screenshot", str(path), raw, arm),
                                source=str(path), origin="offline-stub", live=False)
