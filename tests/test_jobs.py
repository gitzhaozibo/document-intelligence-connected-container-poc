"""
OCR ジョブのテスト。

ジョブ送信・ポーリング・エラーハンドリングを検証します。
モックを使用して Azure や実際のコンテナーへの接続を行いません。
"""

import pytest
import respx
from httpx import AsyncClient, Response

from app.client import (
    _extract_operation_id_from_location,
    validate_operation_id,
)


class TestOperationIdValidation:
    """操作 ID バリデーションのテストスイート。"""

    def test_valid_operation_id(self) -> None:
        """有効な操作 ID を受け付けることを確認します。"""
        assert validate_operation_id("abc-123") is True
        assert validate_operation_id("TEST_OPERATION_ID") is True
        assert validate_operation_id("abcdef1234567890-abcd-efgh-ijkl-mnopqrstuvwx") is True

    def test_invalid_operation_id_empty(self) -> None:
        """空の操作 ID を拒否することを確認します。"""
        assert validate_operation_id("") is False

    def test_invalid_operation_id_path_traversal(self) -> None:
        """パストラバーサルを試みる操作 ID を拒否することを確認します。"""
        assert validate_operation_id("../etc/passwd") is False
        assert validate_operation_id("../../secrets") is False
        assert validate_operation_id("/absolute/path") is False

    def test_invalid_operation_id_special_chars(self) -> None:
        """特殊文字を含む操作 ID を拒否することを確認します。"""
        assert validate_operation_id("id with spaces") is False
        assert validate_operation_id("id\nnewline") is False
        assert validate_operation_id("id;injection") is False
        assert validate_operation_id("id<script>") is False

    def test_invalid_operation_id_too_long(self) -> None:
        """長すぎる操作 ID を拒否することを確認します。"""
        assert validate_operation_id("a" * 257) is False

    def test_valid_operation_id_max_length(self) -> None:
        """最大長の操作 ID を受け付けることを確認します。"""
        assert validate_operation_id("a" * 256) is True


class TestExtractOperationId:
    """Operation-Location ヘッダーからの操作 ID 抽出テスト。"""

    def test_extract_from_standard_url(self, mock_operation_id: str) -> None:
        """標準的な Operation-Location URL から操作 ID を抽出できることを確認します。"""
        url = (
            f"http://localhost:5000/formrecognizer/documentModels/prebuilt-read"
            f"/analyzeResults/{mock_operation_id}?api-version=2024-11-30"
        )
        result = _extract_operation_id_from_location(url)
        assert result == mock_operation_id

    def test_extract_from_url_without_query(self, mock_operation_id: str) -> None:
        """クエリパラメーターなしの URL から操作 ID を抽出できることを確認します。"""
        url = (
            f"http://localhost:5000/formrecognizer/documentModels/prebuilt-read"
            f"/analyzeResults/{mock_operation_id}"
        )
        result = _extract_operation_id_from_location(url)
        assert result == mock_operation_id

    def test_extract_returns_none_for_invalid_url(self) -> None:
        """無効な URL から None を返すことを確認します。"""
        result = _extract_operation_id_from_location("http://localhost:5000/invalid/path")
        assert result is None

    def test_extract_returns_none_for_empty_string(self) -> None:
        """空文字列から None を返すことを確認します。"""
        result = _extract_operation_id_from_location("")
        assert result is None

    def test_extract_rejects_invalid_operation_id_in_url(self) -> None:
        """無効な操作 ID を含む URL から None を返すことを確認します。"""
        url = "http://localhost:5000/formrecognizer/documentModels/prebuilt-read/analyzeResults/../secrets"
        result = _extract_operation_id_from_location(url)
        assert result is None


class TestJobSubmit:
    """ジョブ送信エンドポイントのテストスイート。"""

    @pytest.mark.asyncio
    async def test_submit_returns_202(
        self,
        async_client: AsyncClient,
        sample_pdf_content: bytes,
        mock_operation_id: str,
        operation_location_header: str,
    ) -> None:
        """ジョブ送信成功時に HTTP 202 を返すことを確認します。"""
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
        assert f"/ocr/jobs/{mock_operation_id}" in data["status_url"]
        assert "message" in data

    @pytest.mark.asyncio
    async def test_submit_container_unreachable_returns_502(
        self, async_client: AsyncClient, sample_pdf_content: bytes
    ) -> None:
        """コンテナーへの接続失敗時に HTTP 502 を返すことを確認します。"""
        import httpx

        with respx.mock:
            respx.post(
                "http://localhost:5000/formrecognizer/documentModels/prebuilt-read:analyze"
            ).mock(side_effect=httpx.ConnectError("Connection refused"))

            response = await async_client.post(
                "/api/v1/ocr/jobs",
                files={"file": ("test.pdf", sample_pdf_content, "application/pdf")},
            )

        assert response.status_code == 502
        data = response.json()
        assert data["detail"]["code"] == "CONTAINER_UNREACHABLE"

    @pytest.mark.asyncio
    async def test_submit_container_timeout_returns_504(
        self, async_client: AsyncClient, sample_pdf_content: bytes
    ) -> None:
        """コンテナーへの接続タイムアウト時に HTTP 504 を返すことを確認します。"""
        import httpx

        with respx.mock:
            respx.post(
                "http://localhost:5000/formrecognizer/documentModels/prebuilt-read:analyze"
            ).mock(side_effect=httpx.TimeoutException("Timeout"))

            response = await async_client.post(
                "/api/v1/ocr/jobs",
                files={"file": ("test.pdf", sample_pdf_content, "application/pdf")},
            )

        assert response.status_code == 504
        data = response.json()
        assert data["detail"]["code"] == "CONTAINER_TIMEOUT"

    @pytest.mark.asyncio
    async def test_submit_container_returns_500(
        self, async_client: AsyncClient, sample_pdf_content: bytes
    ) -> None:
        """コンテナーが 500 エラーを返した場合、HTTP 502 を返すことを確認します。"""
        with respx.mock:
            respx.post(
                "http://localhost:5000/formrecognizer/documentModels/prebuilt-read:analyze"
            ).mock(return_value=Response(500))

            response = await async_client.post(
                "/api/v1/ocr/jobs",
                files={"file": ("test.pdf", sample_pdf_content, "application/pdf")},
            )

        assert response.status_code == 502
        data = response.json()
        assert data["detail"]["code"] == "INVALID_CONTAINER_RESPONSE"

    @pytest.mark.asyncio
    async def test_submit_missing_operation_location_header(
        self, async_client: AsyncClient, sample_pdf_content: bytes
    ) -> None:
        """Operation-Location ヘッダーがない場合、HTTP 502 を返すことを確認します。"""
        with respx.mock:
            respx.post(
                "http://localhost:5000/formrecognizer/documentModels/prebuilt-read:analyze"
            ).mock(return_value=Response(202))  # ヘッダーなし

            response = await async_client.post(
                "/api/v1/ocr/jobs",
                files={"file": ("test.pdf", sample_pdf_content, "application/pdf")},
            )

        assert response.status_code == 502


class TestJobPolling:
    """ジョブポーリングエンドポイントのテストスイート。"""

    @pytest.mark.asyncio
    async def test_poll_running_job(
        self,
        async_client: AsyncClient,
        mock_operation_id: str,
        running_result: dict,
    ) -> None:
        """実行中のジョブのステータスが running であることを確認します。"""
        with respx.mock:
            respx.get(
                f"http://localhost:5000/formrecognizer/documentModels/prebuilt-read"
                f"/analyzeResults/{mock_operation_id}"
            ).mock(return_value=Response(200, json=running_result))

            response = await async_client.get(
                f"/api/v1/ocr/jobs/{mock_operation_id}"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert data["result"] is None
        assert data["error"] is None

    @pytest.mark.asyncio
    async def test_poll_succeeded_job(
        self,
        async_client: AsyncClient,
        mock_operation_id: str,
        succeeded_result: dict,
    ) -> None:
        """成功したジョブの結果が返されることを確認します。"""
        with respx.mock:
            respx.get(
                f"http://localhost:5000/formrecognizer/documentModels/prebuilt-read"
                f"/analyzeResults/{mock_operation_id}"
            ).mock(return_value=Response(200, json=succeeded_result))

            response = await async_client.get(
                f"/api/v1/ocr/jobs/{mock_operation_id}"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "succeeded"
        assert data["result"] is not None
        assert data["error"] is None

    @pytest.mark.asyncio
    async def test_poll_failed_job(
        self,
        async_client: AsyncClient,
        mock_operation_id: str,
        failed_result: dict,
    ) -> None:
        """失敗したジョブのエラー情報が返されることを確認します。"""
        with respx.mock:
            respx.get(
                f"http://localhost:5000/formrecognizer/documentModels/prebuilt-read"
                f"/analyzeResults/{mock_operation_id}"
            ).mock(return_value=Response(200, json=failed_result))

            response = await async_client.get(
                f"/api/v1/ocr/jobs/{mock_operation_id}"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["error"] is not None
        assert data["result"] is None

    @pytest.mark.asyncio
    async def test_poll_invalid_operation_id_returns_400(
        self, async_client: AsyncClient
    ) -> None:
        """無効な操作 ID で HTTP 400 を返すことを確認します。"""
        # パストラバーサルを含む無効な ID
        response = await async_client.get("/api/v1/ocr/jobs/../../etc/passwd")
        assert response.status_code in (400, 404, 422)

    @pytest.mark.asyncio
    async def test_poll_malformed_operation_id_returns_400(
        self, async_client: AsyncClient
    ) -> None:
        """不正な操作 ID で HTTP 400 を返すことを確認します。"""
        response = await async_client.get("/api/v1/ocr/jobs/id with spaces")
        # URL エンコードされるため 404 または 400 が返される
        assert response.status_code in (400, 404, 422)

    @pytest.mark.asyncio
    async def test_poll_job_not_found_returns_404(
        self, async_client: AsyncClient
    ) -> None:
        """存在しないジョブで HTTP 404 を返すことを確認します。"""
        with respx.mock:
            respx.get(
                "http://localhost:5000/formrecognizer/documentModels/prebuilt-read"
                "/analyzeResults/nonexistent-job-id"
            ).mock(return_value=Response(404))

            response = await async_client.get(
                "/api/v1/ocr/jobs/nonexistent-job-id"
            )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "JOB_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_poll_container_unreachable_returns_502(
        self, async_client: AsyncClient, mock_operation_id: str
    ) -> None:
        """コンテナーへの接続失敗時に HTTP 502 を返すことを確認します。"""
        import httpx

        with respx.mock:
            respx.get(
                f"http://localhost:5000/formrecognizer/documentModels/prebuilt-read"
                f"/analyzeResults/{mock_operation_id}"
            ).mock(side_effect=httpx.ConnectError("Connection refused"))

            response = await async_client.get(
                f"/api/v1/ocr/jobs/{mock_operation_id}"
            )

        assert response.status_code == 502
        data = response.json()
        assert data["detail"]["code"] == "CONTAINER_UNREACHABLE"


class TestSyncJobEndpoint:
    """PoC 同期エンドポイントのテストスイート。"""

    @pytest.mark.asyncio
    async def test_sync_endpoint_succeeds(
        self,
        async_client: AsyncClient,
        sample_pdf_content: bytes,
        mock_operation_id: str,
        operation_location_header: str,
        succeeded_result: dict,
    ) -> None:
        """同期エンドポイントで OCR 完了結果を受け取れることを確認します。"""
        with respx.mock:
            respx.post(
                "http://localhost:5000/formrecognizer/documentModels/prebuilt-read:analyze"
            ).mock(
                return_value=Response(
                    202,
                    headers={"Operation-Location": operation_location_header},
                )
            )
            respx.get(
                f"http://localhost:5000/formrecognizer/documentModels/prebuilt-read"
                f"/analyzeResults/{mock_operation_id}"
            ).mock(return_value=Response(200, json=succeeded_result))

            response = await async_client.post(
                "/api/v1/ocr/jobs/sync",
                files={"file": ("test.pdf", sample_pdf_content, "application/pdf")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "succeeded"
        assert data["result"] is not None

    @pytest.mark.asyncio
    async def test_sync_endpoint_timeout(
        self,
        async_client: AsyncClient,
        sample_pdf_content: bytes,
        mock_operation_id: str,
        operation_location_header: str,
        running_result: dict,
    ) -> None:
        """同期エンドポイントでタイムアウトが発生することを確認します。"""
        # テスト設定の sync_timeout_seconds=5.0 秒以内に完了しない

        with respx.mock:
            respx.post(
                "http://localhost:5000/formrecognizer/documentModels/prebuilt-read:analyze"
            ).mock(
                return_value=Response(
                    202,
                    headers={"Operation-Location": operation_location_header},
                )
            )
            # 常に running を返してタイムアウトを引き起こす
            respx.get(
                f"http://localhost:5000/formrecognizer/documentModels/prebuilt-read"
                f"/analyzeResults/{mock_operation_id}"
            ).mock(return_value=Response(200, json=running_result))

            response = await async_client.post(
                "/api/v1/ocr/jobs/sync",
                files={"file": ("test.pdf", sample_pdf_content, "application/pdf")},
            )

        assert response.status_code == 408
        data = response.json()
        assert data["detail"]["code"] == "APP_TIMEOUT"
        # タイムアウト後も操作 ID が含まれることを確認
        assert mock_operation_id in data["detail"]["message"]
