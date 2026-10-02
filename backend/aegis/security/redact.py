"""Heuristic redaction of secrets and obvious PII in free text (ADR-015, security model C13).

Applied before persisting live telemetry, before any prompt reaches a model, to log output,
and to outbound notifications. Redaction is **heuristic**: it removes the credential and
PII shapes listed below and is tested adversarially, but it cannot promise that nothing
sensitive remains (security model §7: "complete redaction" is a forbidden claim).

Design choices:

* Replacements are explicit markers (``[REDACTED:kind]``) so an operator can see that
  something was removed and what kind it was; the original is never recoverable.
* Only the *value* is removed where a key names it (``password=…``), so the technical
  context of a log line survives ("db connect failed: password=[REDACTED:secret]").
* Identifiers that look random but are not secrets (trace/span IDs, UUIDs, hashes, IPs,
  hostnames) are deliberately left alone: removing them would destroy evidence. Card numbers
  are only redacted when they pass the Luhn check.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# key names whose values are secrets (matched case-insensitively, with _ - . separators)
_SECRET_KEYS = (
    r"pass(?:word|wd|phrase)?|pwd|secret|client[_.-]?secret|token|access[_.-]?token|"  # noqa: S105
    r"refresh[_.-]?token|id[_.-]?token|auth[_.-]?token|api[_.-]?key|apikey|x[_.-]api[_.-]key|"
    r"access[_.-]?key(?:[_.-]?id)?|secret[_.-]?(?:access[_.-]?)?key|private[_.-]?key|"
    r"credentials?|session(?:[_.-]?id)?|sessid|cookie|signature|sig|auth"
)

_RULES: list[tuple[str, re.Pattern[str], str | Callable[[re.Match[str]], str]]] = []


def _rule(
    kind: str, pattern: str, repl: str | Callable[[re.Match[str]], str], flags: int = 0
) -> None:
    _RULES.append((kind, re.compile(pattern, flags), repl))


# 1. Multi-line and structured secrets first.
_rule(
    "private_key",
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    "[REDACTED:private_key]",
    re.S,
)
_rule("jwt", r"\beyJ[A-Za-z0-9_-]{4,}\.eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}", "[REDACTED:jwt]")
# 2. Credentials embedded in URLs: scheme://user:password@host  (keeps scheme and host)
_rule(
    "url_credentials",
    r"\b([a-zA-Z][a-zA-Z0-9+.-]{1,20}://)[^\s/@:'\"<>]+:[^\s/@'\"<>]+@",
    lambda m: f"{m.group(1)}[REDACTED:credentials]@",
)
# 3. HTTP auth headers and bearer tokens anywhere
_rule(
    "authorization",
    r"(?i)\b(authorization|proxy-authorization)(\"?\s*[:=]\s*\"?)\s*(?:bearer|basic|digest|token|negotiate)?\s*[^\s\"',;]+",
    lambda m: f"{m.group(1)}{m.group(2)}[REDACTED:authorization]",
)
_rule("bearer", r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer [REDACTED:bearer]")
_rule(
    "cookie",
    r"(?i)\b(set-cookie|cookie)(\s*:\s*)[^\r\n\"]+",
    lambda m: f"{m.group(1)}{m.group(2)}[REDACTED:cookie]",
)
# 4. Vendor key formats
_rule("aegis_api_key", r"\baegis_[a-z2-7]{8}_[A-Za-z0-9_-]{43}\b", "[REDACTED:api_key]")
_rule("anthropic_key", r"\bsk-ant-[A-Za-z0-9_-]{20,}", "[REDACTED:api_key]")
_rule("openai_key", r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}", "[REDACTED:api_key]")
_rule(
    "github_token",
    r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,})",
    "[REDACTED:token]",
)
_rule("slack_token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}", "[REDACTED:token]")
_rule(
    "slack_webhook", r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+", "[REDACTED:webhook_url]"
)
_rule("google_api_key", r"\bAIza[0-9A-Za-z_-]{35}\b", "[REDACTED:api_key]")
_rule("aws_access_key", r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b", "[REDACTED:aws_key]")
# 5. key=value / key: value / "key": "value" (JSON, query strings, env files, ADO strings);
#    tolerates escaped quotes from JSON embedded in a string.
_rule(
    "secret",
    r"(?i)(?<![A-Za-z0-9])((?:" + _SECRET_KEYS + r"))(\\?[\"']?\s*[:=]\s*\\?[\"']?)"
    r"(?!\[REDACTED)([^\s\"'\\&;,}<>]{1,512})",
    lambda m: f"{m.group(1)}{m.group(2)}[REDACTED:secret]",
)
# 6. PII
_rule(
    "email",
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b",
    "[REDACTED:email]",
)


# attribute keys such as "db.password", "Password", "x-api-key" (but not "token_budget")
_SECRET_KEY_NAME = re.compile(r"(?i)(?:^|[^a-z0-9])(?:" + _SECRET_KEYS + r")$")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_CARD = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")


@dataclass
class Redaction:
    text: str
    counts: Counter[str] = field(default_factory=Counter)

    @property
    def changed(self) -> bool:
        return bool(self.counts)


def redact(text: str) -> Redaction:
    counts: Counter[str] = Counter()
    out = text
    for kind, pattern, repl in _RULES:
        out, n = pattern.subn(repl, out)
        if n:
            counts[kind] += n

    def card(m: re.Match[str]) -> str:
        digits = re.sub(r"[ -]", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            counts["card_number"] += 1
            return "[REDACTED:card_number]"
        return m.group(0)

    out = _CARD.sub(card, out)
    return Redaction(out, counts)


def redact_text(text: str) -> str:
    return redact(text).text


def redact_value(value: Any, counts: Counter[str] | None = None, depth: int = 0) -> Any:
    """Redact strings inside nested JSON-like values; keys named like secrets lose their value."""
    counts = counts if counts is not None else Counter()
    if depth > 20:
        return "[REDACTED:too_deep]"
    if isinstance(value, str):
        r = redact(value)
        counts.update(r.counts)
        return r.text
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and _SECRET_KEY_NAME.search(k) and v not in (None, ""):
                counts["secret"] += 1
                out[k] = "[REDACTED:secret]"
            else:
                out[k] = redact_value(v, counts, depth + 1)
        return out
    if isinstance(value, list | tuple):
        return [redact_value(v, counts, depth + 1) for v in value]
    return value
