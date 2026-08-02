"""
Azure Document Intelligence Read コンテナー クライアントモジュール。

httpx.AsyncClient を使用してローカルコンテナーの非同期 REST API を呼び出します。
"""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

# セキュリティ: ドキュメント内容・OCR 結果はログに出力しない
# ジョブ ID のバリデーション用パターン（英数字・ハイフン・アンダースコアのみ許可）
_VALID_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,256}$")


def validate_operation_id(operation_id: str) -> bool:
    """操作 ID が安全な形式かどうかを検証します。"""
    return bool(_VALID_OPERATION_ID_RE.match(operation_id))


def _build_analyze_url(settings: Settings) -> str:
    """analyze エンドポイントの URL を構築します。"""
    return (
        f"{settings.di_container_endpoint}"
        f"/formrecognizer/documentModels/{settings.di_model_id}:analyze"
        f"?api-version={settings.di_api_version}"
    )


def _build_analyze_params(
    settings: Settings,
    options: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """analyze リクエストのクエリパラメーターを構築します。"""
    params = {"api-version": settings.di_api_version}
    if options:
        params.update(options)
    return params


def _build_result_url(settings: Settings, operation_id: str) -> str:
    """analyzeResults エンドポイントの URL を構築します。"""
    return (
        f"{settings.di_container_endpoint}"
        f"/formrecognizer/documentModels/{settings.di_model_id}/analyzeResults/{operation_id}"
        f"?api-version={settings.di_api_version}"
    )


def _build_health_url(settings: Settings) -> str:
    """コンテナーのヘルスチェック URL を構築します。

    NOTE: ヘルスエンドポイントのパスはコンテナーイメージのバージョンによって異なる場合があります。
    Microsoft の公式ドキュメントで実際に使用するイメージのエンドポイントを確認してください。
    設定で変更可能です。
    """
    return f"{settings.di_container_endpoint}/status"


def _extract_operation_id_from_location(operation_location: str) -> str | None:
    """
    Operation-Location ヘッダーから操作 ID を抽出します。

    例:
      http://localhost:5000/formrecognizer/documentModels/prebuilt-read/analyzeResults/abc-123?api-version=2024-11-30
      → "abc-123"
    """
    # パス部分から analyzeResults/ 以降を取得
    match = re.search(r"/analyzeResults/([^/?]+)", operation_location)
    if not match:
        return None
    operation_id = match.group(1)
    if not validate_operation_id(operation_id):
        return None
    return operation_id


class DocumentIntelligenceClient:
    """
    ローカル Document Intelligence Read コンテナーへの非同期クライアント。

    httpx.AsyncClient を再利用して効率的な接続管理を行います。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """クライアントを初期化します。アプリケーション起動時に呼び出してください。"""
        timeout = httpx.Timeout(
            connect=self._settings.httpx_connect_timeout,
            read=self._settings.httpx_read_timeout,
            write=self._settings.httpx_write_timeout,
            pool=self._settings.httpx_pool_timeout,
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
        )
        logger.info("DocumentIntelligenceClient を初期化しました。endpoint=%s", self._settings.di_container_endpoint)

    async def stop(self) -> None:
        """クライアントを終了します。アプリケーション終了時に呼び出してください。"""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("DocumentIntelligenceClient を終了しました。")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("クライアントが初期化されていません。start() を呼び出してください。")
        return self._client

    async def submit_document(
        self,
        content: bytes,
        content_type: str,
        options: Mapping[str, str] | None = None,
    ) -> str:
        """
        ドキュメントを Read コンテナーに送信して OCR ジョブを開始します。

        Returns:
            操作 ID 文字列

        Raises:
            httpx.TimeoutException: タイムアウト発生時
            httpx.ConnectError: コンテナーへの接続失敗時
            ValueError: 無効なレスポンス受信時
        """
        client = self._get_client()
        url = _build_analyze_url(self._settings)

        # セキュリティ: ドキュメント内容はログに出力しない
        logger.info("OCR ジョブを送信します。content_type=%s, size=%d bytes", content_type, len(content))

        try:
            response = await client.post(
                url.split("?", maxsplit=1)[0],
                content=content,
                headers={"Content-Type": content_type},
                params=_build_analyze_params(self._settings, options),
            )
        except httpx.TimeoutException as exc:
            logger.warning("コンテナーへの接続がタイムアウトしました。url=%s", url)
            raise exc
        except httpx.ConnectError as exc:
            logger.warning("コンテナーへの接続に失敗しました。url=%s", url)
            raise exc

        if response.status_code != 202:
            logger.warning(
                "コンテナーが予期しないステータスを返しました。status=%d",
                response.status_code,
            )
            raise ValueError(
                f"コンテナーから予期しないステータスコードが返されました: {response.status_code}"
            )

        operation_location = response.headers.get("Operation-Location", "")
        if not operation_location:
            raise ValueError("コンテナーレスポンスに Operation-Location ヘッダーがありません。")

        operation_id = _extract_operation_id_from_location(operation_location)
        if not operation_id:
            raise ValueError(
                "Operation-Location ヘッダーから操作 ID を抽出できませんでした。"
            )

        logger.info("OCR ジョブを受け付けました。operation_id=%s", operation_id)
        return operation_id

    async def get_job_result(self, operation_id: str) -> dict[str, Any]:
        """
        OCR ジョブの結果を取得します。

        Args:
            operation_id: submit_document() が返した操作 ID

        Returns:
            コンテナーからのレスポンス JSON（status, analyzeResult 等を含む）

        Raises:
            httpx.TimeoutException: タイムアウト発生時
            httpx.ConnectError: コンテナーへの接続失敗時
            ValueError: 無効な操作 ID またはレスポンス受信時
        """
        if not validate_operation_id(operation_id):
            raise ValueError(f"無効な操作 ID 形式です: {operation_id!r}")

        client = self._get_client()
        url = _build_result_url(self._settings, operation_id)

        try:
            response = await client.get(url)
        except httpx.TimeoutException as exc:
            logger.warning("ジョブ結果取得がタイムアウトしました。operation_id=%s", operation_id)
            raise exc
        except httpx.ConnectError as exc:
            logger.warning("コンテナーへの接続に失敗しました。operation_id=%s", operation_id)
            raise exc

        if response.status_code == 404:
            raise ValueError(f"指定された操作 ID が見つかりません: {operation_id}")

        if response.status_code != 200:
            logger.warning(
                "ジョブ結果取得で予期しないステータスが返されました。status=%d, operation_id=%s",
                response.status_code,
                operation_id,
            )
            raise ValueError(
                f"コンテナーから予期しないステータスコードが返されました: {response.status_code}"
            )

        try:
            data = response.json()
        except Exception as exc:
            raise ValueError("コンテナーレスポンスの JSON 解析に失敗しました。") from exc

        # セキュリティ: OCR 結果はログに出力しない
        logger.info(
            "ジョブ結果を取得しました。operation_id=%s, status=%s",
            operation_id,
            data.get("status", "unknown"),
        )
        return data

    async def check_health(self) -> dict[str, Any]:
        """
        コンテナーのヘルス状態を確認します。

        NOTE: ヘルスエンドポイントはコンテナーイメージによって異なります。
        使用するイメージの Swagger または Microsoft ドキュメントで確認してください。

        Returns:
            {"reachable": bool, "status": str | None, "message": str | None}
        """
        client = self._get_client()
        url = _build_health_url(self._settings)

        try:
            response = await client.get(url)
            if response.status_code == 200:
                try:
                    data = response.json()
                    container_status = data.get("status", "unknown")
                except Exception:
                    container_status = "unknown"
                return {
                    "reachable": True,
                    "status": container_status,
                    "message": None,
                }
            else:
                return {
                    "reachable": True,
                    "status": f"http_{response.status_code}",
                    "message": f"コンテナーが HTTP {response.status_code} を返しました。",
                }
        except httpx.ConnectError:
            return {
                "reachable": False,
                "status": None,
                "message": "コンテナーへの接続に失敗しました。コンテナーが起動しているか確認してください。",
            }
        except httpx.TimeoutException:
            return {
                "reachable": False,
                "status": None,
                "message": "コンテナーへの接続がタイムアウトしました。",
            }
        except Exception as exc:
            logger.warning("コンテナーヘルスチェックで予期しないエラーが発生しました。error=%s", type(exc).__name__)
            return {
                "reachable": False,
                "status": None,
                "message": "ヘルスチェック中に予期しないエラーが発生しました。",
            }

    async def wait_for_completion(
        self,
        operation_id: str,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> dict[str, Any]:
        """
        OCR ジョブが完了するまでポーリングします（PoC 用同期待機）。

        NOTE: これは PoC 専用のヘルパーメソッドです。
        本番環境では POST /ocr/jobs + GET /ocr/jobs/{id} の非同期フローを推奨します。

        Args:
            operation_id: 操作 ID
            timeout_seconds: アプリケーションレベルの最大待機時間（秒）
                NOTE: このタイムアウトはアプリケーション側の制限です。
                コンテナー側の OCR 処理はキャンセルされません。
            poll_interval_seconds: ポーリング間隔（秒）

        Returns:
            最終的なジョブ結果

        Raises:
            asyncio.TimeoutError: タイムアウト発生時
            ValueError: ジョブが失敗した場合
        """
        deadline = asyncio.get_event_loop().time() + timeout_seconds

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"ジョブ完了を {timeout_seconds} 秒以内に確認できませんでした。"
                    f"operation_id={operation_id}"
                )

            data = await self.get_job_result(operation_id)
            status = data.get("status", "")

            if status == "succeeded":
                return data
            elif status == "failed":
                error = data.get("error", {})
                raise ValueError(
                    f"OCR ジョブが失敗しました。operation_id={operation_id}, "
                    f"error_code={error.get('code', 'unknown')}"
                )
            # "running" または "notStarted" の場合はポーリング継続
            await asyncio.sleep(min(poll_interval_seconds, remaining))


@asynccontextmanager
async def lifespan_client(settings: Settings) -> AsyncGenerator[DocumentIntelligenceClient, None]:
    """FastAPI lifespan で使用するコンテキストマネージャー。"""
    client = DocumentIntelligenceClient(settings)
    await client.start()
    try:
        yield client
    finally:
        await client.stop()
