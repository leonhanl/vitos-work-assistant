"""Environment-backed settings for the Agent API."""

from __future__ import annotations

import sys
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
    m365_mcp_python: str = Field(
        default_factory=lambda: sys.executable,
        validation_alias="M365_MCP_PYTHON",
    )

    @field_validator("llm_model", "m365_mcp_python")
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
    def m365_mcp_working_directory(self) -> Path:
        return self.repository_root / "services" / "m365-mcp"

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

