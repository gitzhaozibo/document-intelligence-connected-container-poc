# ===================================================================
# FastAPI アプリケーション Dockerfile
# ===================================================================
FROM python:3.12-slim

# セキュリティ: 非 root ユーザーで実行
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# 依存関係のインストール（キャッシュ最適化のため先にコピー）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションコードのコピー
COPY app/ ./app/
COPY alembic.ini .
COPY alembic/ ./alembic/

# 非 root ユーザーに切り替え
USER appuser

# ポート公開
EXPOSE 8000

# ヘルスチェック
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

# アプリケーション起動
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
