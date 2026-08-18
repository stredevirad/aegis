import os
import time
from typing import Any

import httpx

from src.domain import (
    FinishReason,
    HealthStatus,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    MessageRole,
    ProviderStatus,
    ToolCall,
)


class OllamaProvider(LLMProvider):
    def __init__(
        self, 
        base_url: str | None = None, 
        model: str | None = None,
        think: bool | None = None
    ):
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
        self.default_think = think if think is not None else os.environ.get("OLLAMA_THINK", "false").lower() == "true"

    def get_status(self) -> ProviderStatus:
        try:
            with httpx.Client(timeout=2.0) as client:
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                data = response.json()
                models = [m.get("name") for m in data.get("models", [])]
                if any(self.model in m for m in models):
                    return ProviderStatus(
                        provider="ollama",
                        health=HealthStatus.HEALTHY,
                        capabilities=["text_generation", "tool_calling"],
                    )
                else:
                    return ProviderStatus(
                        provider="ollama",
                        health=HealthStatus.DEGRADED,
                        limitations=[f"Model {self.model} not found in available models"],
                    )
        except Exception as e:
            return ProviderStatus(
                provider="ollama",
                health=HealthStatus.UNAVAILABLE,
                limitations=[str(e)],
            )

    def generate(self, request: LLMRequest) -> LLMResponse:
        start_time = time.time()
        
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [],
            "options": {}
        }
        # Configure thinking mode
        think_mode = request.thinking_mode if request.thinking_mode is not None else self.default_think
        # Native Ollama thinking mode flag (not an options emulate)
        payload["options"]["think"] = think_mode

        # Build messages
        if request.messages:
            for msg in request.messages:
                m: dict[str, Any] = {"role": msg.role.value, "content": msg.content}
                if msg.tool_call_id and msg.role == MessageRole.TOOL:
                    if msg.tool_name:
                        m["tool_name"] = msg.tool_name
                if msg.role == MessageRole.ASSISTANT and getattr(msg, 'tool_calls', None):
                    # In true Ollama format, an assistant message with tool calls needs a specific format
                    m["tool_calls"] = [
                        {
                            "function": {
                                "name": tc.name,
                                "arguments": tc.arguments
                            }
                        }
                        for tc in msg.tool_calls
                    ]
                payload["messages"].append(m)
        elif request.prompt:
            if request.system_prompt:
                payload["messages"].append({"role": "system", "content": request.system_prompt})
            payload["messages"].append({"role": "user", "content": request.prompt})

        # Add tools
        if request.tools:
            payload["tools"] = []
            for tool in request.tools:
                tool_def: dict[str, Any] = {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": {
                            "type": "object",
                            "properties": tool.parameters,
                        },
                    }
                }
                if tool.required:
                    tool_def["function"]["parameters"]["required"] = tool.required
                payload["tools"].append(tool_def)

        try:
            with httpx.Client(timeout=180.0) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException:
            return LLMResponse(
                content="",
                finish_reason=FinishReason.ERROR,
                error="Ollama request timed out",
                model_name=self.model,
                latency_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            return LLMResponse(
                content="",
                finish_reason=FinishReason.ERROR,
                error=f"Ollama request failed: {e}",
                model_name=self.model,
                latency_ms=(time.time() - start_time) * 1000,
            )

        latency_ms = (time.time() - start_time) * 1000
        message = data.get("message", {})
        raw_content = message.get("content", "")
        
        # Parse `<think>` tags if the model emits them organically
        content = raw_content
        thinking = None
        
        # Check native thinking field first if API supports it
        if "thinking" in message:
            thinking = message["thinking"]
        elif "<think>" in raw_content and "</think>" in raw_content:
            parts = raw_content.split("</think>", 1)
            if len(parts) == 2:
                thinking_part = parts[0].split("<think>")[1]
                thinking = thinking_part.strip()
                content = parts[1].strip()
        
        tool_calls_raw = message.get("tool_calls", [])
        
        parsed_tools = []
        for tc in tool_calls_raw:
            func = tc.get("function", {})
            parsed_tools.append(
                ToolCall(
                    id=tc.get("id", f"call_{int(time.time()*1000)}"),
                    name=func.get("name", "unknown"),
                    arguments=func.get("arguments", {})
                )
            )

        finish_reason = FinishReason.STOP
        done_reason = data.get("done_reason")
        if parsed_tools:
            finish_reason = FinishReason.TOOL_CALL
        elif done_reason == "length":
            finish_reason = FinishReason.LENGTH
            
        token_count = data.get("eval_count", 0) + data.get("prompt_eval_count", 0)

        return LLMResponse(
            content=content,
            thinking=thinking,
            tool_calls=parsed_tools,
            finish_reason=finish_reason,
            model_name=self.model,
            token_count=token_count if token_count > 0 else None,
            prompt_eval_count=data.get("prompt_eval_count"),
            eval_count=data.get("eval_count"),
            latency_ms=latency_ms,
        )
