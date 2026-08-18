"""Environment-backed settings for the Agent API."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

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
    skills_directory: Path = Field(validation_alias="SKILLS_DIRECTORY")
    skills_version: str = Field(validation_alias="SKILLS_VERSION")
    entra_tenant_id: UUID = Field(validation_alias="ENTRA_TENANT_ID")
    entra_work_assistant_api_client_id: UUID = Field(
        validation_alias="ENTRA_WORK_ASSISTANT_API_CLIENT_ID"
    )
    entra_work_assistant_api_client_secret: SecretStr = Field(
        validation_alias="ENTRA_WORK_ASSISTANT_API_CLIENT_SECRET"
    )
    entra_mcp_client_id: UUID = Field(validation_alias="ENTRA_MCP_CLIENT_ID")
    entra_mcp_scope: str | None = Field(
        default=None,
        validation_alias="ENTRA_MCP_SCOPE",
    )
    entra_required_scope: str = Field(
        default="access_as_user",
        validation_alias="ENTRA_REQUIRED_SCOPE",
    )
    m365_mcp_url: AnyHttpUrl = Field(validation_alias="M365_MCP_URL")
    portkey_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="PORTKEY_API_KEY",
    )

    @field_validator("llm_model", "skills_version", "entra_required_scope")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("entra_required_scope")
    @classmethod
    def scope_must_be_a_single_value(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("must contain exactly one scope value")
        return value

    @field_validator("entra_mcp_scope")
    @classmethod
    def optional_scope_must_be_a_single_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or any(character.isspace() for character in value):
            raise ValueError("must contain exactly one scope value")
        return value

    @field_validator("llm_api_key", "entra_work_assistant_api_client_secret")
    @classmethod
    def secret_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("portkey_api_key")
    @classmethod
    def optional_secret_must_not_be_blank(
        cls, value: SecretStr | None
    ) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("skills_directory")
    @classmethod
    def skills_artifact_must_be_usable(cls, value: Path) -> Path:
        try:
            directory = value.expanduser().resolve(strict=True)
        except OSError:
            raise ValueError("must point to an existing skills artifact") from None
        if not directory.is_dir():
            raise ValueError("must point to a skills artifact directory")
        if not any(path.is_file() for path in directory.glob("*/SKILL.md")):
            raise ValueError("must contain at least one <skill>/SKILL.md")
        return directory

    @property
    def mcp_scope(self) -> str:
        return self.entra_mcp_scope or (
            f"api://{self.entra_mcp_client_id}/access_as_user"
        )
