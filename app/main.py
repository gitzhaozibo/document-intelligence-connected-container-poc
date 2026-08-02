"""
FastAPI メインアプリケーション。

Azure Document Intelligence Read Connected Container への
ドキュメント送信・結果取得・ヘルスチェックを提供します。

エンドポイント:
  POST   {prefix}/ocr/jobs          — ドキュメントを送信してジョブ ID を返す（HTTP 202）
  GET    {prefix}/ocr/jobs/{id}     — ジョブのステータス/結果を返す
  POST   {prefix}/ocr/jobs/sync     — 完了まで待機する PoC 専用エンドポイント（非推奨）
  GET    {prefix}/health            — FastAPI + コンテナーのヘルス状態を返す
"""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse

from app.client import DocumentIntelligenceClient, lifespan_client
from app.config import Settings, get_settings
from app.extraction import FinancialSummaryExtractor, build_source_regions
from app.models import (
    ContainerHealth,
    ErrorDetail,
    FinancialSummaryResponse,
    HealthResponse,
    HealthStatus,
    JobStatus,
    JobStatusResponse,
    JobSubmitResponse,
)

logger = logging.getLogger(__name__)

# セキュリティ: ドキュメント内容・OCR 結果・API キーはログに出力しない
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

# 許可する MIME タイプ
ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/tiff",
        "image/bmp",
        "image/heif",
    }
)
ALLOWED_FEATURES: frozenset[str] = frozenset(
    {"barcodes", "formulas", "languages", "ocrHighResolution", "styleFont"}
)
ALLOWED_OUTPUT_FORMATS: frozenset[str] = frozenset({"text", "markdown"})
_PAGES_RE = re.compile(r"^[1-9]\d*(?:-[1-9]\d*)?(?:,[1-9]\d*(?:-[1-9]\d*)?)*$")
_LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


def build_analyze_options(
    pages: str | None,
    locale: str | None,
    features: str | None,
    output_content_format: str,
) -> dict[str, str]:
    """フォーム値を検証し、Document Intelligence のオプションへ変換します。"""
    options: dict[str, str] = {}
    normalized_pages = (pages or "").replace(" ", "")
    if normalized_pages:
        if not _PAGES_RE.fullmatch(normalized_pages):
            raise ValueError("ページ指定は 1-3,5 の形式で入力してください。")
        for page_range in normalized_pages.split(","):
            bounds = [int(value) for value in page_range.split("-")]
            if len(bounds) == 2 and bounds[0] > bounds[1]:
                raise ValueError(
                    "ページ範囲の開始ページは終了ページ以下にしてください。"
                )
        options["pages"] = normalized_pages

    normalized_locale = (locale or "").strip()
    if normalized_locale:
        if not _LOCALE_RE.fullmatch(normalized_locale):
            raise ValueError("ロケールは ja-JP のような形式で入力してください。")
        options["locale"] = normalized_locale

    feature_values = (features or "").split(",")
    normalized_features = [value.strip() for value in feature_values if value.strip()]
    invalid_features = sorted(set(normalized_features) - ALLOWED_FEATURES)
    if invalid_features:
        raise ValueError(f"サポートされていない機能です: {', '.join(invalid_features)}")
    if normalized_features:
        options["features"] = ",".join(dict.fromkeys(normalized_features))

    if output_content_format not in ALLOWED_OUTPUT_FORMATS:
        raise ValueError("本文形式は text または markdown を指定してください。")
    options["outputContentFormat"] = output_content_format
    return options


def get_analyze_options(
    pages: Annotated[
        str | None, Form(description="処理対象ページ（例: 1-3,5）")
    ] = None,
    locale: Annotated[
        str | None, Form(description="ドキュメントのロケール（例: ja-JP）")
    ] = None,
    features: Annotated[
        str | None, Form(description="追加機能（カンマ区切り）")
    ] = None,
    output_content_format: Annotated[
        str, Form(description="本文形式: text または markdown")
    ] = "text",
) -> dict[str, str]:
    """multipart/form-data の解析オプションを検証します。"""
    try:
        return build_analyze_options(pages, locale, features, output_content_format)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_ANALYZE_OPTIONS", "message": str(exc)},
        ) from exc


def create_app(settings: Settings | None = None) -> FastAPI:
    """FastAPI アプリケーションのファクトリ関数。"""
    if settings is None:
        settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """アプリケーションのライフサイクル管理。"""
        # 設定の簡易バリデーション
        if not settings.di_billing_endpoint:
            logger.warning(
                "DI_BILLING_ENDPOINT が設定されていません。"
                ".env ファイルを確認してください。"
            )
        if not settings.di_api_key:
            logger.warning(
                "DI_API_KEY が設定されていません。" ".env ファイルを確認してください。"
            )

        async with lifespan_client(settings) as di_client:
            app.state.di_client = di_client
            app.state.settings = settings
            logger.info(
                "アプリケーションを起動しました。prefix=%s, container=%s",
                settings.api_prefix,
                settings.di_container_endpoint,
            )
            yield
        logger.info("アプリケーションを終了しました。")

    app = FastAPI(
        title="Document Intelligence PoC API",
        description=(
            "Azure AI Document Intelligence Read Connected Container への"
            "ドキュメント送信・OCR 結果取得 API。"
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # ---- 依存性注入ヘルパー ----

    def get_di_client(request: Request) -> DocumentIntelligenceClient:
        return request.app.state.di_client

    def get_app_settings(request: Request) -> Settings:
        return request.app.state.settings

    # ---- ヘルスチェックエンドポイント ----

    @app.get(
        f"{settings.api_prefix}/health",
        response_model=HealthResponse,
        summary="FastAPI および Read コンテナーのヘルス状態を返します",
        tags=["health"],
    )
    async def health_check(
        di_client: Annotated[DocumentIntelligenceClient, Depends(get_di_client)],
    ) -> HealthResponse:
        """
        FastAPI サービスのステータスとローカル Read コンテナーへの接続状態を確認します。

        NOTE: コンテナーのヘルスエンドポイント (/status) はイメージによって
        存在しない場合があります。その場合は reachable=false になりますが、
        実際の OCR は動作している可能性があります。
        """
        container_health = await di_client.check_health()
        container = ContainerHealth(
            reachable=container_health["reachable"],
            status=container_health.get("status"),
            message=container_health.get("message"),
        )

        overall_status = (
            HealthStatus.OK if container.reachable else HealthStatus.DEGRADED
        )

        return HealthResponse(
            status=overall_status,
            fastapi="ok",
            container=container,
        )

    # ---- OCR ジョブ送信エンドポイント ----

    @app.post(
        f"{settings.api_prefix}/ocr/jobs",
        response_model=JobSubmitResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="ドキュメントを送信して OCR ジョブを開始します",
        tags=["ocr"],
        responses={
            202: {"description": "ジョブを受け付けました"},
            400: {"model": ErrorDetail, "description": "無効なファイル"},
            413: {"model": ErrorDetail, "description": "ファイルサイズ超過"},
            502: {"model": ErrorDetail, "description": "コンテナー通信エラー"},
            504: {"model": ErrorDetail, "description": "コンテナー接続タイムアウト"},
        },
    )
    async def submit_job(
        file: Annotated[
            UploadFile, File(description="OCR 対象ファイル（PDF または画像）")
        ],
        request: Request,
        analyze_options: Annotated[dict[str, str], Depends(get_analyze_options)],
        di_client: Annotated[DocumentIntelligenceClient, Depends(get_di_client)],
        app_settings: Annotated[Settings, Depends(get_app_settings)],
    ) -> JobSubmitResponse:
        """
        PDF または対応画像ファイルをアップロードして OCR ジョブを開始します。

        処理は非同期で行われます。返却されたジョブ ID を使用して
        GET /ocr/jobs/{job_id} でステータスを確認してください。
        """
        # コンテンツタイプのバリデーション
        content_type = (file.content_type or "").lower().split(";")[0].strip()
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_CONTENT_TYPE",
                    "message": (
                        f"サポートされていないファイル形式です: {content_type}。"
                        f"対応形式: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
                    ),
                },
            )

        # ファイルサイズのバリデーション
        content = await file.read()
        if len(content) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "EMPTY_FILE",
                    "message": "空のファイルはアップロードできません。",
                },
            )
        if len(content) > app_settings.max_upload_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "code": "FILE_TOO_LARGE",
                    "message": (
                        f"ファイルサイズ ({len(content):,} bytes) が上限 "
                        f"({app_settings.max_upload_size_bytes:,} bytes) を超えています。"
                    ),
                },
            )

        # コンテナーへ送信
        try:
            operation_id = await di_client.submit_document(
                content=content,
                content_type=content_type,
                options=analyze_options,
            )
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail={
                    "code": "CONTAINER_TIMEOUT",
                    "message": (
                        "Read コンテナーへの接続がタイムアウトしました。"
                        "コンテナーが起動して準備完了状態になっているか確認してください。"
                    ),
                },
            )
        except httpx.ConnectError:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "CONTAINER_UNREACHABLE",
                    "message": (
                        "Read コンテナーに接続できません。"
                        "Docker Compose が起動しているか確認してください。"
                    ),
                },
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "INVALID_CONTAINER_RESPONSE",
                    "message": f"コンテナーから無効なレスポンスを受け取りました: {exc}",
                },
            )

        status_url = f"{settings.api_prefix}/ocr/jobs/{operation_id}"
        return JobSubmitResponse(
            job_id=operation_id,
            status_url=status_url,
        )

    # ---- OCR ジョブステータス確認エンドポイント ----

    @app.get(
        f"{settings.api_prefix}/ocr/jobs/{{operation_id}}",
        response_model=JobStatusResponse,
        summary="OCR ジョブのステータスまたは結果を取得します",
        tags=["ocr"],
        responses={
            200: {"description": "ジョブステータスまたは結果"},
            400: {"model": ErrorDetail, "description": "無効な操作 ID"},
            404: {"model": ErrorDetail, "description": "ジョブが見つからない"},
            502: {"model": ErrorDetail, "description": "コンテナー通信エラー"},
            504: {"model": ErrorDetail, "description": "コンテナー接続タイムアウト"},
        },
    )
    async def get_job_status(
        operation_id: str,
        di_client: Annotated[DocumentIntelligenceClient, Depends(get_di_client)],
    ) -> JobStatusResponse:
        """
        OCR ジョブのステータスを確認します。

        - status が "running" または "notStarted" の場合は処理中です。再度ポーリングしてください。
        - status が "succeeded" の場合は result に OCR 結果が含まれます。
        - status が "failed" の場合は error にエラー情報が含まれます。
        """
        from app.client import (
            validate_operation_id,
        )  # ローカルインポートで循環参照を避ける

        if not validate_operation_id(operation_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_OPERATION_ID",
                    "message": f"無効な操作 ID 形式です: {operation_id!r}",
                },
            )

        try:
            data = await di_client.get_job_result(operation_id)
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail={
                    "code": "CONTAINER_TIMEOUT",
                    "message": "Read コンテナーへの接続がタイムアウトしました。",
                },
            )
        except httpx.ConnectError:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "CONTAINER_UNREACHABLE",
                    "message": "Read コンテナーに接続できません。",
                },
            )
        except ValueError as exc:
            error_msg = str(exc)
            if "見つかりません" in error_msg:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "JOB_NOT_FOUND",
                        "message": error_msg,
                    },
                )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "INVALID_CONTAINER_RESPONSE",
                    "message": error_msg,
                },
            )

        raw_status = data.get("status", "")
        try:
            job_status = JobStatus(raw_status)
        except ValueError:
            job_status = JobStatus.RUNNING  # 不明なステータスはランニング扱い

        result: dict[str, Any] | None = None
        error: dict[str, Any] | None = None

        if job_status == JobStatus.SUCCEEDED:
            # セキュリティ: analyzeResult の内容はログに出力しない
            result = data.get("analyzeResult")
        elif job_status == JobStatus.FAILED:
            error = data.get("error")

        return JobStatusResponse(
            job_id=operation_id,
            status=job_status,
            result=result,
            error=error,
        )

    # ---- PoC 専用: 同期待機エンドポイント ----

    @app.post(
        f"{settings.api_prefix}/ocr/jobs/sync",
        response_model=JobStatusResponse,
        summary="[PoC 専用] OCR 完了まで待機して結果を返します",
        description=(
            "⚠️ **警告: これは PoC 専用エンドポイントです。本番環境では使用しないでください。**\n\n"
            "ドキュメントを送信し、OCR が完了するまでアプリケーションレベルで待機します。\n"
            "大きなファイルや複数ページのドキュメントでは、クライアント/リバースプロキシの\n"
            "タイムアウトにより接続が切れる場合があります。\n\n"
            "NOTE: このタイムアウトはアプリケーション側の制限です。\n"
            "タイムアウト後もコンテナー側の OCR 処理はキャンセルされません。"
        ),
        tags=["ocr-poc"],
        responses={
            200: {"description": "OCR 完了結果"},
            400: {"model": ErrorDetail, "description": "無効なファイル"},
            408: {"model": ErrorDetail, "description": "アプリケーションタイムアウト"},
            413: {"model": ErrorDetail, "description": "ファイルサイズ超過"},
            502: {"model": ErrorDetail, "description": "コンテナー通信エラー"},
            504: {"model": ErrorDetail, "description": "コンテナー接続タイムアウト"},
        },
    )
    async def submit_job_sync(
        file: Annotated[
            UploadFile, File(description="OCR 対象ファイル（PDF または画像）")
        ],
        request: Request,
        analyze_options: Annotated[dict[str, str], Depends(get_analyze_options)],
        di_client: Annotated[DocumentIntelligenceClient, Depends(get_di_client)],
        app_settings: Annotated[Settings, Depends(get_app_settings)],
    ) -> JobStatusResponse:
        """
        PoC 専用: OCR 完了まで同期的に待機します。

        非同期フロー (POST /ocr/jobs + GET /ocr/jobs/{id}) の使用を推奨します。
        """
        # コンテンツタイプのバリデーション
        content_type = (file.content_type or "").lower().split(";")[0].strip()
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_CONTENT_TYPE",
                    "message": (
                        f"サポートされていないファイル形式です: {content_type}。"
                        f"対応形式: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
                    ),
                },
            )

        content = await file.read()
        if len(content) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "EMPTY_FILE",
                    "message": "空のファイルはアップロードできません。",
                },
            )
        if len(content) > app_settings.max_upload_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "code": "FILE_TOO_LARGE",
                    "message": (
                        f"ファイルサイズ ({len(content):,} bytes) が上限 "
                        f"({app_settings.max_upload_size_bytes:,} bytes) を超えています。"
                    ),
                },
            )

        # ジョブ送信
        try:
            operation_id = await di_client.submit_document(
                content=content,
                content_type=content_type,
                options=analyze_options,
            )
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail={
                    "code": "CONTAINER_TIMEOUT",
                    "message": "Read コンテナーへの接続がタイムアウトしました。",
                },
            )
        except httpx.ConnectError:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "CONTAINER_UNREACHABLE",
                    "message": "Read コンテナーに接続できません。",
                },
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "INVALID_CONTAINER_RESPONSE",
                    "message": str(exc),
                },
            )

        # 完了まで待機
        try:
            data = await di_client.wait_for_completion(
                operation_id=operation_id,
                timeout_seconds=app_settings.sync_timeout_seconds,
                poll_interval_seconds=app_settings.poll_interval_seconds,
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail={
                    "code": "APP_TIMEOUT",
                    "message": (
                        f"アプリケーションの待機タイムアウト ({app_settings.sync_timeout_seconds}s) に達しました。"
                        f"operation_id={operation_id} "
                        "NOTE: コンテナー側の OCR 処理はまだ継続している可能性があります。"
                        "GET /ocr/jobs/{operation_id} で結果を確認できます。"
                    ),
                },
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "OCR_JOB_FAILED",
                    "message": str(exc),
                },
            )

        return JobStatusResponse(
            job_id=operation_id,
            status=JobStatus.SUCCEEDED,
            result=data.get("analyzeResult"),
            error=None,
        )

    @app.post(
        f"{settings.api_prefix}/financial-summary/extract",
        response_model=FinancialSummaryResponse,
        summary="決算短信から会社名、コード、決算期と根拠を抽出します",
        tags=["financial-summary"],
    )
    async def extract_financial_summary(
        file: Annotated[UploadFile, File(description="決算短信 PDF")],
        di_client: Annotated[DocumentIntelligenceClient, Depends(get_di_client)],
        app_settings: Annotated[Settings, Depends(get_app_settings)],
    ) -> FinancialSummaryResponse:
        content_type = (file.content_type or "").lower().split(";")[0].strip()
        if content_type != "application/pdf":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "PDF_REQUIRED", "message": "決算短信 PDF を指定してください。"},
            )
        content = await file.read()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "EMPTY_FILE", "message": "空の PDF はアップロードできません。"},
            )
        if len(content) > app_settings.max_upload_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"code": "FILE_TOO_LARGE", "message": "PDF がサイズ上限を超えています。"},
            )

        try:
            operation_id = await di_client.submit_document(
                content=content,
                content_type=content_type,
                options={"outputContentFormat": "text"},
            )
            data = await di_client.wait_for_completion(
                operation_id=operation_id,
                timeout_seconds=app_settings.sync_timeout_seconds,
                poll_interval_seconds=app_settings.poll_interval_seconds,
            )
            regions = build_source_regions(data.get("analyzeResult") or {})
            fields = await FinancialSummaryExtractor(app_settings).extract(regions)
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail={"code": "APP_TIMEOUT", "message": "PDF の解析がタイムアウトしました。"},
            ) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail={"code": "UPSTREAM_TIMEOUT", "message": "外部サービスがタイムアウトしました。"},
            ) from exc
        except httpx.ConnectError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "UPSTREAM_UNREACHABLE", "message": "外部サービスに接続できません。"},
            ) from exc
        except httpx.HTTPStatusError as exc:
            logger.warning("Azure GPT が HTTP %d を返しました。", exc.response.status_code)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "GPT_ERROR", "message": "Azure GPT による抽出に失敗しました。"},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "EXTRACTION_FAILED", "message": str(exc)},
            ) from exc

        return FinancialSummaryResponse(fields=fields)

    return app


# アプリケーションインスタンスの作成
app = create_app()
