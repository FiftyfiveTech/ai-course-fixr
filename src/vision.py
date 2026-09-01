"""Screenshot-to-text backends. One adapter per way of running a document-VLM; the arms are in
config.py.

Nothing here resolves an arm, logs a call, or knows which arm is the default — `src/arms.py` owns
all three, exactly as it does for `stt` and `llm`. A backend takes the arm it was told to run, the
payload, and the mutable call record it adds its measured facts to.

The one adapter is the same OpenAI-compatible /chat/completions the LLM arms speak, with one
difference: the user message carries an image. The VLM endpoint takes the image inline as a `data:`
URI in an `image_url` content part, so the screenshot never touches disk and no file is uploaded —
the bytes go straight into the request body, base64-encoded. It serves a hosted VLM and a local
ollama one identically: `arm.auth_headers` simply omits the Authorization header for the keyless
local arm, the same way `openai_chat` serves both the Groq and ollama LLM arms.
"""
import base64

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

# Hosted, so nothing to warm — the same as the two remote LLM arms.
LOADERS = {}
