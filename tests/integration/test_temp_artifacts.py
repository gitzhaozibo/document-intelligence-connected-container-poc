"""テスト検証用成果物の一時保存に関する結合テスト。"""

import json
from pathlib import Path

import pytest
import respx
from httpx import AsyncClient, Response

from app.config import Settings
from app.extraction import FinancialSummaryExtractor
from app.models import ExtractedField


def _directories(settings: Settings) -> list[Path]:
    return sorted(path for path in settings.temp_dir.iterdir() if path.is_dir())


def _json(directory: Path, filename: str) -> object:
    return json.loads((directory / filename).read_text(encoding="utf-8"))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_async_pdf_saves_upload_and_result(
    async_client: AsyncClient,
    test_settings: Settings,
    sample_pdf_content: bytes,
    mock_operation_id: str,
    operation_location_header: str,
    succeeded_result: dict,
) -> None:
    with respx.mock:
        respx.post(
            "http://localhost:5000/documentintelligence/documentModels/prebuilt-read:analyze"
        ).mock(
            return_value=Response(
                202,
                headers={"Operation-Location": operation_location_header},
            )
        )
        respx.get(
            "http://localhost:5000/documentintelligence/documentModels/prebuilt-read"
            f"/analyzeResults/{mock_operation_id}"
        ).mock(return_value=Response(200, json=succeeded_result))
        submitted = await async_client.post(
            "/api/v1/ocr/jobs",
            files={"file": ("same.pdf", sample_pdf_content, "application/pdf")},
        )
        completed = await async_client.get(
            f"/api/v1/ocr/jobs/{submitted.json()['job_id']}"
        )

    assert completed.status_code == 200
    directory = _directories(test_settings)[0]
    assert (directory / "input.pdf").read_bytes() == sample_pdf_content
    metadata = _json(directory, "metadata.json")
    assert metadata["filename"] == "same.pdf"
    assert metadata["operation_id"] == mock_operation_id
    assert _json(directory, "document_intelligence.json") == succeeded_result
    assert _json(directory, "final_response.json")["status"] == "succeeded"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_named_pdfs_use_distinct_directories(
    async_client: AsyncClient,
    test_settings: Settings,
    sample_pdf_content: bytes,
    operation_location_header: str,
) -> None:
    with respx.mock:
        respx.post(
            "http://localhost:5000/documentintelligence/documentModels/prebuilt-read:analyze"
        ).mock(
            return_value=Response(
                202,
                headers={"Operation-Location": operation_location_header},
            )
        )
        for _ in range(2):
            response = await async_client.post(
                "/api/v1/ocr/jobs",
                files={"file": ("same.pdf", sample_pdf_content, "application/pdf")},
            )
            assert response.status_code == 202

    directories = _directories(test_settings)
    assert len(directories) == 2
    assert directories[0] != directories[1]
    assert all((directory / "input.pdf").exists() for directory in directories)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_intelligence_failure_saves_error_artifact(
    async_client: AsyncClient,
    test_settings: Settings,
    sample_pdf_content: bytes,
) -> None:
    with respx.mock:
        respx.post(
            "http://localhost:5000/documentintelligence/documentModels/prebuilt-read:analyze"
        ).mock(return_value=Response(500, json={"error": {"code": "InternalError"}}))
        response = await async_client.post(
            "/api/v1/ocr/jobs",
            files={"file": ("summary.pdf", sample_pdf_content, "application/pdf")},
        )

    assert response.status_code == 502
    error = _json(_directories(test_settings)[0], "error.json")
    assert error["stage"] == "document_intelligence"
    assert error["code"] == "INVALID_CONTAINER_RESPONSE"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_financial_summary_saves_gpt_artifacts_and_cache(
    async_client: AsyncClient,
    test_settings: Settings,
    sample_pdf_content: bytes,
    mock_operation_id: str,
    operation_location_header: str,
    succeeded_result: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_extract(
        self: FinancialSummaryExtractor, _regions: object
    ) -> list[ExtractedField]:
        self.last_payload = {
            "company_name": {"value": "株式会社サンプル", "source_ids": []}
        }
        return [
            ExtractedField(name="company_name", label="会社名", value="株式会社サンプル"),
            ExtractedField(name="securities_code", label="コード", value=None),
            ExtractedField(name="fiscal_period", label="決算期", value=None),
        ]

    monkeypatch.setattr(FinancialSummaryExtractor, "extract", fake_extract)
    with respx.mock:
        respx.post(
            "http://localhost:5000/documentintelligence/documentModels/prebuilt-read:analyze"
        ).mock(
            return_value=Response(
                202,
                headers={"Operation-Location": operation_location_header},
            )
        )
        respx.get(
            "http://localhost:5000/documentintelligence/documentModels/prebuilt-read"
            f"/analyzeResults/{mock_operation_id}"
        ).mock(return_value=Response(200, json=succeeded_result))
        first = await async_client.post(
            "/api/v1/financial-summary/extract",
            files={"file": ("summary.pdf", sample_pdf_content, "application/pdf")},
        )
        second = await async_client.post(
            "/api/v1/financial-summary/extract",
            files={"file": ("summary.pdf", sample_pdf_content, "application/pdf")},
        )

    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    directories = _directories(test_settings)
    assert len(directories) == 2
    fresh = next(
        directory
        for directory in directories
        if not _json(directory, "final_response.json")["cache_hit"]
    )
    cached = next(
        directory
        for directory in directories
        if _json(directory, "final_response.json")["cache_hit"]
    )
    assert _json(fresh, "gpt_result.json")["company_name"]["value"] == "株式会社サンプル"
    assert _json(cached, "gpt_result.json")["cache_hit"] is True
    for directory in directories:
        assert (directory / "document_intelligence.json").exists()
        assert (directory / "source_regions.json").exists()
        assert (directory / "final_response.json").exists()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gpt_failure_saves_error_artifact(
    async_client: AsyncClient,
    test_settings: Settings,
    sample_pdf_content: bytes,
    mock_operation_id: str,
    operation_location_header: str,
    succeeded_result: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_extract(
        _self: FinancialSummaryExtractor, _regions: object
    ) -> list[ExtractedField]:
        raise ValueError("テスト用 GPT 応答エラー")

    monkeypatch.setattr(FinancialSummaryExtractor, "extract", fail_extract)
    with respx.mock:
        respx.post(
            "http://localhost:5000/documentintelligence/documentModels/prebuilt-read:analyze"
        ).mock(
            return_value=Response(
                202,
                headers={"Operation-Location": operation_location_header},
            )
        )
        respx.get(
            "http://localhost:5000/documentintelligence/documentModels/prebuilt-read"
            f"/analyzeResults/{mock_operation_id}"
        ).mock(return_value=Response(200, json=succeeded_result))
        response = await async_client.post(
            "/api/v1/financial-summary/extract",
            files={"file": ("summary.pdf", sample_pdf_content, "application/pdf")},
        )

    assert response.status_code == 502
    error = _json(_directories(test_settings)[0], "error.json")
    assert error["stage"] == "extraction"
    assert error["code"] == "EXTRACTION_FAILED"
