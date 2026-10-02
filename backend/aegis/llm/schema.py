"""The only output shape a model may produce (schema-constrained; ADR-009).

The JSON schema below is sent to the provider (Ollama ``format``, OpenAI
``response_format: json_schema`` with ``strict``, Anthropic forced tool input), and the same
pydantic model parses the response. Unknown fields are rejected; every string and list is
bounded. Parsing only establishes *shape*: :mod:`aegis.llm.validate` then checks the content
against the deterministic analysis.

There is deliberately no "reasoning" or "thinking" field: hidden chain-of-thought is not a
product feature and is never stored or shown. What is stored and shown are typed claims, each
citing evidence IDs.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ClaimText = Annotated[str, Field(min_length=1, max_length=400)]
EvidenceIds = Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=10)]


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["observation", "inference"]
    text: ClaimText
    evidence_ids: EvidenceIds


class Adjudication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_candidate: Annotated[str, Field(max_length=200)] | None
    classification: Literal["CONFIRMED", "PLAUSIBLE", "INSUFFICIENT", "NO_ANOMALY"]
    support: Literal["strong", "moderate", "weak", "none"]
    mechanism: Annotated[str, Field(max_length=60)]
    claims: Annotated[list[Claim], Field(max_length=12)]
    contradicting_evidence_ids: Annotated[
        list[Annotated[str, Field(max_length=100)]], Field(max_length=20)
    ]
    uncertainty: Annotated[str, Field(max_length=600)]
    missing_information: Annotated[list[Annotated[str, Field(max_length=200)]], Field(max_length=6)]
    summary: Annotated[str, Field(max_length=800)]
    next_check: Annotated[str, Field(max_length=300)] | None


def _str(max_len: int) -> dict[str, Any]:
    return {"type": "string", "maxLength": max_len}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {**schema, "type": [schema["type"], "null"]}


# Hand-written (not generated) so it stays within what all three providers' strict modes
# accept: every property required, no additional properties, nullable via type unions.
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "selected_candidate",
        "classification",
        "support",
        "mechanism",
        "claims",
        "contradicting_evidence_ids",
        "uncertainty",
        "missing_information",
        "summary",
        "next_check",
    ],
    "properties": {
        "selected_candidate": _nullable(_str(200)),
        "classification": {
            "type": "string",
            "enum": ["CONFIRMED", "PLAUSIBLE", "INSUFFICIENT", "NO_ANOMALY"],
        },
        "support": {"type": "string", "enum": ["strong", "moderate", "weak", "none"]},
        "mechanism": _str(60),
        "claims": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "text", "evidence_ids"],
                "properties": {
                    "type": {"type": "string", "enum": ["observation", "inference"]},
                    "text": _str(400),
                    "evidence_ids": {"type": "array", "maxItems": 10, "items": _str(100)},
                },
            },
        },
        "contradicting_evidence_ids": {"type": "array", "maxItems": 20, "items": _str(100)},
        "uncertainty": _str(600),
        "missing_information": {"type": "array", "maxItems": 6, "items": _str(200)},
        "summary": _str(800),
        "next_check": _nullable(_str(300)),
    },
}
