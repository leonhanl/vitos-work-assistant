import base64
import json
import stat
import time

import httpx
import msal
import pytest

from redteam_bridge.auth import UserTokenCache
from redteam_bridge.errors import BridgeError


@pytest.fixture
def application(settings, monkeypatch):
    class Application:
        user_oid = str(settings.user_oid)
        silent_result = {"access_token": "refreshed-user-token"}
        scopes = []

        def __init__(self, client_id, authority, token_cache):
            assert client_id == str(settings.client_id)
            assert authority.endswith(str(settings.tenant_id))
            self.cache = token_cache

        def get_accounts(self):
            return list(self.cache.search(msal.TokenCache.CredentialType.ACCOUNT))

        def initiate_device_flow(self, scopes):
            self.scopes.append(scopes)
            return {"user_code": "TEST-CODE", "message": "Open Microsoft login and enter TEST-CODE."}

        def acquire_token_by_device_flow(self, flow):
            account = {
                "home_account_id": f"{self.user_oid}.{settings.tenant_id}",
                "environment": "login.microsoftonline.com",
                "realm": str(settings.tenant_id),
                "local_account_id": self.user_oid,
                "username": "alice@example.com",
            }
            self.cache.modify(msal.TokenCache.CredentialType.ACCOUNT, account, account)
            return {
                "access_token": "device-user-token",
                "id_token_claims": {"oid": self.user_oid, "tid": str(settings.tenant_id)},
            }

        def acquire_token_silent_with_error(self, scopes, account):
            assert scopes == [settings.scope]
            assert account["local_account_id"] == str(settings.user_oid)
            self.cache.modify(msal.TokenCache.CredentialType.ACCOUNT, account, {"username": "refreshed@example.com"})
            return self.silent_result

    monkeypatch.setattr(msal, "PublicClientApplication", Application)
    return Application


def test_login_persists_cache_and_running_service_reloads_it(settings, application, capsys):
    service = UserTokenCache(settings)
    with pytest.raises(BridgeError, match="authorize") as raised:
        service.get_token()
    assert raised.value.code == "user_authorization_required"

    UserTokenCache(settings).login()
    assert application.scopes == [[settings.scope]]
    assert service.get_token() == "refreshed-user-token"
    assert "refreshed@example.com" in settings.cache_path.read_text()
    assert stat.S_IMODE(settings.cache_path.stat().st_mode) == 0o600
    output = capsys.readouterr().out
    assert "TEST-CODE" in output
    assert "device-user-token" not in output
    assert "refreshed-user-token" not in output


def test_wrong_user_cannot_replace_existing_authorization(settings, application):
    tokens = UserTokenCache(settings)
    tokens.login()
    before = settings.cache_path.read_text()
    application.user_oid = "00000000-0000-0000-0000-000000000099"
    with pytest.raises(BridgeError) as raised:
        tokens.login()
    assert raised.value.code == "wrong_test_user"
    assert settings.cache_path.read_text() == before


def test_revoked_authorization_requires_operator_login(settings, application):
    tokens = UserTokenCache(settings)
    tokens.login()
    application.silent_result = {"error": "invalid_grant", "error_description": "secret-provider-detail"}
    with pytest.raises(BridgeError) as raised:
        tokens.get_token()
    assert raised.value.status == 503
    assert "secret-provider-detail" not in raised.value.message


def test_cache_is_not_silently_replaced_when_corrupt(settings, application):
    settings.cache_path.write_text("invalid JSON")
    with pytest.raises(json.JSONDecodeError):
        UserTokenCache(settings).get_token()
    assert settings.cache_path.read_text() == "invalid JSON"


def test_real_msal_refreshes_expired_token_and_persists_rotated_credentials(settings, monkeypatch):
    cache = msal.SerializableTokenCache()
    common = {
        "home_account_id": f"{settings.user_oid}.{settings.tenant_id}",
        "environment": "login.microsoftonline.com",
    }
    account = {
        **common, "realm": str(settings.tenant_id),
        "local_account_id": str(settings.user_oid),
        "username": "alice@example.com", "authority_type": "MSSTS",
    }
    access = {
        **common, "client_id": str(settings.client_id),
        "realm": str(settings.tenant_id), "target": settings.scope,
        "credential_type": "AccessToken", "token_type": "Bearer",
        "secret": "expired-access", "cached_at": "1", "expires_on": "2",
        "extended_expires_on": "2",
    }
    refresh = {
        **common, "client_id": str(settings.client_id), "target": settings.scope,
        "credential_type": "RefreshToken", "secret": "old-refresh",
        "last_modification_time": str(int(time.time())),
    }
    for kind, value in [("Account", account), ("AccessToken", access), ("RefreshToken", refresh)]:
        cache.modify(kind, value, value)
    settings.cache_path.write_text(cache.serialize())
    posts = []

    class Entra:
        def get(self, url, **kwargs):
            assert "openid-configuration" in url
            base = f"https://login.microsoftonline.com/{settings.tenant_id}"
            return httpx.Response(200, json={
                "authorization_endpoint": base + "/oauth2/v2.0/authorize",
                "token_endpoint": base + "/oauth2/v2.0/token",
                "issuer": base + "/v2.0",
            })

        def post(self, url, data, **kwargs):
            assert data["grant_type"] == "refresh_token"
            assert data["refresh_token"] == "old-refresh"
            assert settings.scope in data["scope"]
            posts.append(url)
            return httpx.Response(200, json={
                "access_token": "new-access", "refresh_token": "rotated-refresh",
                "expires_in": 3600, "scope": settings.scope, "token_type": "Bearer",
                "client_info": base64.urlsafe_b64encode(json.dumps({
                    "uid": str(settings.user_oid), "utid": str(settings.tenant_id),
                }).encode()).decode().rstrip("="),
            })

    actual_application = msal.PublicClientApplication
    monkeypatch.setattr(
        msal, "PublicClientApplication",
        lambda *args, **kwargs: actual_application(
            *args, **kwargs, http_client=Entra(), instance_discovery=False
        ),
    )
    tokens = UserTokenCache(settings)
    assert tokens.get_token() == "new-access"
    assert "rotated-refresh" in settings.cache_path.read_text()
    # A second call loads the saved access token; it does not refresh again.
    assert tokens.get_token() == "new-access"
    assert len(posts) == 1
