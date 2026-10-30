"""Explicit opt-in model calls through an OpenAI-compatible endpoint."""

from __future__ import annotations

import json
import time
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx


class StructuredProvider(Protocol):
    def generate(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]: ...


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        client: httpx.Client | None = None,
        retries: int = 2,
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

    def generate(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "organization_project", "schema": schema},
            },
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        owned = self.client is None
        client = self.client or httpx.Client(timeout=httpx.Timeout(120, connect=10))
        try:
            for attempt in range(self.retries + 1):
                try:
                    response = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt == self.retries:
                        raise ValueError("Model endpoint request failed; no source content was logged") from exc
                    time.sleep(2**attempt)
                    continue
                if (response.status_code == 429 or response.status_code >= 500) and attempt < self.retries:
                    time.sleep(2**attempt)
                    continue
                if response.is_error:
                    raise ValueError(f"Model endpoint returned HTTP {response.status_code}")
                try:
                    data = response.json()
                    message = data["choices"][0]["message"]["content"]
                    result = json.loads(message)
                    if not isinstance(result, dict):
                        raise ValueError("Expected a JSON object")
                    return result
                except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("Model returned an invalid structured response") from exc
            raise ValueError("Model request exhausted retries")
        finally:
            if owned:
                client.close()
