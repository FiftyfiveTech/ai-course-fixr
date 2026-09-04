"""Screenshot-to-text backends. One adapter per way of running a document-VLM; the arms are in
config.py.

Nothing here resolves an arm, logs a call, or knows which arm is the default — `src/arms.py` owns
all three, exactly as it does for `stt` and `llm`. A backend takes the arm it was told to run, the
payload, and the mutable call record it adds its measured facts to.

Two backends:

  openai-vision   OpenAI-compatible /chat/completions with an inline base64 image — serves the
                  local ollama VLM (Qwen2.5-VL) with no auth header, the same wire protocol the LLM
                  stage already speaks.

  got-ocr2        In-process transformers inference for stepfun-ai/GOT-OCR2_0. OCR-focused rather
                  than generalist-VLM; takes the raw screenshot bytes and returns the extracted text.
                  The prompt is ignored — GOT-OCR2 always performs plain text OCR; there is no
                  system-prompt slot. Weights are loaded once via the LOADERS entry and kept in the
                  module-level cache (_GOT_CACHE) across turns so the load cost is paid at warm()
                  time, not inside a timed call.
"""
import base64
import io

import httpx

from src import errors

# A screenshot read is a transcription task, not a conversation, so it gets a wider ceiling than a
# spoken reply: a busy error dialog or a full terminal is easily a few hundred tokens of text, and a
# reply cut off at nlu.MAX_TOKENS would drop exactly the identifier the incident turns on.
MAX_TOKENS = 1024

# Deterministic on purpose. Reading what is on a screen is not a creative task, and two runs of the
# same screenshot should say the same thing — the id already dedupes the input, and a wandering
# transcript would make the *content* disagree where the id says it is the same evidence.
TEMPERATURE = 0.0


def _data_uri(image: bytes, media_type: str) -> str:
    """-> a base64 `data:` URI for the raw image bytes. What an image_url content part expects."""
    return f"data:{media_type};base64,{base64.b64encode(image).decode('ascii')}"


def openai_vision(arm, payload, rec, timeout=None, temperature=None, max_tokens=None):
    """OpenAI-compatible /chat/completions with an inline image — serves the local ollama VLM here.

    `payload` is `{"image": bytes, "media_type": str, "prompt": str}`: the raw screenshot, its type
    so the data URI is honest about PNG vs JPEG, and the instruction that tells the model to
    transcribe rather than describe. The instruction is a versioned prompt file, passed in by the
    caller — no prompt is inlined here, the same rule the LLM path follows.
    """
    body = {
        "model": arm.provider_model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": payload["prompt"]},
                {"type": "image_url",
                 "image_url": {"url": _data_uri(payload["image"], payload["media_type"])}},
            ],
        }],
        "temperature": TEMPERATURE if temperature is None else temperature,
        "max_tokens": MAX_TOKENS if max_tokens is None else max_tokens,
    }
    for k, v in (arm.extra.get("request") or {}).items():
        body.setdefault(k, v)

    r = httpx.post(
        f"{arm.api_base}/chat/completions",
        headers=arm.auth_headers({"Content-Type": "application/json"}),
        json=body,
        timeout=arm.timeout_s if timeout is None else timeout,
    )
    errors.check(r, arm, rec)
    resp = r.json()
    choice = resp["choices"][0]
    text = (choice["message"].get("content") or "").strip()
    usage = resp.get("usage") or {}
    # `image_bytes` is stamped on the record by arms.vision before dispatch, so it is present even
    # when this call fails; here we add only what the response tells us.
    rec["prompt_tokens"] = usage.get("prompt_tokens")
    rec["completion_tokens"] = usage.get("completion_tokens")
    rec["read_chars"] = len(text)
    rec["finish_reason"] = choice.get("finish_reason")

    if not text:
        # A VLM that returns nothing on a screenshot that plainly has text on it is a failed read,
        # not empty evidence. Raise where the arm and finish_reason are still in hand, rather than
        # letting a blank content string travel downstream as if the screen were blank.
        raise RuntimeError(
            f"{arm.id} read no text from the screenshot "
            f"(finish_reason={choice.get('finish_reason')!r}, "
            f"{usage.get('completion_tokens')} completion tokens of {MAX_TOKENS})."
        )
    return text


BACKENDS = {"openai-vision": openai_vision}


# ---------------------------------------------------------------------------
# GOT-OCR2 backend (FIXR-016): in-process transformers inference
# ---------------------------------------------------------------------------

# Module-level cache so the model and processor survive across turns. Keys are the
# arm's repo_id so a second arm on the same weights (different quantisation, say)
# gets a separate entry rather than the wrong processor.
_GOT_CACHE: dict[str, tuple] = {}   # repo_id -> (model, processor)


def _load_got_ocr2(arm):
    """Download (if needed) and cache the GOT-OCR2 model + tokenizer.

    Called by arms.warm() before anything is timed; the model weighs ~1.4 GB and
    the first call will block while the HF hub fetches it. Subsequent calls are a
    dict lookup.

    `stepfun-ai/GOT-OCR2_0` ships its own model class (`modeling_GOT.py`) and tokenizer
    (`tokenization_qwen.py`). `trust_remote_code=True` is required; the native transformers
    `GotOcr2ForConditionalGeneration` has a different weight layout and cannot load this
    checkpoint. `device_map` is absent — it requires `accelerate` which is not in the
    project deps; the model loads to CPU and moves to GPU with `.cuda()` when available.
    """
    if arm.repo_id in _GOT_CACHE:
        return
    import torch
    from transformers import AutoModel, AutoTokenizer

    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    tokenizer = AutoTokenizer.from_pretrained(
        arm.repo_id, trust_remote_code=True, use_fast=False
    )
    model = AutoModel.from_pretrained(
        arm.repo_id,
        trust_remote_code=True,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
        pad_token_id=tokenizer.eos_token_id,
    )
    if torch.cuda.is_available():
        model = model.cuda()
    model.eval()
    _GOT_CACHE[arm.repo_id] = (model, tokenizer)


def got_ocr2(arm, payload, rec, timeout=None):
    """In-process OCR via stepfun-ai/GOT-OCR2_0.

    `payload["image"]` is the raw screenshot bytes. The `prompt` key is present but
    ignored — GOT-OCR2 has a fixed OCR task and no generalist instruction slot.

    The model's `chat()` method takes an image file path (not bytes), so the raw bytes
    are written to a temp file, the call is made, and the temp file is removed.
    """
    import os
    import tempfile
    import torch

    if arm.repo_id not in _GOT_CACHE:
        _load_got_ocr2(arm)
    model, tokenizer = _GOT_CACHE[arm.repo_id]

    if not torch.cuda.is_available():
        raise RuntimeError(
            f"{arm.id}: stepfun-ai/GOT-OCR2_0 hardcodes .cuda() in its chat() method "
            "and cannot run without a GPU. Use the offline stub path in ingest.py or "
            "run on a machine with CUDA."
        )

    suffix = ".png" if payload.get("media_type") == "image/png" else ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(payload["image"])
        tmp_path = tmp.name

    try:
        with torch.inference_mode():
            text = model.chat(tokenizer, tmp_path, ocr_type="ocr")
    finally:
        os.unlink(tmp_path)

    text = (text or "").strip()
    rec["read_chars"] = len(text)
    rec["finish_reason"] = "stop"

    if not text:
        raise RuntimeError(f"{arm.id} produced no text from the screenshot.")
    return text


BACKENDS = {"openai-vision": openai_vision, "got-ocr2": got_ocr2}

LOADERS = {"got-ocr2": _load_got_ocr2}
