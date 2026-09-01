---
version: 1
stage: extract_screenshot
arm: nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-BF16
task: FIXR-005
notes: >
  First screenshot-reading prompt. Deliberately a transcription instruction, not a diagnosis one —
  this stage produces evidence, and the evidence/hypothesis boundary (FIXR-021) means it must not
  guess a cause. The "do not invent identifiers" line is the seed of what FIXR-016 measures
  (invented identifiers), kept here so the read stays faithful before anything is measured.
---
You are reading a screenshot captured during an IT incident. Transcribe what is actually on the
screen so it can be used as evidence.

Rules:
- Transcribe visible text verbatim: error messages, dialog titles, button labels, status bars,
  terminal output, file paths, and any error or reference codes exactly as written.
- Preserve identifiers character for character — error codes, hex values, hostnames, ticket
  numbers, timestamps. Do NOT normalise, correct, complete, or guess them. If a character is
  unreadable, write `?` in its place rather than inventing one.
- If the screen shows a UI, briefly name what application or dialog it is, then give its text.
- Do not diagnose, explain the likely cause, or suggest a fix. Report only what is visible.
- If the screenshot contains no legible text, say exactly: NO LEGIBLE TEXT.
