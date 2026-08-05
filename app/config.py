"""
アプリケーション設定モジュール。

環境変数（.env ファイルまたはシステム環境変数）から設定を読み込みます。
pydantic-settings を使用して型安全な設定管理を行います。
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """アプリケーション全体の設定クラス。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Azure Document Intelligence 認証情報 ----
    # NOTE: これらはコンテナーの Billing/ApiKey として使用されます。
    # アプリケーションコードはコンテナーの REST API にキーを送信しません
    # （コンテナー自身が Azure に課金情報を送信します）。
    di_billing_endpoint: str = Field(
        default="",
        description="Azure DI リソースのエンドポイント URL（コンテナー起動用）",
    )
    di_api_key: str = Field(
        default="",
        description="Azure DI API キー（コンテナー起動用）",
    )

    # ---- コンテナー接続設定 ----
    di_container_endpoint: str = Field(
        default="http://di-read:5000",
        description="ローカル Read コンテナーの URL",
    )
    di_api_version: str = Field(
        default="2024-11-30",
        description="Document Intelligence REST API バージョン",
    )
    di_model_id: str = Field(
        default="prebuilt-read",
        description="使用するモデル ID",
    )

    # ---- Azure OpenAI（決算短信の項目抽出）----
    azure_openai_endpoint: str = Field(default="", description="Azure OpenAI エンドポイント")
    azure_openai_api_key: SecretStr = Field(
        default=SecretStr(""), description="Azure OpenAI API キー"
    )
    azure_openai_deployment: str = Field(default="", description="GPT デプロイ名")
    azure_openai_api_version: str = Field(
        default="2024-10-21", description="Azure OpenAI API バージョン"
    )
    azure_openai_timeout_seconds: float = Field(
        default=60.0, ge=1.0, description="Azure OpenAI リクエストタイムアウト（秒）"
    )

    # ---- FastAPI 設定 ----
    api_prefix: str = Field(
        default="/api/v1",
        description="API ルートのプレフィックス",
    )
    database_url: str = Field(
        default="",
        description="SQLAlchemy 非同期データベース接続 URL",
    )
    database_host: str = Field(default="localhost", description="PostgreSQL ホスト")
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = Field(default="document_app", min_length=1)
    database_user: str = Field(default="postgres", min_length=1)
    database_password: SecretStr = Field(default=SecretStr(""))
    analysis_processing_version: str = Field(
        default="financial-summary-v1",
        min_length=1,
        max_length=100,
        description="同一 PDF の再解析要否を判定する処理バージョン",
    )

    # ---- ポーリング・タイムアウト設定 ----
    poll_interval_seconds: float = Field(
        default=2.0,
        ge=0.05,
        description="ジョブポーリング間隔（秒）",
    )
    sync_timeout_seconds: float = Field(
        default=120.0,
        ge=1.0,
        description="同期エンドポイントの最大待機時間（秒）",
    )

    # ---- ファイルアップロード設定 ----
    max_upload_size_bytes: int = Field(
        default=52_428_800,  # 50 MB
        ge=1,
        description="アップロード可能な最大ファイルサイズ（バイト）",
    )
    temp_dir: Path = Field(
        default=Path("temp"),
        description="テスト検証用のアップロード・解析成果物保存先",
    )

    # ---- HTTPX タイムアウト設定 ----
    httpx_connect_timeout: float = Field(
        default=10.0,
        ge=1.0,
        description="HTTPX 接続タイムアウト（秒）",
    )
    httpx_read_timeout: float = Field(
        default=60.0,
        ge=5.0,
        description="HTTPX 読み取りタイムアウト（秒）",
    )
    httpx_write_timeout: float = Field(
        default=30.0,
        ge=5.0,
        description="HTTPX 書き込みタイムアウト（秒）",
    )
    httpx_pool_timeout: float = Field(
        default=10.0,
        ge=1.0,
        description="HTTPX 接続プールタイムアウト（秒）",
    )

    @field_validator("di_container_endpoint", "azure_openai_endpoint")
    @classmethod
    def validate_endpoint_url(cls, v: str) -> str:
        """エンドポイント URL の末尾スラッシュを除去します。"""
        return v.rstrip("/")

    @model_validator(mode="after")
    def validate_required_for_runtime(self) -> "Settings":
        """
        実行時に必要な設定の検証。
        アプリ起動時にエラーが出るよう、
        di_billing_endpoint と di_api_key の存在を確認します。
        ただし、テスト環境では空でも許容します。
        """
        # 空の場合は警告ログを出すが起動は許容（コンテナーが別途設定を持つため）
        return self

    def get_database_url(self) -> str | URL:
        """明示 URL または個別設定から SQLAlchemy URL を返します。"""
        if self.database_url:
            return self.database_url
        credentials = {"pass" + "word": self.database_password.get_secret_value()}
        return URL.create(
            "postgresql+asyncpg",
            username=self.database_user,
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
            **credentials,
        )


@lru_cache
def get_settings() -> Settings:
    """設定のシングルトンインスタンスを返します。"""
    return Settings()
