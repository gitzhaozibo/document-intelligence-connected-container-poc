"""
ファイルアップロードバリデーションのテスト。

コンテンツタイプ・ファイルサイズ・空ファイルの検証を確認します。
"""

import io

import pytest
import respx
from httpx import AsyncClient, Response


class TestUploadValidation:
    """ファイルアップロードバリデーションのテストスイート。"""

    @pytest.mark.asyncio
    async def test_reject_unsupported_content_type(
        self, async_client: AsyncClient
    ) -> None:
        """サポートされていないコンテンツタイプを拒否することを確認します。"""
        response = await async_client.post(
            "/api/v1/ocr/jobs",
            files={"file": ("test.txt", b"hello world", "text/plain")},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["code"] == "INVALID_CONTENT_TYPE"

    @pytest.mark.asyncio
    async def test_reject_html_content_type(self, async_client: AsyncClient) -> None:
        """HTML コンテンツタイプを拒否することを確認します。"""
        response = await async_client.post(
            "/api/v1/ocr/jobs",
            files={"file": ("test.html", b"<html></html>", "text/html")},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["code"] == "INVALID_CONTENT_TYPE"

    @pytest.mark.asyncio
    async def test_accept_pdf_content_type(
        self,
        async_client: AsyncClient,
        sample_pdf_content: bytes,
        mock_operation_id: str,
        operation_location_header: str,
    ) -> None:
        """PDF コンテンツタイプを受け付けることを確認します。"""
        with respx.mock:
            respx.post(
                "http://localhost:5000/formrecognizer/documentModels/prebuilt-read:analyze"
            ).mock(
                return_value=Response(
                    202,
                    headers={"Operation-Location": operation_location_header},
                )
            )

            response = await async_client.post(
                "/api/v1/ocr/jobs",
                files={"file": ("test.pdf", sample_pdf_content, "application/pdf")},
            )

        assert response.status_code == 202
        data = response.json()
        assert data["job_id"] == mock_operation_id

    @pytest.mark.asyncio
    async def test_accept_jpeg_content_type(
        self,
        async_client: AsyncClient,
        sample_jpeg_content: bytes,
        mock_operation_id: str,
        operation_location_header: str,
    ) -> None:
        """JPEG コンテンツタイプを受け付けることを確認します。"""
        with respx.mock:
            respx.post(
                "http://localhost:5000/formrecognizer/documentModels/prebuilt-read:analyze"
            ).mock(
                return_value=Response(
                    202,
                    headers={"Operation-Location": operation_location_header},
                )
            )

            response = await async_client.post(
                "/api/v1/ocr/jobs",
                files={"file": ("test.jpg", sample_jpeg_content, "image/jpeg")},
            )

        assert response.status_code == 202

    @pytest.mark.asyncio
    async def test_reject_empty_file(self, async_client: AsyncClient) -> None:
        """空のファイルを拒否することを確認します。"""
        response = await async_client.post(
            "/api/v1/ocr/jobs",
            files={"file": ("empty.pdf", b"", "application/pdf")},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["code"] == "EMPTY_FILE"

    @pytest.mark.asyncio
    async def test_reject_file_too_large(
        self, async_client: AsyncClient, test_settings
    ) -> None:
        """最大ファイルサイズを超えるファイルを拒否することを確認します。"""
        # テスト設定の max_upload_size_bytes (10MB) を超えるファイル
        oversized_content = b"x" * (test_settings.max_upload_size_bytes + 1)

        response = await async_client.post(
            "/api/v1/ocr/jobs",
            files={"file": ("large.pdf", oversized_content, "application/pdf")},
        )

        assert response.status_code == 413
        data = response.json()
        assert data["detail"]["code"] == "FILE_TOO_LARGE"

    @pytest.mark.asyncio
    async def test_accept_file_at_max_size(
        self,
        async_client: AsyncClient,
        test_settings,
        mock_operation_id: str,
        operation_location_header: str,
    ) -> None:
        """最大ファイルサイズちょうどのファイルを受け付けることを確認します。"""
        max_size_content = b"x" * test_settings.max_upload_size_bytes

        with respx.mock:
            respx.post(
                "http://localhost:5000/formrecognizer/documentModels/prebuilt-read:analyze"
            ).mock(
                return_value=Response(
                    202,
                    headers={"Operation-Location": operation_location_header},
                )
            )

            response = await async_client.post(
                "/api/v1/ocr/jobs",
                files={"file": ("max.pdf", max_size_content, "application/pdf")},
            )

        assert response.status_code == 202

    @pytest.mark.asyncio
    async def test_content_type_with_charset_param(
        self,
        async_client: AsyncClient,
        sample_pdf_content: bytes,
        mock_operation_id: str,
        operation_location_header: str,
    ) -> None:
        """charset パラメーター付きのコンテンツタイプを正しく処理することを確認します。"""
        with respx.mock:
            respx.post(
                "http://localhost:5000/formrecognizer/documentModels/prebuilt-read:analyze"
            ).mock(
                return_value=Response(
                    202,
                    headers={"Operation-Location": operation_location_header},
                )
            )

            response = await async_client.post(
                "/api/v1/ocr/jobs",
                files={
                    "file": (
                        "test.pdf",
                        sample_pdf_content,
                        "application/pdf; charset=utf-8",
                    )
                },
            )

        assert response.status_code == 202
