import pytest

from redteam_bridge.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        ENTRA_TENANT_ID="00000000-0000-0000-0000-000000000001",
        client_id="00000000-0000-0000-0000-000000000002",
        ENTRA_WORK_ASSISTANT_API_CLIENT_ID="00000000-0000-0000-0000-000000000003",
        user_oid="00000000-0000-0000-0000-000000000004",
        api_key="test-api-key-with-at-least-32-characters",
        assistant_url="http://agent/chat",
        cache_path=tmp_path / "msal_token_cache.json",
        conversations_path=tmp_path / "conversations.sqlite3",
    )
