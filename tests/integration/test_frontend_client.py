"""Streamlit API クライアントと FastAPI 契約の結合テスト。"""

import httpx
import pytest
import respx

from frontend.api_client import ApiError, DocumentApiClient


@pytest.mark.integration
def test_frontend_client_uploads_file_and_options() -> None:
    with respx.mock:
        route = respx.post("http://fastapi:8000/api/v1/ocr/jobs/sync").mock(
            return_value=httpx.Response(
                200,
                json={
                    "job_id": "job-1",
                    "status": "succeeded",
                    "result": {"content": "結果"},
                },
            )
        )
        result = DocumentApiClient("http://fastapi:8000/").analyze(
            "sample.pdf",
            b"%PDF-test",
            "application/pdf",
            {
                "pages": "1-2",
                "locale": "ja-JP",
                "features": "languages",
                "output_content_format": "text",
            },
        )

    assert result["status"] == "succeeded"
    body = route.calls[0].request.content
    assert b"sample.pdf" in body
    assert b'name="pages"' in body
    assert b"1-2" in body
    assert b'name="features"' in body
    assert b"languages" in body


@pytest.mark.integration
def test_frontend_client_exposes_api_error_message() -> None:
    with respx.mock:
        respx.post("http://fastapi:8000/api/v1/ocr/jobs/sync").mock(
            return_value=httpx.Response(
                400,
                json={
                    "detail": {"code": "INVALID", "message": "ページ指定が不正です。"}
                },
            )
        )

        with pytest.raises(ApiError, match="ページ指定が不正"):
            DocumentApiClient("http://fastapi:8000").analyze(
                "sample.pdf",
                b"%PDF-test",
                "application/pdf",
                {},
            )
