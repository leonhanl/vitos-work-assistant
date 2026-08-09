import json

from langchain_core.messages import ToolMessage

from work_assistant.agent import classify_agent_error, normalize_sources


def test_sources_prefer_read_documents_and_deduplicate() -> None:
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
        {"name": "VPN KB.docx", "url": "https://tenant.example/vpn"}
    ]


def test_sources_ignore_model_text_and_invalid_urls() -> None:
    unrelated = ToolMessage(
        name="read_file",
        tool_call_id="other-1",
        content='{"name":"Fake","web_url":"https://fake.example"}',
    )
    invalid = ToolMessage(
        name="search_sharepoint",
        tool_call_id="search-1",
        content='[{"name":"Local","web_url":"file:///secret"}]',
    )

    assert normalize_sources([unrelated, invalid]) == []


def test_sources_support_mcp_structured_content_artifact() -> None:
    message = ToolMessage(
        name="search_sharepoint",
        tool_call_id="search-1",
        content=[{"type": "text", "text": "search completed"}],
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


def test_graph_errors_are_mapped_without_exposing_raw_details() -> None:
    error = RuntimeError(
        "Microsoft Graph denied access (403). secret upstream detail"
    )

    mapped = classify_agent_error(error)

    assert mapped.status_code == 502
    assert mapped.code == "m365_permission_denied"
    assert "secret upstream detail" not in mapped.public_message


def test_gpt_5_6_reasoning_error_is_not_reported_as_unsupported_tools() -> None:
    error = RuntimeError(
        "Function tools with reasoning_effort are not supported for "
        "gpt-5.6-terra in /v1/chat/completions."
    )

    mapped = classify_agent_error(error)

    assert mapped.code == "llm_tool_calling_configuration_error"
