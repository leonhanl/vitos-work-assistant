"""Environment-backed settings for the Agent API."""

from __future__ import annotations

from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Small, provider-neutral configuration surface."""

    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    llm_base_url: AnyHttpUrl = Field(validation_alias="LLM_BASE_URL")
    llm_api_key: SecretStr = Field(validation_alias="LLM_API_KEY")
    llm_model: str = Field(validation_alias="LLM_MODEL")
    m365_mcp_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://127.0.0.1:8001/mcp"),
        validation_alias="M365_MCP_URL",
    )

    @field_validator("llm_model")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("llm_api_key")
    @classmethod
    def api_key_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @property
    def repository_root(self) -> Path:
        return Path(__file__).resolve().parents[4]

    @property
    def skill_file(self) -> Path:
        return (
            self.repository_root
            / "apps"
            / "agent"
            / "skills"
            / "enterprise-knowledge-search"
            / "SKILL.md"
        )
