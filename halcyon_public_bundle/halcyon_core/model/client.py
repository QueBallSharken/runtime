"""Local OpenAI-compatible model client."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable
from urllib import error, request

from halcyon_core.model.schemas import ModelIntent, parse_model_intent

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


class ModelClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": "model_client_error",
            "message": str(self),
            "status_code": self.status_code,
            "body": self.body,
        }


@dataclass(frozen=True)
class ModelResponse:
    text: str
    intent: ModelIntent
    raw: dict[str, Any]


class LocalModelClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        temperature: float = 0.2,
        timeout: float = 60.0,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self._transport = transport or self._http_post

    def complete(self, messages: list[dict[str, str]]) -> ModelResponse:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        raw = self._transport(f"{self.base_url}/chat/completions", payload)
        text = extract_chat_content(raw)
        intent = parse_model_intent(text)
        return ModelResponse(text=intent.reply, intent=intent, raw=raw)

    def _http_post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise ModelClientError(
                f"model endpoint returned HTTP {exc.code}",
                status_code=exc.code,
                body=body,
            ) from exc
        except error.URLError as exc:
            raise ModelClientError(f"model endpoint request failed: {exc.reason}") from exc


def extract_chat_content(raw: dict[str, Any]) -> str:
    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("OpenAI-compatible response missing choices[0].message.content") from exc
    if not isinstance(content, str):
        raise ValueError("OpenAI-compatible response content must be a string")
    return content
