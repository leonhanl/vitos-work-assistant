"""OpenAI-compatible LangChain chat model construction."""

from typing import Any

from langchain_openai import ChatOpenAI

from work_assistant.config import Settings


def create_chat_model(settings: Settings) -> ChatOpenAI:
    """Create the common Chat Completions + tool-calling client."""
    compatibility_options: dict[str, Any] = {}
    if _is_gpt_5_6(settings.llm_model):
        # GPT-5.6 defaults to reasoning effort "medium", while its Chat
        # Completions endpoint only accepts function tools at effort "none".
        # Responses API is intentionally out of scope for this minimal demo.
        compatibility_options["reasoning_effort"] = "none"

    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=str(settings.llm_base_url),
        use_responses_api=False,
        max_retries=1,
        timeout=60.0,
        **compatibility_options,
    )


def _is_gpt_5_6(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized == "gpt-5.6" or normalized.startswith("gpt-5.6-")
