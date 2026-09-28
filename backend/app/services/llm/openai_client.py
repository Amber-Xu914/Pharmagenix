import time

from openai import OpenAI

from .base import LLMClient, LLMError, LLMResult


class OpenAIClient(LLMClient):
    provider = "openai"

    def complete(self, system, user):
        client = OpenAI(api_key=self.api_key, timeout=self.timeout)
        kwargs = {
            "model": self.model,
            "instructions": system,
            "input": user,
            # For reasoning models this budget also covers reasoning tokens.
            "max_output_tokens": self.max_tokens,
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature

        start = time.perf_counter()
        resp = client.responses.create(**kwargs)
        latency = int((time.perf_counter() - start) * 1000)

        text = resp.output_text or ""
        truncated = resp.status == "incomplete"
        if not text.strip():
            reason = getattr(resp.incomplete_details, "reason", None) if resp.incomplete_details else None
            raise LLMError(f"OpenAI returned an empty response (status={resp.status}, reason={reason})")
        usage = resp.usage
        return LLMResult(
            text=text,
            model=resp.model,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            latency_ms=latency,
            truncated=truncated,
        )
