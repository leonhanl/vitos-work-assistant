from redteam_bridge.config import Settings


def test_existing_entra_environment_variables_are_used_without_redteam_prefix(settings, monkeypatch):
    existing_api_id = "00000000-0000-0000-0000-000000000099"
    existing_tenant_id = "00000000-0000-0000-0000-000000000088"
    monkeypatch.setenv("ENTRA_WORK_ASSISTANT_API_CLIENT_ID", existing_api_id)
    monkeypatch.setenv("ENTRA_TENANT_ID", existing_tenant_id)
    configured = Settings(**settings.model_dump(exclude={"api_client_id", "tenant_id"}))
    assert str(configured.api_client_id) == existing_api_id
    assert str(configured.tenant_id) == existing_tenant_id
    assert configured.scope == f"api://{existing_api_id}/access_as_user"
    assert configured.owner == f"{existing_tenant_id}:{settings.user_oid}"
