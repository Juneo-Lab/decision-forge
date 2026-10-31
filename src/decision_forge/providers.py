"""Explicit opt-in model calls through an OpenAI-compatible endpoint."""

from __future__ import annotations

import copy
import json
import math
import re
import time
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import httpx


class StructuredProvider(Protocol):
    def generate(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]: ...


def parse_generated_object(content: str) -> tuple[dict[str, Any], str]:
    """Accept JSON or one enclosing Markdown JSON fence, without extracting arbitrary substrings."""
    encoding = "json"
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\r?\n([\s\S]*)\r?\n```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
        encoding = "json_fence"
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result, encoding


def strict_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize fixed records for native strict output; detect free-form JSON maps."""
    result = copy.deepcopy(schema)
    portable = True

    def visit(value: Any) -> None:
        nonlocal portable
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object":
                if value.get("additionalProperties") is not False:
                    portable = False
                else:
                    value["required"] = list(value.get("properties", {}))
            for child in value.values():
                visit(child)

    visit(result)
    return result if portable and result.get("type") == "object" else None


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        client: httpx.Client | None = None,
        retries: int = 2,
        max_output_tokens: int = 16000,
        response_mode: Literal["auto", "json_schema", "json_object"] = "auto",
        temperature: float | None = 0,
        reasoning_effort: str | None = None,
        timeout_seconds: float = 120,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("Use an explicit HTTP(S) model endpoint")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Endpoint URL must not contain credentials, query parameters, or fragments")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Use HTTPS for remote model endpoints")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.client = client
        self.retries = retries
        if retries < 0:
            raise ValueError("Retries must be nonnegative")
        if max_output_tokens < 1:
            raise ValueError("Output token limit must be positive")
        self.max_output_tokens = max_output_tokens
        if temperature is not None and (not math.isfinite(temperature) or not 0 <= temperature <= 2):
            raise ValueError("Temperature must be between 0 and 2, or omitted")
        if reasoning_effort not in {None, "none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Unsupported reasoning effort")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Timeout must be positive and finite")
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        if response_mode not in {"auto", "json_schema", "json_object"}:
            raise ValueError("Unknown structured response mode")
        self.response_mode = response_mode
        self.last_response_encoding: str | None = None
        self.last_response_metadata: dict[str, Any] | None = None
        self.last_response_mode: str | None = None
        self.last_usage: dict[str, int | float] | None = None
        # Capability negotiation learned at runtime; explicit response_mode values never degrade.
        self.structured_mode_unavailable = False

    @property
    def configuration(self) -> dict[str, Any]:
        return {
            "transport_version": "structured-auto-v3",
            "parser_version": "json-or-single-fence-v1",
            "base_url": self.base_url,
            "model": self.model,
            "response_mode": self.response_mode,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "reasoning_effort": self.reasoning_effort,
        }

    def request_payload(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Prepare exactly the JSON body used by HTTP transport, without side effects."""
        normalized = strict_schema(schema)
        if self.response_mode == "json_schema" and normalized is None:
            raise ValueError("Schema contains free-form maps; select auto or json_object with local validation")
        if self.response_mode == "json_object" or self.structured_mode_unavailable:
            mode = "json_object"
        else:
            mode = "json_object" if normalized is None else "json_schema"
        return self._payload(system, user, schema, normalized, mode)

    def _payload(
        self, system: str, user: str, schema: dict[str, Any], normalized: dict[str, Any] | None, mode: str
    ) -> dict[str, Any]:
        if mode == "json_object":
            system += "\nReturn one JSON object satisfying this schema. Local validation is mandatory.\n" + json.dumps(
                schema
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": mode},
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        if mode == "json_schema":
            payload["response_format"]["json_schema"] = {
                "name": "decision_forge_response",
                "schema": normalized,
                "strict": True,
            }
        return payload

    def _transport_bytes(self, payload: dict[str, Any]) -> int:
        return len(httpx.Request("POST", f"{self.base_url}/chat/completions", json=payload).content)

    def input_bytes(self, system: str, user: str, schema: dict[str, Any]) -> int:
        """Upper bound on transport bytes, including a possible response-mode downgrade."""
        size = self._transport_bytes(self.request_payload(system, user, schema))
        if self.response_mode == "auto" and not self.structured_mode_unavailable:
            # auto may fall back to json_object, whose system prompt carries the whole schema.
            fallback = self._payload(system, user, schema, strict_schema(schema), "json_object")
            size = max(size, self._transport_bytes(fallback))
        return size

    def _error_message(self, response: httpx.Response) -> str:
        """Return the provider's own diagnostic without echoing prompts or credentials."""
        try:
            data = response.json()
        except (ValueError, UnicodeError):
            return ""
        error = data.get("error") if isinstance(data, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        if not isinstance(message, str):
            return ""
        return message.strip()[:300]

    def _rejects_structured_mode(self, response: httpx.Response) -> bool:
        if response.status_code != 400 or self.response_mode != "auto" or self.structured_mode_unavailable:
            return False
        message = self._error_message(response).lower()
        if "response_format" in message:
            return True
        return "json_schema" in message or "json schema" in message

    def generate(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.last_usage = None
        self.last_response_encoding = None
        self.last_response_metadata = None
        self.last_response_mode = None
        payload = self.request_payload(system, user, schema)
        self.last_response_mode = payload["response_format"]["type"]
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        owned = self.client is None
        client = self.client or httpx.Client(timeout=httpx.Timeout(self.timeout_seconds, connect=10))
        try:
            attempt = 0
            while True:
                try:
                    response = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt >= self.retries:
                        raise ValueError("Model endpoint request failed; no source content was logged") from exc
                    time.sleep(2**attempt)
                    attempt += 1
                    continue
                if self._rejects_structured_mode(response):
                    # The endpoint cannot enforce JSON Schema. Degrade once, stay degraded, and retry
                    # without spending a transport retry, because no model generation happened.
                    self.structured_mode_unavailable = True
                    payload = self.request_payload(system, user, schema)
                    self.last_response_mode = payload["response_format"]["type"]
                    continue
                if (response.status_code == 429 or response.status_code >= 500) and attempt < self.retries:
                    time.sleep(2**attempt)
                    attempt += 1
                    continue
                if response.is_error:
                    detail = self._error_message(response)
                    raise ValueError(
                        f"Model endpoint returned HTTP {response.status_code}" + (f": {detail}" if detail else "")
                    )
                try:
                    data = response.json()
                    usage = data.get("usage") if isinstance(data, dict) else None
                    if isinstance(usage, dict) and all(
                        type(usage.get(key)) is int and usage[key] >= 0
                        for key in ("prompt_tokens", "completion_tokens")
                    ):
                        self.last_usage = {key: usage[key] for key in ("prompt_tokens", "completion_tokens")}
                        cost = usage.get("cost")
                        if (
                            isinstance(cost, (int, float))
                            and not isinstance(cost, bool)
                            and math.isfinite(cost)
                            and cost >= 0
                        ):
                            self.last_usage["cost"] = cost
                    choice = data["choices"][0]
                    self.last_response_metadata = {
                        "id": data.get("id"),
                        "model": data.get("model"),
                        "finish_reason": choice.get("finish_reason"),
                    }
                    message = choice["message"]["content"]
                    if not isinstance(message, str):
                        raise ValueError("Model returned no generated text")
                    result, self.last_response_encoding = parse_generated_object(message)
                    return result
                except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("Model returned an invalid structured response") from exc
        finally:
            if owned:
                client.close()
