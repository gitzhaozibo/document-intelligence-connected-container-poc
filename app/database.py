"""SQLAlchemy のエンジン、セッション、永続化モデル。"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """ORM モデルの基底クラス。"""


class Document(Base):
    """アップロードされた PDF。"""

    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    analyses: Mapped[list["AnalysisResult"]] = relationship(back_populates="document")


class AnalysisResult(Base):
    """OCR と決算情報抽出の結果。"""

    __tablename__ = "analysis_results"
    __table_args__ = (
        UniqueConstraint("document_id", "processing_version", name="uq_analysis_version"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    processing_version: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="processing")
    ocr_text: Mapped[str | None] = mapped_column(Text)
    ocr_result: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    extracted_fields: Mapped[list[dict[str, Any]] | None] = mapped_column(JsonType)
    company_name: Mapped[str | None] = mapped_column(Text)
    securities_code: Mapped[str | None] = mapped_column(String(100))
    fiscal_period: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    document: Mapped[Document] = relationship(back_populates="analyses")


class TraceLog(Base):
    """解析・出力処理の監査用実行履歴。"""

    __tablename__ = "trace_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    process_type: Mapped[str] = mapped_column(String(50))
    operation_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="running")
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_ms: Mapped[float | None] = mapped_column(Float)
    ocr_ms: Mapped[float | None] = mapped_column(Float)
    extraction_ms: Mapped[float | None] = mapped_column(Float)
    db_ms: Mapped[float | None] = mapped_column(Float)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(String(500))


def create_database(
    database_url: str | URL,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """非同期エンジンとセッションファクトリーを作成します。"""
    engine = create_async_engine(database_url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
