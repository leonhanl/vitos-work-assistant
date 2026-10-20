"""Device-code bootstrap and silent delegated-token acquisition using MSAL."""

import fcntl
import os
import sys
import tempfile
from contextlib import contextmanager
from threading import Lock

import msal

from redteam_bridge.config import Settings
from redteam_bridge.errors import BridgeError


class UserTokenCache:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = Lock()
        self._cache = msal.SerializableTokenCache()
        self._client: msal.PublicClientApplication | None = None

    @contextmanager
    def _application(self):
        path = self.settings.cache_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # The CLI and API are separate processes sharing the same volume. Reload
        # under a file lock so neither overwrites refreshed credentials from the other.
        with self._lock:
            fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(fd, "a") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                self._cache.deserialize(path.read_text() if path.exists() else "{}")
                if self._client is None:
                    self._client = msal.PublicClientApplication(
                        str(self.settings.client_id),
                        authority=f"https://login.microsoftonline.com/{self.settings.tenant_id}",
                        token_cache=self._cache,
                    )
                yield self._client
                if self._cache.has_state_changed:
                    fd, temporary = tempfile.mkstemp(dir=path.parent)
                    try:
                        with os.fdopen(fd, "w") as output:
                            output.write(self._cache.serialize())
                        os.replace(temporary, path)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)

    def get_token(self) -> str:
        if not self.settings.cache_path.exists():
            raise BridgeError(
                503, "user_authorization_required",
                "Run redteam-bridge-login to authorize the configured test user again.",
            )
        with self._application() as app:
            account = next(
                (
                    account
                    for account in app.get_accounts()
                    if account.get("local_account_id") == str(self.settings.user_oid)
                    and account.get("realm") == str(self.settings.tenant_id)
                ),
                None,
            )
            result = (
                app.acquire_token_silent_with_error(
                    [self.settings.scope], account=account
                )
                if account
                else None
            )
            if result and result.get("access_token"):
                return result["access_token"]
            raise BridgeError(
                503,
                "user_authorization_required",
                "Run redteam-bridge-login to authorize the configured test user again.",
            )

    def login(self) -> None:
        with self._application() as app:
            flow = app.initiate_device_flow(scopes=[self.settings.scope])
            if "user_code" not in flow:
                raise BridgeError(
                    503, "device_login_failed",
                    f"Could not start device login ({flow.get('error', 'unknown_error')}).",
                )
            print(flow["message"], flush=True)
            result = app.acquire_token_by_device_flow(flow)
            if not result.get("access_token"):
                raise BridgeError(
                    503, "device_login_failed",
                    f"Device login failed ({result.get('error', 'unknown_error')}). "
                    "Check Entra consent, public client flows and Conditional Access.",
                )
            claims = result.get("id_token_claims", {})
            if (
                claims.get("oid") != str(self.settings.user_oid)
                or claims.get("tid") != str(self.settings.tenant_id)
            ):
                raise BridgeError(
                    403, "wrong_test_user",
                    "The signed-in user does not match REDTEAM_USER_OID and ENTRA_TENANT_ID. "
                    "The new authorization was not saved.",
                )
        print("Test user authorized. Token cache saved; no tokens are displayed.")


def main() -> None:
    try:
        UserTokenCache(Settings()).login()
    except BridgeError as error:
        print(error.message, file=sys.stderr)
        raise SystemExit(1) from None
