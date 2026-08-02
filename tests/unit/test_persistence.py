"""SQLAlchemy 永続化と重複防止の単体テスト。"""

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import AnalysisResult, Document
from app.repository import AnalysisRepository


@pytest.mark.asyncio
async def test_document_and_analysis_claims_are_unique(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = AnalysisRepository(db_session_factory)
    document = await repository.get_or_create_document(
        digest="a" * 64,
        filename="summary.pdf",
        content_type="application/pdf",
        content=b"pdf",
    )
    duplicate = await repository.get_or_create_document(
        digest="a" * 64,
        filename="renamed.pdf",
        content_type="application/pdf",
        content=b"pdf",
    )

    claims = await asyncio.gather(
        repository.claim_analysis(document.id, "v1"),
        repository.claim_analysis(document.id, "v1"),
    )

    assert duplicate.id == document.id
    assert sum(claimed for _, claimed in claims) == 1
    async with db_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Document)) == 1
        assert await session.scalar(select(func.count()).select_from(AnalysisResult)) == 1
