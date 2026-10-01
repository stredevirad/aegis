"""Neutralise characters that let untrusted strings forge structure.

Telemetry strings (service names, operations, log bodies, attribute keys and values, change
summaries) are untrusted. Control characters, line/paragraph separators, zero-width and
bidirectional-override characters can forge new "lines" in prompts and UIs or visually
reorder text (audit finding S-H1 used a newline inside a service name). They are replaced,
never silently dropped, so the tampering stays visible. Replacing instead of rejecting keeps a
single hostile record from invalidating a whole snapshot.

The patterns are written with escape sequences only: this file must itself contain no
invisible characters.
"""

from __future__ import annotations

import re

REPLACEMENT = "\ufffd"

# C0 controls except TAB/LF, DEL, C1 controls, line/paragraph separators, zero-width
# characters, bidi embeddings/overrides/isolates, BOM.
_UNSAFE_TEXT = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)
_UNSAFE_NAME = re.compile(
    "[\x00-\x1f\x7f-\x9f\u2028\u2029\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)


def clean_name(value: str) -> str:
    """Single-line identifier: every control/format character (incl. TAB, LF) is replaced."""
    return _UNSAFE_NAME.sub(REPLACEMENT, value)


def clean_text(value: str) -> str:
    """Free text: TAB and LF are kept (stack traces), everything else unsafe is replaced."""
    return _UNSAFE_TEXT.sub(REPLACEMENT, value)


def single_line(value: str) -> str:
    """For text that is about to be embedded in a one-line summary."""
    return clean_name(value.replace("\n", " ").replace("\t", " "))
