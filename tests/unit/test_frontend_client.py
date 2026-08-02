"""Streamlit 用 FastAPI クライアントの単体テスト。"""

import httpx
import pytest

from frontend.api_client import DocumentApiClient, _error_message


def test_error_message_reads_structured_fastapi_error() -> None:
    response = httpx.Response(
        400,
        json={"detail": {"code": "INVALID", "message": "入力が不正です。"}},
    )

    assert _error_message(response) == "入力が不正です。"


def test_error_message_handles_non_json_response() -> None:
    response = httpx.Response(502, text="Bad Gateway")

    assert _error_message(response) == "API が HTTP 502 を返しました。"


def test_extract_financial_summary_posts_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "http://api/api/v1/financial-summary/extract")
    response = httpx.Response(200, request=request, json={"fields": []})

    def fake_post(*args: object, **kwargs: object) -> httpx.Response:
        assert args[0] == "http://api/api/v1/financial-summary/extract"
        assert kwargs["files"] == {"file": ("summary.pdf", b"pdf", "application/pdf")}
        return response

    monkeypatch.setattr(httpx, "post", fake_post)

    result = DocumentApiClient("http://api").extract_financial_summary(
        "summary.pdf", b"pdf", "application/pdf"
    )

    assert result == {"fields": []}


def test_download_financial_summary_excel(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", "http://api/api/v1/financial-summary/document-id/excel")
    response = httpx.Response(200, request=request, content=b"xlsx")

    def fake_get(*args: object, **kwargs: object) -> httpx.Response:
        assert args[0] == "http://api/api/v1/financial-summary/document-id/excel"
        return response

    monkeypatch.setattr(httpx, "get", fake_get)

    assert (
        DocumentApiClient("http://api").download_financial_summary_excel("document-id") == b"xlsx"
    )
