from pathlib import Path

import pytest

from m365_mcp.auth import AuthenticationError, get_access_token


def test_missing_token_cache_returns_login_instructions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("M365_TENANT_ID", "tenant-id")
    monkeypatch.setenv("M365_CLIENT_ID", "client-id")
    monkeypatch.setenv("M365_TOKEN_CACHE_PATH", str(tmp_path / "missing-cache.json"))

    with pytest.raises(AuthenticationError, match="m365_mcp.auth login"):
        get_access_token()
