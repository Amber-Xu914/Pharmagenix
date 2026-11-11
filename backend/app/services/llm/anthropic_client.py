import time

import anthropic

from .base import LLMClient, LLMError, LLMResult


class AnthropicClient(LLMClient):
    provider = "anthropic"

    def complete(self, system, user):
        client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)
        kwargs = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if self.temperature is not None:
            # SDK 1.x dropped the named sampling arguments; the API still accepts them on Haiku 4.5 and older models.
            kwargs["extra_body"] = {"temperature": self.temperature}

        start = time.perf_counter()
        resp = client.messages.create(**kwargs)
        latency = int((time.perf_counter() - start) * 1000)

        text = "".join(block.text for block in resp.content if block.type == "text")
        if not text.strip():
            raise LLMError(f"Anthropic returned an empty response (stop_reason={resp.stop_reason})")
        return LLMResult(
            text=text,
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            latency_ms=latency,
            truncated=resp.stop_reason == "max_tokens",
        )
