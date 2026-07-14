"""Injection scanning for content that persists into future context windows.

Memory entries and skills are re-injected into system prompts across every
future session — a poisoned entry persists until explicitly removed. So
anything flowing INTO those stores is scanned at write time (the Hermes
``threat_patterns`` pattern: one source of truth, strictness proportional
to persistence).

Deliberately narrow: we block instruction-hijack phrasing and data-exfil
link shapes, not legitimate engineering content. A skill body containing
``curl`` or an API example must pass; "ignore all previous instructions"
must not.
"""

from __future__ import annotations

import re
from typing import Optional

# (compiled pattern, human-readable reason)
_THREATS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+"
                r"(?:instructions?|context|prompts?)", re.I),
     "instruction-override phrasing ('ignore previous instructions')"),
    (re.compile(r"\bdisregard\s+(?:all\s+|your\s+)?(?:previous|prior|system)\s+"
                r"(?:instructions?|prompts?)", re.I),
     "instruction-override phrasing ('disregard prior instructions')"),
    (re.compile(r"\bforget\s+(?:everything|all)\s+(?:you\s+know|above|previous)", re.I),
     "instruction-override phrasing ('forget everything above')"),
    (re.compile(r"\byou\s+are\s+now\s+(?:in\s+)?(?:developer|dan|jailbreak|god)\s*mode\b", re.I),
     "jailbreak-mode phrasing"),
    (re.compile(r"<\s*/?\s*system\s*>", re.I),
     "embedded <system> tag"),
    (re.compile(r"\bnew\s+system\s+prompt\s*:", re.I),
     "system-prompt replacement phrasing"),
    (re.compile(r"\bdo\s+not\s+(?:tell|inform|alert)\s+the\s+user\b", re.I),
     "concealment instruction ('do not tell the user')"),
    # Markdown link/image whose URL smuggles secrets in query params —
    # the classic exfil channel for content rendered in a client.
    (re.compile(r"\]\(\s*https?://[^)\s]*[?&][^)\s]*"
                r"(?:token|secret|key|password|credential|auth)[^)\s]*=", re.I),
     "markdown link with credential-shaped query parameter (possible exfil)"),
]


def first_threat(content: str) -> Optional[str]:
    """Return a human-readable reason if *content* matches a threat pattern,
    else None. Callers refuse the write and surface the reason."""
    for pattern, reason in _THREATS:
        if pattern.search(content):
            return reason
    return None
