"""Delegated Microsoft 365 authentication using MSAL Device Code Flow."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import msal

SCOPES = ["User.Read", "Files.Read.All"]
LOGIN_INSTRUCTIONS = (
    "No valid Microsoft 365 login session.\nRun:\n\n"
    "python -m m365_mcp.auth login"
)


class AuthenticationError(RuntimeError):
    """A safe-to-display authentication error that contains no token data."""


@dataclass(frozen=True)
class AuthConfig:
    tenant_id: str
    client_id: str
    cache_path: Path

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"


def load_config() -> AuthConfig:
    """Read authentication settings from the environment."""
    tenant_id = os.environ.get("M365_TENANT_ID", "").strip()
    client_id = os.environ.get("M365_CLIENT_ID", "").strip()
    missing = [
        name
        for name, value in (
            ("M365_TENANT_ID", tenant_id),
            ("M365_CLIENT_ID", client_id),
        )
        if not value
    ]
    if missing:
        raise AuthenticationError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )

    default_cache = Path.home() / ".cache" / "m365-mcp" / "msal_token_cache.json"
    cache_path = Path(
        os.environ.get("M365_TOKEN_CACHE_PATH", str(default_cache))
    ).expanduser()
    return AuthConfig(tenant_id, client_id, cache_path)


def _load_cache(path: Path) -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if path.exists():
        try:
            cache.deserialize(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AuthenticationError(
                "The Microsoft 365 token cache cannot be read. "
                "Remove it and run the login command again."
            ) from exc
    return cache


def _save_cache(cache: msal.SerializableTokenCache, path: Path) -> None:
    if not cache.has_state_changed:
        return

    directory_was_missing = not path.parent.exists()
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if directory_was_missing and os.name != "nt":
            path.parent.chmod(0o700)

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            delete=False,
        ) as temporary_file:
            temporary_file.write(cache.serialize())
            temporary_path = Path(temporary_file.name)
        if os.name != "nt":
            temporary_path.chmod(0o600)
        temporary_path.replace(path)
        temporary_path = None
    except OSError as exc:
        raise AuthenticationError(
            "The Microsoft 365 token cache could not be saved securely."
        ) from exc
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def _create_app(
    config: AuthConfig, cache: msal.SerializableTokenCache
) -> msal.PublicClientApplication:
    try:
        return msal.PublicClientApplication(
            client_id=config.client_id,
            authority=config.authority,
            token_cache=cache,
        )
    except Exception:
        # Authority discovery errors can include low-level HTTP details. Keep the
        # MCP-facing exception useful but fully under our control.
        raise AuthenticationError(
            "Could not initialize Microsoft 365 authentication. Check the tenant ID, "
            "client ID, and network connection."
        ) from None


def get_access_token() -> str:
    """Acquire a delegated Graph token silently, refreshing it when possible."""
    config = load_config()
    if not config.cache_path.exists():
        raise AuthenticationError(LOGIN_INSTRUCTIONS)
    cache = _load_cache(config.cache_path)
    app = _create_app(config, cache)
    accounts = app.get_accounts()

    result = None
    if len(accounts) == 1:
        try:
            result = app.acquire_token_silent(SCOPES, account=accounts[0])
        except Exception:
            # Do not propagate MSAL exception text because it is outside our control.
            raise AuthenticationError(LOGIN_INSTRUCTIONS) from None
        finally:
            _save_cache(cache, config.cache_path)

    if result and isinstance(result.get("access_token"), str):
        return result["access_token"]
    raise AuthenticationError(LOGIN_INSTRUCTIONS)


def login() -> None:
    """Run Device Code Flow and persist the resulting MSAL token cache."""
    config = load_config()
    # A successful explicit login replaces the old cache, keeping this first-stage
    # server deliberately single-user and making account selection deterministic.
    cache = msal.SerializableTokenCache()
    app = _create_app(config, cache)

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise AuthenticationError(
            "Could not start Microsoft 365 Device Code Flow. "
            "Check the tenant ID, client ID, and public-client configuration."
        )

    # The device-code message is intended for the interactive login terminal.
    # It contains a short-lived user code, never a Graph access token.
    print(flow["message"], flush=True)
    try:
        result = app.acquire_token_by_device_flow(flow)
    except Exception:
        raise AuthenticationError(
            "Microsoft 365 login could not be completed. Please try again."
        ) from None

    if "access_token" not in result:
        error_code = result.get("error", "unknown_error")
        raise AuthenticationError(f"Microsoft 365 login failed ({error_code}).")

    _save_cache(cache, config.cache_path)

    claims = result.get("id_token_claims") or {}
    username = claims.get("preferred_username") or claims.get("name")
    suffix = f" as {username}" if username else ""
    print(f"Microsoft 365 login succeeded{suffix}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["login"])
    args = parser.parse_args()

    try:
        if args.command == "login":
            login()
    except AuthenticationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
