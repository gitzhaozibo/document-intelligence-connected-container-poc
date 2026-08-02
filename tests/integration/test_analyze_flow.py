"""FastAPI と Document Intelligence クライアント間の結合テスト。"""

import pytest
import respx
from httpx import AsyncClient, Response


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_flow_forwards_analyze_options(
    async_client: AsyncClient,
    sample_pdf_content: bytes,
    mock_operation_id: str,
    operation_location_header: str,
    succeeded_result: dict,
) -> None:
    with respx.mock:
        submit_route = respx.post(
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
            data={
                "pages": "1-3,5",
                "locale": "ja-JP",
                "features": "languages,ocrHighResolution",
                "output_content_format": "markdown",
            },
        )

    assert response.status_code == 200
    params = submit_route.calls[0].request.url.params
    assert params["api-version"] == "2024-11-30"
    assert params["pages"] == "1-3,5"
    assert params["locale"] == "ja-JP"
    assert params["features"] == "languages,ocrHighResolution"
    assert params["outputContentFormat"] == "markdown"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_flow_rejects_invalid_options_before_container_call(
    async_client: AsyncClient,
    sample_pdf_content: bytes,
) -> None:
    with respx.mock:
        response = await async_client.post(
            "/api/v1/ocr/jobs/sync",
            files={"file": ("test.pdf", sample_pdf_content, "application/pdf")},
            data={"pages": "../1"},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_ANALYZE_OPTIONS"
    assert not respx.calls
