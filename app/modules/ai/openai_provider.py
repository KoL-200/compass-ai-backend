"""OpenAI Chat Completions adapter for the provider-independent AI layer."""
import json
import logging

import httpx

from app.core.config import get_settings
from app.modules.ai.llm import LLMRequest, LLMResponse, ProviderError, ProviderTimeout

CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
log = logging.getLogger(__name__)


def _http(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout)


class OpenAIProvider:
    name = "openai"

    def __init__(self):
        settings = get_settings()
        api_key = settings.openai_api_key.strip()
        if not api_key or api_key.lower().startswith(("your-", "replace-", "<")):
            raise ProviderError("OPENAI_API_KEY is not configured")
        self.api_key = api_key
        self.model = settings.ai_model

    def complete(self, request: LLMRequest) -> LLMResponse:
        schema = json.dumps(request.json_schema, separators=(",", ":"))
        system = f"{request.system}\n\nOutput JSON matching this schema:\n{schema}"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": request.user},
            ],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": request.max_output_tokens,
            "temperature": 0,
        }

        try:
            with _http(request.timeout_seconds) as http:
                response = http.post(
                    CHAT_COMPLETIONS_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("Could not reach the OpenAI API", retryable=True) from exc

        if response.status_code >= 400:
            try:
                error = response.json().get("error", {})
            except ValueError:
                error = {}
            error = error if isinstance(error, dict) else {}
            error_type = error.get("type")
            error_code = error.get("code")
            quota_exhausted = error_type == "insufficient_quota" or error_code in {
                "insufficient_quota", "credit_balance_exhausted"
            }
            retryable = not quota_exhausted and (
                response.status_code == 408 or response.status_code == 429 or response.status_code >= 500
            )
            safe_code = error_code if error_code in {
                "insufficient_quota", "credit_balance_exhausted", "rate_limit_exceeded"
            } else "other"
            log.warning("OpenAI API rejected request (status=%s, code=%s)", response.status_code, safe_code)
            raise ProviderError("The OpenAI API rejected the request", retryable=retryable)

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("The OpenAI API returned an invalid response", retryable=True) from exc

        choices = data.get("choices") if isinstance(data, dict) else None
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text:
            raise ProviderError("The OpenAI API returned no content")

        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        return LLMResponse(
            text=text,
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            model=str(data.get("model") or self.model),
        )