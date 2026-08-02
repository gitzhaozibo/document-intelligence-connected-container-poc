"""Streamlit 用 FastAPI クライアントの単体テスト。"""

import httpx

from frontend.api_client import _error_message


def test_error_message_reads_structured_fastapi_error() -> None:
    response = httpx.Response(
        400,
        json={"detail": {"code": "INVALID", "message": "入力が不正です。"}},
    )

    assert _error_message(response) == "入力が不正です。"


def test_error_message_handles_non_json_response() -> None:
    response = httpx.Response(502, text="Bad Gateway")

    assert _error_message(response) == "API が HTTP 502 を返しました。"
