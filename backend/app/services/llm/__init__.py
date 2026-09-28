from flask import current_app

from .anthropic_client import AnthropicClient
from .base import LLMClient, LLMError, LLMResult
from .openai_client import OpenAIClient

CLIENTS = {"openai": OpenAIClient, "anthropic": AnthropicClient}


def get_llm_client(provider) -> LLMClient:
    cfg = current_app.config
    if provider not in CLIENTS:
        raise LLMError(f"Unknown LLM provider: {provider}")
    key = provider.upper()
    return CLIENTS[provider](
        api_key=cfg[f"{key}_API_KEY"],
        model=cfg[f"{key}_MODEL"],
        max_tokens=cfg["LLM_MAX_TOKENS"],
        timeout=cfg["LLM_TIMEOUT"],
        temperature=cfg["LLM_TEMPERATURE"],
    )


def provider_info():
    cfg = current_app.config
    return {
        role: {"provider": p, "model": cfg[f"{p.upper()}_MODEL"], "enabled": bool(cfg[f"{p.upper()}_API_KEY"])}
        for role, p in (("primary", cfg["PRIMARY_PROVIDER"]), ("comparison", cfg["COMPARISON_PROVIDER"]))
    }


__all__ = ["LLMClient", "LLMError", "LLMResult", "get_llm_client", "provider_info"]
