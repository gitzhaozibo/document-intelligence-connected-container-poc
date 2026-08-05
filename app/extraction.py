"""Azure OpenAI を使用した決算短信の項目抽出。"""

import json
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.models import ExtractedField, SourceRegion

_FIELD_LABELS = {
    "company_name": "会社名",
    "securities_code": "コード",
    "fiscal_period": "決算期",
}


def build_source_regions(analyze_result: dict[str, Any]) -> list[SourceRegion]:
    """Document Intelligence の行情報を GPT と画面表示に使える形へ変換します。"""
    regions: list[SourceRegion] = []
    for page in analyze_result.get("pages") or []:
        width = float(page.get("width") or 0)
        height = float(page.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        page_number = int(page.get("pageNumber") or len(regions) + 1)
        for line in page.get("lines") or []:
            text = str(line.get("content") or "").strip()
            polygon = line.get("polygon") or []
            if not text or len(polygon) != 8:
                continue
            normalized = [
                float(value) / (width if index % 2 == 0 else height)
                for index, value in enumerate(polygon)
            ]
            regions.append(
                SourceRegion(
                    source_id=f"L{len(regions) + 1}",
                    page_number=page_number,
                    text=text,
                    polygon=normalized,
                )
            )
    return regions


class FinancialSummaryExtractor:
    """Azure OpenAI Chat Completions API を呼び出します。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.last_payload: dict[str, Any] | None = None

    async def extract(self, regions: list[SourceRegion]) -> list[ExtractedField]:
        if not self._settings.azure_openai_endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT が設定されていません。")
        if not self._settings.azure_openai_api_key:
            raise ValueError("AZURE_OPENAI_API_KEY が設定されていません。")
        if not self._settings.azure_openai_deployment:
            raise ValueError("AZURE_OPENAI_DEPLOYMENT が設定されていません。")
        if not regions:
            raise ValueError("OCR 結果に位置情報付きのテキストがありません。")

        source_by_id = {region.source_id: region for region in regions}
        document = "\n".join(
            f"{region.source_id} [page {region.page_number}] {region.text}"
            for region in regions
        )
        prompt = (
            "次の決算短信から会社名、証券コード、決算期を抽出してください。"
            "推測せず、根拠となる行IDを必ず指定してください。見つからない値は null、"
            "source_ids は空配列にしてください。"
            'JSON形式 {"company_name":{"value":string|null,"source_ids":[string]},'
            '"securities_code":{"value":string|null,"source_ids":[string]},'
            '"fiscal_period":{"value":string|null,"source_ids":[string]}} '
            "のみを返してください。\n\n"
            f"{document}"
        )
        endpoint = self._settings.azure_openai_endpoint.rstrip("/")
        deployment = quote(self._settings.azure_openai_deployment, safe="")
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions"
        timeout = httpx.Timeout(self._settings.azure_openai_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                params={"api-version": self._settings.azure_openai_api_version},
                headers={
                    "api-key": self._settings.azure_openai_api_key.get_secret_value(),
                    "Content-Type": "application/json",
                },
                json={
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "あなたは日本の決算短信を正確に読み取るアシスタントです。"
                                "指定された JSON 以外は出力しません。"
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()

        try:
            content = response.json()["choices"][0]["message"]["content"]
            payload = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Azure GPT から無効な JSON レスポンスを受信しました。") from exc
        self.last_payload = payload if isinstance(payload, dict) else {}

        fields: list[ExtractedField] = []
        for name, label in _FIELD_LABELS.items():
            item = payload.get(name) if isinstance(payload, dict) else None
            item = item if isinstance(item, dict) else {}
            source_ids = item.get("source_ids")
            valid_ids = source_ids if isinstance(source_ids, list) else []
            sources = [
                source_by_id[source_id]
                for source_id in valid_ids
                if isinstance(source_id, str) and source_id in source_by_id
            ]
            value = item.get("value")
            fields.append(
                ExtractedField(
                    name=name,
                    label=label,
                    value=str(value) if value is not None else None,
                    sources=sources,
                )
            )
        return fields
