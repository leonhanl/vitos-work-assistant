"""Environment-backed configuration for the standalone MCP service."""

from __future__ import annotations

from uuid import UUID

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Network and Microsoft Entra settings owned by this service."""

    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    mcp_host: str = Field(default="127.0.0.1", validation_alias="MCP_HOST")
    mcp_port: int = Field(default=8001, validation_alias="MCP_PORT")
    mcp_path: str = Field(default="/mcp", validation_alias="MCP_PATH")
    mcp_resource_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://127.0.0.1:8001/mcp"),
        validation_alias="MCP_RESOURCE_URL",
    )

    entra_tenant_id: UUID = Field(validation_alias="ENTRA_TENANT_ID")
    entra_mcp_client_id: UUID = Field(validation_alias="ENTRA_MCP_CLIENT_ID")
    entra_mcp_client_secret: SecretStr = Field(
        validation_alias="ENTRA_MCP_CLIENT_SECRET"
    )
    entra_required_scope: str = Field(
        default="access_as_user",
        validation_alias="ENTRA_REQUIRED_SCOPE",
    )
    entra_mcp_scope: str | None = Field(
        default=None,
        validation_alias="ENTRA_MCP_SCOPE",
    )

    @field_validator("mcp_host", "entra_required_scope")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("mcp_port")
    @classmethod
    def port_must_be_valid(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("must be between 1 and 65535")
        return value

    @field_validator("mcp_path")
    @classmethod
    def path_must_be_absolute(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/"):
            raise ValueError("must start with '/'")
        return value

    @field_validator("entra_required_scope")
    @classmethod
    def required_scope_must_be_short(cls, value: str) -> str:
        if any(character.isspace() for character in value) or "/" in value:
            raise ValueError("must be one unqualified scope claim value")
        return value

    @field_validator("entra_mcp_scope")
    @classmethod
    def authorization_scope_must_be_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or any(character.isspace() for character in value):
            raise ValueError("must be one fully-qualified scope value")
        return value

    @field_validator("entra_mcp_client_secret")
    @classmethod
    def secret_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @property
    def issuer_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}/v2.0"

    @property
    def authorization_scope(self) -> str:
        """Scope advertised through protected resource metadata."""
        if self.entra_mcp_scope is not None:
            return self.entra_mcp_scope
        return f"api://{self.entra_mcp_client_id}/{self.entra_required_scope}"
