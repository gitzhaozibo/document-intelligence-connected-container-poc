"""決算短信の項目抽出ロジックの単体テスト。"""

import json

import httpx
import pytest
import respx

from app.config import Settings
from app.extraction import FinancialSummaryExtractor, build_source_regions


def test_build_source_regions_normalizes_document_coordinates() -> None:
    regions = build_source_regions(
        {
            "pages": [
                {
                    "pageNumber": 2,
                    "width": 10,
                    "height": 20,
                    "lines": [
                        {
                            "content": "株式会社サンプル",
                            "polygon": [1, 2, 5, 2, 5, 4, 1, 4],
                        }
                    ],
                }
            ]
        }
    )

    assert len(regions) == 1
    assert regions[0].page_number == 2
    assert regions[0].polygon == [0.1, 0.1, 0.5, 0.1, 0.5, 0.2, 0.1, 0.2]


@pytest.mark.asyncio
async def test_extractor_returns_only_grounded_sources() -> None:
    settings = Settings(
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_api_key="test-key",
        azure_openai_deployment="gpt-test",
    )
    regions = build_source_regions(
        {
            "pages": [
                {
                    "pageNumber": 1,
                    "width": 10,
                    "height": 10,
                    "lines": [
                        {
                            "content": "株式会社サンプル",
                            "polygon": [1, 1, 5, 1, 5, 2, 1, 2],
                        }
                    ],
                }
            ]
        }
    )
    response_payload = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"company_name":{"value":"株式会社サンプル","source_ids":["L1"]},'
                        '"securities_code":{"value":"1234","source_ids":["unknown"]},'
                        '"fiscal_period":{"value":null,"source_ids":[]}}'
                    )
                }
            }
        ]
    }

    with respx.mock:
        route = respx.post(
            "https://example.openai.azure.com/openai/deployments/gpt-test/chat/completions"
        ).mock(return_value=httpx.Response(200, json=response_payload))
        fields = await FinancialSummaryExtractor(settings).extract(regions)

    assert route.called
    request_payload = json.loads(route.calls[0].request.content)
    assert "temperature" not in request_payload
    assert fields[0].value == "株式会社サンプル"
    assert fields[0].sources[0].source_id == "L1"
    assert fields[1].value == "1234"
    assert fields[1].sources == []
    assert fields[2].value is None
