"""Log parser (FIXR-017): structured fields out of raw log text, each line a citable evidence record.

Each non-blank line becomes one EvidenceRecord whose id is stable over that line's raw bytes. The
structured fields extracted per line — timestamp, level, component, message — let downstream code
name a specific observation by id. The provenance validator then enforces that a cited id was
actually produced by this parser for this log; a citation to a line that was never parsed is a
defect, not a hallucination to tolerate.

Acceptance criterion in two parts:
  log lines get evidence ids       each non-blank line -> EvidenceRecord with a stable ev-txt-* id.
  a claim citing a missing id fails  validate_provenance raises DanglingEidError on any id that
                                     was not produced by ingest_log for this log.

Usage::

    from src.log_parser import ingest_log, parse_line
    from src.validators.provenance import validate_provenance, DanglingEidError

    records = ingest_log(raw_log_text, source="incident.log")
    minted  = {r.id for r in records}

    validate_provenance(cited_ids, minted)   # raises if any id was not in this log
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict

from schemas.evidence import EvidenceRecord

# ---------------------------------------------------------------------------
# Structured log line
# ---------------------------------------------------------------------------


class LogLine(BaseModel):
    """One parsed log line. `raw` is always present; structured fields are best-effort.

    Fields
    ------
    raw        : the original, unmodified line from the log.
    timestamp  : extracted timestamp string, or None if the line has none.
    level      : normalised severity (DEBUG | INFO | WARN | ERROR | CRITICAL | FATAL | TRACE),
                 or None if no level keyword was found.
    component  : logger name, process, or bracketed tag, or None.
    message    : the message portion of the line, or the whole raw line if no pattern matched.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    raw: str
    timestamp: Optional[str] = None
    level: Optional[str] = None
    component: Optional[str] = None
    message: str


# ---------------------------------------------------------------------------
# Parsing patterns
# ---------------------------------------------------------------------------

# Tried in order; first match wins.  Each compiled regex uses named groups so groupdict() is safe.
_LEVEL_KW = r"DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL|FATAL|TRACE"
_TS_ISO = r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"

_PATTERNS: list[re.Pattern[str]] = [
    # ISO / RFC-3339: 2024-01-15T09:12:34.567 ERROR [component] message
    re.compile(
        rf"^(?P<timestamp>{_TS_ISO})"
        rf"(?:[ \t]+(?P<level>{_LEVEL_KW}))?"
        rf"(?:[ \t]+\[(?P<component>[^\]]+)\])?"
        rf"[ \t]+(?P<message>.+)$",
        re.IGNORECASE,
    ),
    # Syslog: Jan 15 09:12:34 hostname process[pid]: message
    re.compile(
        r"^(?P<timestamp>[A-Za-z]{3}[ \t]+\d{1,2}[ \t]+\d{2}:\d{2}:\d{2})"
        r"[ \t]+\S+"                       # hostname (not captured)
        r"[ \t]+(?P<component>\S+?)(?:\[\d+\])?:[ \t]*"
        r"(?P<message>.+)$",
    ),
    # Logfmt: level=ERROR ts=2024-01-15T09:12:34 msg="..."
    re.compile(
        rf"(?:level=(?P<level>{_LEVEL_KW})[ \t]+)?"
        rf"(?:(?:ts|time|timestamp)=(?P<timestamp>\S+)[ \t]+)?"
        rf'(?:msg|message)="(?P<message>[^"]+)"',
        re.IGNORECASE,
    ),
    # Bracket prefix: [ERROR] 2024-01-15 message  or  [ERROR] message
    re.compile(
        rf"^\[(?P<level>{_LEVEL_KW})\]"
        rf"(?:[ \t]+(?P<timestamp>{_TS_ISO}))?"
        rf"[ \t]+(?P<message>.+)$",
        re.IGNORECASE,
    ),
    # Level-colon: ERROR: message  or  WARN  message
    re.compile(
        rf"^(?P<level>{_LEVEL_KW})(?::)?[ \t]+(?P<message>.+)$",
        re.IGNORECASE,
    ),
]

_LEVEL_RE = re.compile(rf"\b({_LEVEL_KW})\b", re.IGNORECASE)
_LEVEL_NORM = {"WARNING": "WARN"}


def _norm_level(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    upper = raw.upper()
    return _LEVEL_NORM.get(upper, upper)


def parse_line(line: str) -> LogLine:
    """Parse one log line into structured fields. Best-effort; `raw` is always preserved.

    Tries common log patterns in order. If none matches, falls back to a bare-level scan.
    Unknown lines produce a LogLine whose `message` is the whole raw line.
    """
    stripped = line.strip()
    for pat in _PATTERNS:
        m = pat.match(stripped)
        if m:
            gd = m.groupdict()
            return LogLine(
                raw=line,
                timestamp=gd.get("timestamp"),
                level=_norm_level(gd.get("level")),
                component=gd.get("component"),
                message=gd.get("message") or stripped,
            )
    # No pattern matched — scan for a bare level keyword.
    m_level = _LEVEL_RE.search(stripped)
    return LogLine(
        raw=line,
        level=_norm_level(m_level.group(1) if m_level else None),
        message=stripped,
    )


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


def ingest_log(text: str, *, source: str = "log:inline") -> list[EvidenceRecord]:
    """Parse log text and return one EvidenceRecord per non-blank line.

    The id for each record is stable over the raw bytes of that line, so the same log line
    always produces the same id regardless of which run parses it.  Two identical lines in
    the same file collapse to the same id — they are the same observed fact, not two.

    The `content` field holds the verbatim log line — exactly what was observed.  `origin`
    is ``log:parsed`` and `live` is True: no model was called; the content is the raw bytes.
    """
    records: list[EvidenceRecord] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        raw_bytes = line.encode("utf-8")
        records.append(
            EvidenceRecord.build(
                "text",
                raw_bytes,
                content=line,
                source=source,
                origin="log:parsed",
                live=True,
            )
        )
    return records
