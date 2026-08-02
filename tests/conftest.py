"""
共有テストフィクスチャ。

Azure や実際の Read コンテナーへの接続を必要としません。
"""

from typing import AsyncGenerator

import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.client import DocumentIntelligenceClient
from app.config import Settings
from app.database import Base
from app.main import create_app
from app.repository import AnalysisRepository


@pytest.fixture
def test_settings() -> Settings:
    """テスト用設定（ダミー認証情報使用）。"""
    return Settings(
        di_billing_endpoint="https://test.cognitiveservices.azure.com/",
        di_api_key="00000000000000000000000000000000",
        di_container_endpoint="http://localhost:5000",
        di_api_version="2024-11-30",
        di_model_id="prebuilt-read",
        api_prefix="/api/v1",
        poll_interval_seconds=0.1,  # テストではポーリング間隔を短く
        sync_timeout_seconds=5.0,
        max_upload_size_bytes=10 * 1024 * 1024,  # 10 MB
        httpx_connect_timeout=5.0,
        httpx_read_timeout=10.0,
        httpx_write_timeout=10.0,
        httpx_pool_timeout=5.0,
        database_url="sqlite+aiosqlite:///:memory:",
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_api_key="test-key",
        azure_openai_deployment="gpt-test",
    )


@pytest_asyncio.fixture
async def db_session_factory(
    test_settings: Settings,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """テストごとに空の SQLite データベースを作成します。"""
    engine = create_async_engine(test_settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_client(
    test_settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    """
    テスト用 FastAPI AsyncClient。

    lifespan をスキップして app.state を直接設定します。
    httpx と respx を使ってアップストリームコンテナーをモックします。
    """
    app = create_app(settings=test_settings)

    # テスト用 DI クライアントを起動して app.state に設定
    # (lifespan を経由せず直接設定することで lifespan のコンテナー接続を回避)
    di_client = DocumentIntelligenceClient(test_settings)
    await di_client.start()
    app.state.di_client = di_client
    app.state.settings = test_settings
    app.state.analysis_repository = AnalysisRepository(db_session_factory)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    await di_client.stop()


@pytest.fixture
def sample_pdf_content() -> bytes:
    """ダミー PDF バイト列（最小限の PDF ヘッダー）。"""
    return b"%PDF-1.4 1 0 obj<</Type/Catalog>>endobj\n%%EOF"


@pytest.fixture
def sample_jpeg_content() -> bytes:
    """ダミー JPEG バイト列（最小限の JPEG マーカー）。"""
    # JPEG SOI マーカー + EOI マーカー
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


@pytest.fixture
def mock_operation_id() -> str:
    """テスト用操作 ID。"""
    return "test-operation-12345678-abcd-efgh"


@pytest.fixture
def mock_analyze_url() -> str:
    """テスト用 analyze エンドポイント URL。"""
    return "http://localhost:5000/documentintelligence/documentModels/prebuilt-read:analyze"


@pytest.fixture
def mock_result_url(mock_operation_id: str) -> str:
    """テスト用 analyzeResults エンドポイント URL。"""
    return (
        f"http://localhost:5000/documentintelligence/documentModels/prebuilt-read"
        f"/analyzeResults/{mock_operation_id}"
    )


@pytest.fixture
def operation_location_header(mock_operation_id: str) -> str:
    """テスト用 Operation-Location ヘッダー値。"""
    return (
        f"http://localhost:5000/documentintelligence/documentModels/prebuilt-read"
        f"/analyzeResults/{mock_operation_id}?api-version=2024-11-30"
    )


@pytest.fixture
def succeeded_result(mock_operation_id: str) -> dict:
    """成功した OCR ジョブの結果（コンテンツなし）。"""
    return {
        "status": "succeeded",
        "createdDateTime": "2024-01-01T00:00:00Z",
        "lastUpdatedDateTime": "2024-01-01T00:00:01Z",
        "analyzeResult": {
            "apiVersion": "2024-11-30",
            "modelId": "prebuilt-read",
            "content": "[REDACTED IN TEST]",
        },
    }


@pytest.fixture
def failed_result() -> dict:
    """失敗した OCR ジョブの結果。"""
    return {
        "status": "failed",
        "error": {
            "code": "InvalidRequest",
            "message": "The document could not be processed.",
        },
    }


@pytest.fixture
def running_result() -> dict:
    """実行中の OCR ジョブの結果。"""
    return {
        "status": "running",
    }
