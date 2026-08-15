"""Run a real Entra -> MCP -> OBO -> Microsoft Graph verification.

This script uses MSAL only to obtain a real delegated Token M through Device Code
Flow. It then delegates MCP protocol verification and the real tool call to the
official MCP Inspector CLI. Tokens are never printed or placed in command arguments.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import msal


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def protected_resource_metadata_url(resource_url: str) -> str:
    parsed = urlsplit(resource_url)
    path = parsed.path if parsed.path != "/" else ""
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"/.well-known/oauth-protected-resource{path}",
            "",
            "",
        )
    )


def health_url(resource_url: str) -> str:
    parsed = urlsplit(resource_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))


def acquire_token_m(tenant_id: str, test_client_id: str, scope: str) -> str:
    application = msal.PublicClientApplication(
        client_id=test_client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )
    flow = application.initiate_device_flow(scopes=[scope])
    message = flow.get("message") if isinstance(flow, dict) else None
    if not isinstance(message, str):
        raise RuntimeError("Microsoft Entra did not start Device Code Flow.")

    print(message, file=sys.stderr, flush=True)
    result = application.acquire_token_by_device_flow(flow)
    token = result.get("access_token") if isinstance(result, dict) else None
    if isinstance(token, str) and token.strip():
        return token

    error_code = result.get("error") if isinstance(result, dict) else None
    raise RuntimeError(f"Could not acquire Token M (error={error_code or 'unknown'}).")


def verify_auth_discovery(
    resource_url: str,
    expected_scope: str,
    expected_issuer: str,
) -> None:
    metadata_url = protected_resource_metadata_url(resource_url)
    with httpx.Client(timeout=10.0, follow_redirects=False) as client:
        health = client.get(health_url(resource_url))
        health.raise_for_status()
        if health.json() != {"status": "ok"}:
            raise RuntimeError("Unexpected health response.")

        challenge = client.post(resource_url)
        if challenge.status_code != 401:
            raise RuntimeError(
                f"Anonymous MCP request returned {challenge.status_code}, expected 401."
            )
        if metadata_url not in challenge.headers.get("www-authenticate", ""):
            raise RuntimeError("401 response does not reference MCP resource metadata.")

        metadata = client.get(metadata_url)
        metadata.raise_for_status()
        payload = metadata.json()
        if payload.get("resource") != resource_url:
            raise RuntimeError("Protected resource metadata has the wrong resource URL.")
        if expected_scope not in payload.get("scopes_supported", []):
            raise RuntimeError("Protected resource metadata is missing the MCP scope.")
        authorization_servers = payload.get("authorization_servers")
        if (
            not isinstance(authorization_servers, list)
            or expected_issuer not in authorization_servers
        ):
            raise RuntimeError(
                "Protected resource metadata has the wrong authorization server."
            )

    print("✓ Health, 401 challenge, and protected resource metadata are valid.")


def run_inspector(resource_url: str, token_m: str, query: str) -> None:
    if shutil.which("npx") is None:
        raise RuntimeError("npx is required. Install a supported Node.js release first.")

    inspector_package = os.environ.get(
        "MCP_INSPECTOR_PACKAGE",
        "@modelcontextprotocol/inspector@2.2.0",
    ).strip()
    config = {
        "mcpServers": {
            "vitos-m365-mcp": {
                "type": "http",
                "url": resource_url,
                "headers": {"Authorization": f"Bearer {token_m}"},
            }
        }
    }

    config_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="vitos-m365-mcp-live-",
            suffix=".json",
            delete=False,
        ) as config_file:
            config_path = Path(config_file.name)
            os.chmod(config_path, 0o600)
            json.dump(config, config_file)

        environment = os.environ.copy()
        environment.pop("MCP_CATALOG_PATH", None)
        base_command = [
            "npx",
            "-y",
            inspector_package,
            "--cli",
            "--config",
            str(config_path),
            "--server",
            "vitos-m365-mcp",
        ]

        print("\nListing tools with the official MCP Inspector...")
        subprocess.run(
            [*base_command, "--method", "tools/list"],
            check=True,
            env=environment,
        )

        print("\nCalling real search_sharepoint through MCP -> OBO -> Graph...")
        subprocess.run(
            [
                *base_command,
                "--method",
                "tools/call",
                "--tool-name",
                "search_sharepoint",
                "--tool-args-json",
                json.dumps({"query": query, "top": 1}),
            ],
            check=True,
            env=environment,
        )
    finally:
        if config_path is not None:
            config_path.unlink(missing_ok=True)


def main() -> None:
    tenant_id = required_env("ENTRA_TENANT_ID")
    mcp_client_id = required_env("ENTRA_MCP_CLIENT_ID")
    test_client_id = required_env("ENTRA_LIVE_TEST_CLIENT_ID")
    resource_url = os.environ.get(
        "MCP_RESOURCE_URL",
        "http://127.0.0.1:8001/mcp",
    ).strip()
    required_scope = os.environ.get(
        "ENTRA_REQUIRED_SCOPE",
        "access_as_user",
    ).strip()
    scope = os.environ.get(
        "ENTRA_MCP_SCOPE",
        f"api://{mcp_client_id}/{required_scope}",
    ).strip()
    query = os.environ.get("MCP_LIVE_TEST_QUERY", "VPN").strip() or "VPN"

    expected_issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
    verify_auth_discovery(resource_url, scope, expected_issuer)
    token_m = acquire_token_m(tenant_id, test_client_id, scope)
    try:
        run_inspector(resource_url, token_m, query)
    finally:
        token_m = ""

    print("\n✓ Live Token M -> MCP -> OBO -> Microsoft Graph verification passed.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, httpx.HTTPError, subprocess.CalledProcessError) as exc:
        print(f"\nLive verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
