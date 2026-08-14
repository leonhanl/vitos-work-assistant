import json

from langchain_core.messages import ToolMessage

from work_assistant.agent import classify_agent_error, normalize_sources
from work_assistant.obo import OboTokenError


def test_sources_include_only_documents_actually_read_and_deduplicate() -> None:
    search = ToolMessage(
        name="search_sharepoint",
        tool_call_id="search-1",
        content=json.dumps(
            [
                {
                    "name": "VPN KB.docx",
                    "web_url": "https://tenant.example/vpn",
                    "drive_id": "d1",
                    "item_id": "i1",
                },
                {
                    "name": "Other.docx",
                    "web_url": "https://tenant.example/other",
                },
            ]
        ),
    )
    read = ToolMessage(
        name="read_document",
        tool_call_id="read-1",
        content=json.dumps(
            {
                "name": "VPN KB.docx",
                "web_url": "https://tenant.example/vpn",
                "content": "Steps...",
            }
        ),
    )

    sources = normalize_sources([search, read, read])

    assert [source.model_dump() for source in sources] == [
        {"name": "VPN KB.docx", "url": "https://tenant.example/vpn"},
    ]


def test_sources_ignore_model_text_and_invalid_urls() -> None:
    unrelated = ToolMessage(
        name="read_file",
        tool_call_id="other-1",
        content='{"name":"Fake","web_url":"https://fake.example"}',
    )
    invalid = ToolMessage(
        name="read_document",
        tool_call_id="read-1",
        content='{"name":"Local","web_url":"file:///secret"}',
    )

    assert normalize_sources([unrelated, invalid]) == []


def test_sources_support_mcp_structured_content_artifact() -> None:
    message = ToolMessage(
        name="read_document",
        tool_call_id="read-1",
        content=[{"type": "text", "text": "document read"}],
        artifact={
            "structured_content": {
                "result": [
                    {
                        "name": "Policy.docx",
                        "web_url": "https://tenant.example/policy",
                    }
                ]
            }
        },
    )

    assert [source.model_dump() for source in normalize_sources([message])] == [
        {"name": "Policy.docx", "url": "https://tenant.example/policy"}
    ]


def test_missing_graph_token_errors_are_mapped_to_authentication_failure() -> None:
    mapped = classify_agent_error(RuntimeError("missing a Graph access token"))

    assert mapped.status_code == 503
    assert mapped.code == "m365_authentication_unavailable"


def test_obo_consent_errors_are_mapped_without_exposing_provider_details() -> None:
    wrapped = RuntimeError("tool failed")
    wrapped.__cause__ = OboTokenError("obo_authorization_required")

    mapped = classify_agent_error(wrapped)

    assert mapped.status_code == 403
    assert mapped.code == "m365_authorization_required"


def test_other_errors_map_to_generic_without_exposing_raw_details() -> None:
    mapped = classify_agent_error(RuntimeError("secret upstream detail"))

    assert mapped.status_code == 502
    assert mapped.code == "agent_execution_failed"
    assert "secret upstream detail" not in mapped.public_message
