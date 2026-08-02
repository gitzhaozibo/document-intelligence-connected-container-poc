"""ドキュメント解析結果と TraceLog の永続化。"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import AnalysisResult, Document, TraceLog


class AnalysisRepository:
    """解析処理に必要な DB 操作を提供します。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def get_or_create_document(
        self, *, digest: str, filename: str, content_type: str, content: bytes
    ) -> Document:
        async with self._sessions() as session:
            existing = await session.scalar(select(Document).where(Document.sha256 == digest))
            if existing:
                return existing
            document = Document(
                sha256=digest,
                filename=filename[:255] or "document.pdf",
                content_type=content_type,
                size_bytes=len(content),
                content=content,
            )
            session.add(document)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(select(Document).where(Document.sha256 == digest))
                if existing:
                    return existing
                raise
            await session.refresh(document)
            return document

    async def claim_analysis(
        self, document_id: UUID, processing_version: str
    ) -> tuple[AnalysisResult, bool]:
        async with self._sessions() as session:
            existing = await session.scalar(
                select(AnalysisResult).where(
                    AnalysisResult.document_id == document_id,
                    AnalysisResult.processing_version == processing_version,
                )
            )
            if existing:
                if existing.status == "failed":
                    existing.status = "processing"
                    await session.commit()
                    return existing, True
                return existing, False

            result = AnalysisResult(
                document_id=document_id,
                processing_version=processing_version,
                status="processing",
            )
            session.add(result)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(AnalysisResult).where(
                        AnalysisResult.document_id == document_id,
                        AnalysisResult.processing_version == processing_version,
                    )
                )
                if existing:
                    return existing, False
                raise
            await session.refresh(result)
            return result, True

    async def wait_for_analysis(
        self, analysis_id: UUID, timeout_seconds: float, poll_interval: float
    ) -> AnalysisResult:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            async with self._sessions() as session:
                result = await session.get(AnalysisResult, analysis_id)
                if result and result.status != "processing":
                    return result
            await asyncio.sleep(poll_interval)
        raise TimeoutError("同一 PDF の解析完了待機がタイムアウトしました。")

    async def complete_analysis(
        self,
        analysis_id: UUID,
        *,
        ocr_result: dict[str, Any],
        fields: list[dict[str, Any]],
    ) -> AnalysisResult:
        values = {field["name"]: field.get("value") for field in fields}
        async with self._sessions() as session:
            result = await session.get(AnalysisResult, analysis_id)
            if result is None:
                raise LookupError("解析結果が見つかりません。")
            result.status = "succeeded"
            result.ocr_text = str(ocr_result.get("content") or "")
            result.ocr_result = ocr_result
            result.extracted_fields = fields
            result.company_name = values.get("company_name")
            result.securities_code = values.get("securities_code")
            result.fiscal_period = values.get("fiscal_period")
            await session.commit()
            await session.refresh(result)
            return result

    async def fail_analysis(self, analysis_id: UUID) -> None:
        async with self._sessions() as session:
            result = await session.get(AnalysisResult, analysis_id)
            if result:
                result.status = "failed"
                await session.commit()

    async def get_export_data(
        self, document_id: UUID, processing_version: str
    ) -> tuple[Document, AnalysisResult] | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(Document, AnalysisResult)
                    .join(AnalysisResult)
                    .where(
                        Document.id == document_id,
                        AnalysisResult.processing_version == processing_version,
                        AnalysisResult.status == "succeeded",
                    )
                )
            ).one_or_none()
            return (row[0], row[1]) if row else None

    async def start_trace(self, document_id: UUID, process_type: str) -> TraceLog:
        async with self._sessions() as session:
            trace = TraceLog(document_id=document_id, process_type=process_type)
            session.add(trace)
            await session.commit()
            await session.refresh(trace)
            return trace

    async def finish_trace(
        self,
        trace_id: UUID,
        *,
        status: str,
        total_ms: float,
        cache_hit: bool = False,
        operation_id: str | None = None,
        ocr_ms: float | None = None,
        extraction_ms: float | None = None,
        db_ms: float | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        async with self._sessions() as session:
            trace = await session.get(TraceLog, trace_id)
            if trace is None:
                return
            trace.status = status
            trace.finished_at = datetime.now(UTC)
            trace.total_ms = total_ms
            trace.cache_hit = cache_hit
            trace.operation_id = operation_id
            trace.ocr_ms = ocr_ms
            trace.extraction_ms = extraction_ms
            trace.db_ms = db_ms
            trace.error_code = error_code
            trace.error_message = error_message[:500] if error_message else None
            await session.commit()
