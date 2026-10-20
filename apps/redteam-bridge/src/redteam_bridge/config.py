from pathlib import Path
from uuid import UUID

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDTEAM_", extra="ignore")

    tenant_id: UUID = Field(validation_alias="ENTRA_TENANT_ID")
    client_id: UUID
    api_client_id: UUID = Field(validation_alias="ENTRA_WORK_ASSISTANT_API_CLIENT_ID")
    user_oid: UUID
    api_key: SecretStr
    assistant_url: AnyHttpUrl = "http://agent:8000/chat"
    cache_path: Path = Path("/data/msal_token_cache.json")
    conversations_path: Path = Path("/data/conversations.sqlite3")
    request_timeout: float = Field(default=120, gt=0)

    @field_validator("api_key")
    @classmethod
    def require_api_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().strip()) < 32:
            raise ValueError("must contain at least 32 characters")
        return value

    @property
    def scope(self) -> str:
        return f"api://{self.api_client_id}/access_as_user"

    @property
    def owner(self) -> str:
        return f"{self.tenant_id}:{self.user_oid}"
