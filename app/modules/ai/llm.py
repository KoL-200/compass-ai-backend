"""The provider-independent LLM interface."""
from dataclasses import dataclass
from typing import Protocol

from app.core.config import get_settings


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class ProviderTimeout(ProviderError):
    def __init__(self, message: str = "The AI provider did not answer in time"):
        super().__init__(message, retryable=True)


@dataclass(frozen=True)
class LLMRequest:
    task: str  # for example "profile_extract"
    system: str
    user: str  # the prompt text a real provider sends
    json_schema: dict  # the JSON the answer must follow
    payload: dict  # the same inputs as structured data (only the mock provider reads this)
    max_output_tokens: int = 1200
    timeout_seconds: float = 30


@dataclass(frozen=True)
class LLMResponse:
    text: str  # JSON text
    tokens_in: int
    tokens_out: int
    model: str


class Provider(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


# USD per million tokens: (input, output). Add real models here. Unknown models cost 0 here.
PRICES_PER_MILLION: dict[str, tuple[float, float]] = {"mock-1": (0.0, 0.0)}


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    rate = PRICES_PER_MILLION.get(model, (0.0, 0.0))
    return round((tokens_in * rate[0] + tokens_out * rate[1]) / 1_000_000, 6)


def get_provider() -> Provider:
    name = get_settings().ai_provider
    if name == "mock":
        from app.modules.ai.mock_provider import MockProvider

        return MockProvider()
    raise ProviderError(f"Unknown AI provider '{name}'")