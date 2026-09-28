from abc import ABC, abstractmethod
from dataclasses import dataclass


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    truncated: bool = False


class LLMClient(ABC):
    provider = "base"

    def __init__(self, api_key, model, max_tokens=4000, timeout=90, temperature=None):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.temperature = temperature

    @property
    def enabled(self):
        return bool(self.api_key)

    @abstractmethod
    def complete(self, system, user) -> LLMResult:
        """Send one system + user prompt and return the text answer."""
