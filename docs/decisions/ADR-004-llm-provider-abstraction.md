# Title: LLM Provider Abstraction
# Status: Accepted
# Date: 2026-08-17

## Context
The initial `LLMProvider` interface contained application-specific methods such as `generate_hypotheses` and `analyze_evidence`. This conflated the language model's generic capabilities (text generation, structured output) with Aegis's specific investigation workflow.

## Decision
Refactor `LLMProvider` to represent generic model invocation (`generate(request) -> response`). The core investigation engine will be responsible for defining the prompts, formatting the requests for hypothesis generation or evidence analysis, and interpreting the responses.

## Consequences
- **Positive**: The LLM Provider adapter is now strictly an abstraction over vendor APIs (e.g., Ollama, OpenAI) and is entirely agnostic to Aegis domain concepts.
- **Negative**: The investigation engine must handle more complex orchestration of prompts and tools.
