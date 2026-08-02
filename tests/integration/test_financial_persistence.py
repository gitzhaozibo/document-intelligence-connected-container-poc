"""決算短信の永続化、キャッシュ、TraceLog、Excel 出力の結合テスト。"""

from io import BytesIO

import pytest
import respx
from httpx import AsyncClient, Response
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import TraceLog
from app.extraction import FinancialSummaryExtractor
from app.models import ExtractedField


def _mock_ocr(operation_location_header: str, mock_operation_id: str, result: dict) -> None:
    respx.post(
        "http://localhost:5000/documentintelligence/documentModels/prebuilt-read:analyze"
    ).mock(return_value=Response(202, headers={"Operation-Location": operation_location_header}))
    respx.get(
        "http://localhost:5000/documentintelligence/documentModels/prebuilt-read"
        f"/analyzeResults/{mock_operation_id}"
    ).mock(return_value=Response(200, json=result))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_first_analysis_is_persisted_and_second_uses_cache(
    async_client: AsyncClient,
    sample_pdf_content: bytes,
    mock_operation_id: str,
    operation_location_header: str,
    succeeded_result: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_extract(
        _self: FinancialSummaryExtractor, _regions: object
    ) -> list[ExtractedField]:
        return [
            ExtractedField(name="company_name", label="会社名", value="株式会社サンプル"),
            ExtractedField(name="securities_code", label="コード", value="1234"),
            ExtractedField(name="fiscal_period", label="決算期", value="2026年3月期"),
        ]

    monkeypatch.setattr(FinancialSummaryExtractor, "extract", fake_extract)
    with respx.mock:
        _mock_ocr(operation_location_header, mock_operation_id, succeeded_result)
        first = await async_client.post(
            "/api/v1/financial-summary/extract",
            files={"file": ("summary.pdf", sample_pdf_content, "application/pdf")},
        )
        external_call_count = len(respx.calls)
        second = await async_client.post(
            "/api/v1/financial-summary/extract",
            files={"file": ("renamed.pdf", sample_pdf_content, "application/pdf")},
        )
        assert len(respx.calls) == external_call_count

    assert first.status_code == 200
    assert first.json()["cache_hit"] is False
    assert second.status_code == 200
    assert second.json()["cache_hit"] is True
    assert second.json()["document_id"] == first.json()["document_id"]
    excel = await async_client.get(f"/api/v1/financial-summary/{first.json()['document_id']}/excel")
    assert excel.status_code == 200
    rows = list(load_workbook(BytesIO(excel.content), read_only=True)["決算短信"].values)
    assert rows[1] == ("summary.pdf", "株式会社サンプル", "1234", "2026年3月期")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_extraction_failure_is_recorded_without_document_content(
    async_client: AsyncClient,
    sample_pdf_content: bytes,
    mock_operation_id: str,
    operation_location_header: str,
    succeeded_result: dict,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_extract(
        _self: FinancialSummaryExtractor, _regions: object
    ) -> list[ExtractedField]:
        raise ValueError("抽出形式が不正です。")

    monkeypatch.setattr(FinancialSummaryExtractor, "extract", fail_extract)
    with respx.mock:
        _mock_ocr(operation_location_header, mock_operation_id, succeeded_result)
        response = await async_client.post(
            "/api/v1/financial-summary/extract",
            files={"file": ("summary.pdf", sample_pdf_content, "application/pdf")},
        )

    assert response.status_code == 502
    async with db_session_factory() as session:
        trace = (await session.scalars(select(TraceLog))).one()
    assert trace.status == "failed"
    assert trace.error_code == "EXTRACTION_FAILED"
    assert sample_pdf_content.decode() not in (trace.error_message or "")
