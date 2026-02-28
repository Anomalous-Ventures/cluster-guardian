"""LLM provider factory for Cluster Guardian.

Supports multiple LLM backends: OpenAI, Anthropic, Azure OpenAI, Ollama, and LiteLLM.
"""

from langchain_openai import ChatOpenAI
from .config import settings


def create_llm():
    """Create the appropriate LangChain chat model based on settings.llm_provider.

    Supported providers:
    - openai: Direct OpenAI API or OpenAI-compatible (default)
    - litellm: LiteLLM proxy (OpenAI-compatible)
    - anthropic: Anthropic Claude models
    - azure_openai: Azure OpenAI Service
    - ollama: Local Ollama instance
    """
    provider = settings.llm_provider.lower()

    if provider in ("openai", "litellm"):
        kwargs = {
            "model": settings.llm_model,
            "temperature": 0.1,
        }
        if settings.llm_api_key:
            kwargs["api_key"] = settings.llm_api_key
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        return ChatOpenAI(**kwargs)

    elif provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError(
                "langchain-anthropic is required for the 'anthropic' provider. "
                "Install it with: pip install langchain-anthropic"
            )
        kwargs = {
            "model": settings.llm_model,
            "temperature": 0.1,
        }
        if settings.llm_api_key:
            kwargs["api_key"] = settings.llm_api_key
        return ChatAnthropic(**kwargs)

    elif provider == "azure_openai":
        from langchain_openai import AzureChatOpenAI

        kwargs = {
            "model": settings.llm_model,
            "temperature": 0.1,
        }
        if settings.llm_api_key:
            kwargs["api_key"] = settings.llm_api_key
        if settings.llm_base_url:
            kwargs["azure_endpoint"] = settings.llm_base_url
        return AzureChatOpenAI(**kwargs)

    elif provider == "ollama":
        kwargs = {
            "model": settings.llm_model,
            "temperature": 0.1,
            "base_url": settings.llm_base_url or "http://localhost:11434/v1",
        }
        if settings.llm_api_key:
            kwargs["api_key"] = settings.llm_api_key
        else:
            kwargs["api_key"] = "ollama"  # Ollama doesn't need a real key
        return ChatOpenAI(**kwargs)

    else:
        raise ValueError(
            f"Unsupported LLM provider: {provider}. "
            f"Supported: openai, litellm, anthropic, azure_openai, ollama"
        )
