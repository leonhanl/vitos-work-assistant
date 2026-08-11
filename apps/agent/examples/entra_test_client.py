"""Interactive Entra login client for Work Assistant Token A testing."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass

import httpx
import msal


@dataclass(frozen=True)
class ClientConfig:
    tenant_id: str
    client_id: str
    api_client_id: str
    required_scope: str
    api_url: str

    @classmethod
    def from_env(cls) -> ClientConfig:
        environment_names = {
            "tenant_id": "ENTRA_TENANT_ID",
            "client_id": "ENTRA_TEST_CLIENT_ID",
            "api_client_id": "ENTRA_WORK_ASSISTANT_API_CLIENT_ID",
            "required_scope": "ENTRA_REQUIRED_SCOPE",
            "api_url": "WORK_ASSISTANT_API_URL",
        }
        defaults = {
            "required_scope": "access_as_user",
            "api_url": "http://127.0.0.1:8000",
        }
        values: dict[str, str] = {}
        for field_name, environment_name in environment_names.items():
            default = defaults.get(field_name, "")
            values[field_name] = os.environ.get(environment_name, default).strip()
        missing = [
            environment_names[name] for name, value in values.items() if not value
        ]
        if missing:
            raise ValueError(
                "Missing test-client configuration: " + ", ".join(missing)
            )
        return cls(**values)

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def scope(self) -> str:
        return f"api://{self.api_client_id}/{self.required_scope}"


def acquire_token_a(config: ClientConfig, login_hint: str | None) -> dict[str, object]:
    application = msal.PublicClientApplication(
        client_id=config.client_id,
        authority=config.authority,
    )
    result = application.acquire_token_interactive(
        scopes=[config.scope],
        prompt="select_account",
        login_hint=login_hint,
    )
    if not isinstance(result.get("access_token"), str):
        error = result.get("error", "unknown_error")
        raise RuntimeError(f"Interactive Microsoft Entra login failed ({error}).")
    return result


def print_json(label: str, response: httpx.Response) -> None:
    try:
        body = response.json()
    except ValueError:
        body = {"status_code": response.status_code, "body": response.text}
    print(f"{label}: {json.dumps(body, ensure_ascii=False, indent=2)}")


def run(message: str, login_hint: str | None, me_only: bool) -> None:
    config = ClientConfig.from_env()
    token_result = acquire_token_a(config, login_hint)
    access_token = token_result["access_token"]
    expires_in = token_result.get("expires_in", "unknown")
    print(f"Token A acquired; expires_in={expires_in}. The token is not displayed.")

    headers = {"Authorization": f"Bearer {access_token}"}
    with httpx.Client(timeout=60.0) as client:
        me_response = client.get(f"{config.api_url.rstrip('/')}/me", headers=headers)
        print_json("GET /me", me_response)
        me_response.raise_for_status()

        if me_only:
            return

        chat_response = client.post(
            f"{config.api_url.rstrip('/')}/chat",
            headers=headers,
            json={"message": message},
        )
        print_json("POST /chat", chat_response)
        chat_response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--message",
        default="Python 中 list 和 tuple 有什么区别？",
    )
    parser.add_argument(
        "--login-hint",
        help="Optional Alice/Bob username hint; Entra still performs the login.",
    )
    parser.add_argument(
        "--me-only",
        action="store_true",
        help="Validate Token A and identity without invoking the Agent or LLM.",
    )
    args = parser.parse_args()

    try:
        run(args.message, args.login_hint, args.me_only)
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
