"""OpenAI-compatible PydanticAI model construction."""

from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from work_assistant.config import Settings


def create_chat_model_client(settings: Settings) -> OpenAIChatModel:
    """Create the PydanticAI model used by the shared Agent."""
    model_settings = OpenAIChatModelSettings(timeout=60.0)
    if _is_gpt_5_6(settings.llm_model):
        # GPT-5.6 defaults to reasoning effort "medium", while its Chat
        # Completions endpoint only accepts function tools at effort "none".
        # Responses API is intentionally out of scope for this minimal demo.
        model_settings["openai_reasoning_effort"] = "none"

    provider = OpenAIProvider(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=str(settings.llm_base_url),
    )
    return OpenAIChatModel(
        settings.llm_model,
        provider=provider,
        settings=model_settings,
    )


def _is_gpt_5_6(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized == "gpt-5.6" or normalized.startswith("gpt-5.6-")
