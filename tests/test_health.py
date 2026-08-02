"""
ヘルスチェックエンドポイントのテスト。

/health エンドポイントの動作を検証します。
"""

import pytest
import respx
from httpx import AsyncClient, Response


class TestHealthEndpoint:
    """ヘルスチェックエンドポイントのテストスイート。"""

    @pytest.mark.asyncio
    async def test_health_ok_when_container_reachable(
        self, async_client: AsyncClient
    ) -> None:
        """コンテナーが到達可能な場合、status=ok を返すことを確認します。"""
        with respx.mock:
            respx.get("http://localhost:5000/status").mock(
                return_value=Response(200, json={"status": "ready"})
            )

            response = await async_client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["fastapi"] == "ok"
        assert data["container"]["reachable"] is True
        assert data["container"]["status"] == "ready"

    @pytest.mark.asyncio
    async def test_health_degraded_when_container_unreachable(
        self, async_client: AsyncClient
    ) -> None:
        """コンテナーへの接続が失敗した場合、status=degraded を返すことを確認します。"""
        import httpx

        with respx.mock:
            respx.get("http://localhost:5000/status").mock(
                side_effect=httpx.ConnectError("Connection refused")
            )

            response = await async_client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["fastapi"] == "ok"
        assert data["container"]["reachable"] is False
        assert data["container"]["message"] is not None

    @pytest.mark.asyncio
    async def test_health_degraded_when_container_timeout(
        self, async_client: AsyncClient
    ) -> None:
        """コンテナーへの接続がタイムアウトした場合、status=degraded を返すことを確認します。"""
        import httpx

        with respx.mock:
            respx.get("http://localhost:5000/status").mock(
                side_effect=httpx.TimeoutException("Timeout")
            )

            response = await async_client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["container"]["reachable"] is False

    @pytest.mark.asyncio
    async def test_health_container_returns_non_200(
        self, async_client: AsyncClient
    ) -> None:
        """コンテナーが非 200 を返した場合、reachable=True だが status は http_xxx であることを確認します。"""
        with respx.mock:
            respx.get("http://localhost:5000/status").mock(
                return_value=Response(503)
            )

            response = await async_client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        # コンテナーには到達できているが、ステータスが異常
        assert data["container"]["reachable"] is True
        assert "503" in data["container"]["status"]

    @pytest.mark.asyncio
    async def test_health_response_schema(self, async_client: AsyncClient) -> None:
        """ヘルスレスポンスのスキーマが正しいことを確認します。"""
        with respx.mock:
            respx.get("http://localhost:5000/status").mock(
                return_value=Response(200, json={"status": "ready"})
            )

            response = await async_client.get("/api/v1/health")

        data = response.json()
        # 必須フィールドの存在確認
        assert "status" in data
        assert "fastapi" in data
        assert "container" in data
        assert "reachable" in data["container"]
