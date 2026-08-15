from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart

from work_assistant.agent import normalize_sources


def _tool_result(name: str, content) -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(name, content)])


def test_sources_include_only_documents_actually_read_and_deduplicate() -> None:
    search = _tool_result(
        "search_sharepoint",
        [{"name": "VPN KB.docx", "web_url": "https://tenant.example/vpn"}],
    )
    read = _tool_result(
        "read_document",
        {
            "name": "VPN KB.docx",
            "web_url": "https://tenant.example/vpn",
            "content": "Steps...",
        },
    )

    sources = normalize_sources([search, read, read])

    assert [source.model_dump() for source in sources] == [
        {"name": "VPN KB.docx", "url": "https://tenant.example/vpn"},
    ]


def test_sources_ignore_model_text_other_tools_and_invalid_urls() -> None:
    model_text = ModelResponse(parts=[TextPart("https://fake.example")])
    unrelated = _tool_result(
        "read_file",
        {"name": "Fake", "web_url": "https://fake.example"},
    )
    invalid = _tool_result(
        "read_document",
        {"name": "Local", "web_url": "file:///secret"},
    )

    assert normalize_sources([model_text, unrelated, invalid]) == []
