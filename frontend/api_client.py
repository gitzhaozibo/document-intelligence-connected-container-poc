"""FastAPI OCR エンドポイント用の同期クライアント。"""

from typing import Any

import httpx


class ApiError(Exception):
    """FastAPI から返されたユーザー表示可能なエラー。"""


def _error_message(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail", {})
    except ValueError:
        return f"API が HTTP {response.status_code} を返しました。"
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("code") or detail)
    return str(detail)


class DocumentApiClient:
    """Streamlit から FastAPI を呼び出します。"""

    def __init__(self, base_url: str, timeout_seconds: float = 180.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds, connect=10.0)

    def analyze(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        options: dict[str, str],
    ) -> dict[str, Any]:
        """同期 PoC エンドポイントへファイルと解析オプションを送信します。"""
        try:
            response = httpx.post(
                f"{self._base_url}/api/v1/ocr/jobs/sync",
                files={"file": (filename, content, content_type)},
                data=options,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ApiError(_error_message(exc.response)) from exc
        except httpx.RequestError as exc:
            raise ApiError("FastAPI に接続できません。サービスの起動状態を確認してください。") from exc

        try:
            return response.json()
        except ValueError as exc:
            raise ApiError("FastAPI から無効な JSON レスポンスを受信しました。") from exc
