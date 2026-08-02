"""
API リクエスト・レスポンス用 Pydantic モデル。
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """OCR ジョブのステータス。"""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_STARTED = "notStarted"


class JobSubmitResponse(BaseModel):
    """ジョブ送信成功時のレスポンス（HTTP 202）。"""

    job_id: str = Field(description="ジョブ ID（ポーリングに使用）")
    status_url: str = Field(description="ステータス確認用 URL")
    message: str = Field(default="ジョブを受け付けました。status_url でステータスを確認してください。")


class JobStatusResponse(BaseModel):
    """ジョブステータス確認のレスポンス。"""

    job_id: str = Field(description="ジョブ ID")
    status: JobStatus = Field(description="現在のジョブステータス")
    result: dict[str, Any] | None = Field(
        default=None,
        description="OCR 完了時の結果（succeeded の場合のみ）",
    )
    error: dict[str, Any] | None = Field(
        default=None,
        description="エラー情報（failed の場合のみ）",
    )


class HealthStatus(str, Enum):
    """ヘルスチェックのステータス。"""

    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"


class ContainerHealth(BaseModel):
    """Read コンテナーのヘルス情報。"""

    reachable: bool = Field(description="コンテナーへの接続可否")
    status: str | None = Field(default=None, description="コンテナーの報告するステータス")
    message: str | None = Field(default=None, description="詳細メッセージ")


class HealthResponse(BaseModel):
    """ヘルスチェックエンドポイントのレスポンス。"""

    status: HealthStatus = Field(description="全体のヘルスステータス")
    fastapi: str = Field(default="ok", description="FastAPI サービスのステータス")
    container: ContainerHealth = Field(description="Read コンテナーのヘルス情報")


class ErrorDetail(BaseModel):
    """エラーレスポンスの詳細。"""

    code: str = Field(description="エラーコード")
    message: str = Field(description="ユーザー向けエラーメッセージ")
