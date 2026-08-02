# Azure AI Document Intelligence Read Connected Container PoC

Azure AI Document Intelligence Read **接続コンテナー（Connected Container）** をローカルで動かし、Python FastAPI アプリケーションと連携させる PoC（概念実証）プロジェクトです。

> **重要な注意事項**
> - このリポジトリには実際の Azure 認証情報は含まれていません。
> - `.env` ファイルは `.gitignore` で除外されています。**認証情報を絶対に Git にコミットしないでください。**
> - コンテナーイメージのタグ、API パス、API バージョン、Swagger エンドポイントは、実際にデプロイするイメージで Microsoft の公式ドキュメントを確認してください。

---

## 目次

1. [アーキテクチャとデータフロー](#1-アーキテクチャとデータフロー)
2. [前提条件](#2-前提条件)
3. [Azure リソースの作成](#3-azure-リソースの作成)
4. [Docker Desktop のセットアップ](#4-docker-desktop-のセットアップ)
5. [セットアップと起動手順](#5-セットアップと起動手順)
6. [タイムアウトと非同期処理](#6-タイムアウトと非同期処理)
7. [起動・停止と週末運用](#7-起動停止と週末運用)
8. [トラブルシューティング](#8-トラブルシューティング)
9. [セキュリティ注意事項](#9-セキュリティ注意事項)
10. [クリーンアップ](#10-クリーンアップ)

---

## 1. アーキテクチャとデータフロー

```
ローカル PC（Windows + Docker Desktop）
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  Streamlit (8501) / curl / PowerShell                         │
│       │                                                     │
│       ▼ HTTP                                                │
│  ┌──────────────────┐                                       │
│  │  FastAPI (8000)  │  ← ドキュメント受付・OCR 結果返却        │
│  └──────────────────┘                                       │
│       │ HTTP（Docker 内部ネットワーク）                         │
│       ▼                                                     │
│  ┌──────────────────────────────────┐                       │
│  │  Read Connected Container (5000) │  ← OCR 推論（ローカル） │
│  └──────────────────────────────────┘                       │
│       │ HTTPS（アウトバウンド）                                │
│       ▼                                                     │
└───────│─────────────────────────────────────────────────────┘
        │
        ▼ インターネット
  Azure AI Services（課金・メータリングのみ）
  ※ ドキュメントの内容は Azure に送信されません
```

### データフローの説明

1. **PDF / 画像のアップロード**: クライアントが `POST /api/v1/ocr/jobs` に PDF または画像を送信します。
2. **OCR ジョブの投入**: FastAPI がローカルの Read コンテナーに転送します。
3. **OCR 推論（ローカル）**: Read コンテナーがローカルでモデルを実行して文字認識を行います。**ドキュメントの内容は Azure に送信されません。**
4. **課金情報の送信**: Read コンテナーが処理したページ数などの**課金メタデータのみ**を Azure に送信します。
5. **結果の取得**: クライアントが `GET /api/v1/ocr/jobs/{job_id}` でポーリングして結果を受け取ります。

### 接続コンテナーと切断コンテナーの違い

| 項目 | 接続コンテナー（Connected） | 切断コンテナー（Disconnected） |
|------|--------------------------|------------------------------|
| OCR 推論 | ローカル | ローカル |
| インターネット接続 | **常時必要**（課金情報送信） | 運用時は不要（ライセンス取得時のみ） |
| 料金体系 | 従量課金または月間コミットメント | **年間コミットメント（前払い）** |
| 事前承認 | 不要 | 必要（Microsoft への申請が必要） |
| 対象 | PoC・中小規模 | 高セキュリティ環境・大規模 |

> この PoC は**接続コンテナー**を対象としています。接続コンテナーは Azure へのアウトバウンド HTTPS 通信が必要です。組織のポリシーですべてのアウトバウンド通信が禁止されている場合は使用できません。

---

## 2. 前提条件

### ハードウェア要件

| リソース | 最小（評価用） | 推奨（快適な動作） |
|---------|--------------|-----------------|
| CPU | 4 コア（x64） | 8 コア以上（x64） |
| RAM（PC 全体） | 16 GB | 32 GB 以上 |
| Docker 割り当て RAM | 8 GB | 16 GB 以上 |
| ストレージ（空き容量） | 20 GB | 50 GB 以上 |

> **ARM CPU（例: Qualcomm Snapdragon X）について**: Azure AI Services コンテナーは一般的に x64 アーキテクチャを対象としています。ARM 環境での動作は保証されていません。x64 PC の使用を強く推奨します。

### ソフトウェア要件

- **OS**: Windows 10 バージョン 2004 以降 / Windows 11
- **CPU 仮想化**: BIOS/UEFI で有効化されていること
- **WSL 2**: 有効化済み（インストール手順は[セクション 4](#4-docker-desktop-のセットアップ)を参照）
- **Docker Desktop**: 最新の安定版（Linux コンテナーモード）
- **curl** または **PowerShell 7+**: 動作確認用（コンテナー外で実行する場合）

### Python 環境（コンテナー外でアプリを実行する場合のみ）

- Python 3.12 以上
- pip

---

## 3. Azure リソースの作成

### 3-1. Azure Portal でリソースを作成する

1. [Azure Portal](https://portal.azure.com) にサインインします。
2. 検索バーに「Document Intelligence」と入力して選択します。
3. **「作成」** をクリックします。
4. 以下の項目を入力します：

   | 項目 | 設定値 |
   |------|--------|
   | サブスクリプション | 使用する Azure サブスクリプション |
   | リソースグループ | 新規作成または既存のものを選択 |
   | リージョン | お近くのリージョン（例: Japan East） |
   | 名前 | 任意の一意な名前（例: `my-doc-intel-poc`） |
   | 価格レベル | **Free（F0）** または **Standard（S0）** |

   > **価格レベルについて**: 価格レベルや無料枠の有無はリージョンや時期によって異なります。現在の価格は [Azure 価格ページ](https://azure.microsoft.com/ja-jp/pricing/details/ai-document-intelligence/) で確認してください。コンテナーを使用する場合は **Standard（S0）** 以上が必要です。

5. **「確認および作成」** → **「作成」** をクリックします。
6. デプロイ完了後、**「リソースに移動」** をクリックします。

### 3-2. エンドポイントと API キーの取得

1. 作成したリソースのページで、左メニューの **「キーとエンドポイント」** を選択します。
2. 以下の情報をメモします：
   - **エンドポイント**: `https://your-resource-name.cognitiveservices.azure.com/` の形式
   - **キー 1** または **キー 2**: 32 文字の 16 進数文字列

> ⚠️ **セキュリティ警告**: API キーは絶対に GitHub や他の公開場所に貼り付けないでください。`.env` ファイルに記載してローカルのみで使用してください。

### 3-3. Billing エンドポイントと API キーの対応関係

コンテナーの `Billing` と `ApiKey` は**同じ Azure リソース**のものを使用する必要があります。異なるリソースのものを混在させると、コンテナーが起動しないか課金エラーになります。

### 3-4. コスト管理とアラートの設定（推奨）

PoC 期間中に予期しないコストが発生しないよう、Azure Cost Management でアラートを設定することを強く推奨します。

1. Azure Portal の検索バーで **「Cost Management」** を選択します。
2. **「予算」** → **「追加」** をクリックします。
3. 月間予算額を設定し、80% および 100% に達したときのメールアラートを設定します。

> 接続コンテナーの課金は、コンテナーが処理したページ数に基づきます。PoC では使用後にコンテナーを停止することでコストを抑制できます。最新の価格はリージョンやサブスクリプション条件によって異なるため、Azure Portal の価格計算ツールで見積もることを推奨します。

---

## 4. Docker Desktop のセットアップ

### 4-1. WSL 2 の有効化

PowerShell を**管理者として**開き、以下を実行します：

```powershell
# WSL 2 の有効化と Ubuntu のインストール
wsl --install

# WSL のバージョンを確認
wsl --version

# デフォルトバージョンを WSL 2 に設定（古いバージョンの場合）
wsl --set-default-version 2
```

インストール後に PC を再起動してください。

### 4-2. Docker Desktop のインストール

1. [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) から最新版をダウンロードします。
2. インストーラーを実行します（「Use WSL 2 instead of Hyper-V」のオプションを選択）。
3. インストール後に PC を再起動します。
4. Docker Desktop を起動して、**Linux コンテナーモード**になっていることを確認します。
   - タスクトレイの Docker アイコンを右クリックして「Switch to Linux containers...」が**グレーアウト**（既に Linux モード）になっていれば OK。

### 4-3. Docker Desktop のリソース設定

Docker Desktop の Settings（歯車アイコン）→ **Resources** で以下を設定します：

| 設定項目 | 推奨値 | 説明 |
|---------|--------|------|
| CPU | 8 | Read コンテナー用に最低 4 コア必要 |
| Memory | 16 GB 以上 | Read コンテナーは 8 GB 以上を推奨 |
| Disk image size | 50 GB 以上 | コンテナーイメージ（数 GB）+ データ |

**WSL Integration** タブで、使用する WSL ディストリビューション（例: Ubuntu）を有効化します。

### 4-4. 設定の確認

PowerShell で以下を実行して環境を確認します：

```powershell
# Docker が起動していることを確認
docker version

# Linux コンテナーモードであることを確認（OSType が linux）
docker info | Select-String "OSType"

# CPU アーキテクチャの確認（x64 であること）
docker info | Select-String "Architecture"

# 利用可能なメモリの確認
docker info | Select-String "Total Memory"
```

### 4-5. よくある問題

| 症状 | 原因と対処 |
|------|-----------|
| Docker が起動しない | BIOS で CPU 仮想化が無効。BIOS 設定を確認して有効化してください。 |
| WSL 2 エラー | `wsl --update` を実行して WSL カーネルを更新してください。 |
| メモリ不足でコンテナーが落ちる | Docker Desktop のメモリ割り当てを増やしてください。 |
| 社内 VPN 経由で Pull できない | VPN を一時的に切断して `docker pull` してみてください。TLS インスペクションが原因の場合は IT 部門に確認してください。 |
| DNS 解決エラー | Docker Desktop の Settings → Docker Engine で `"dns": ["8.8.8.8"]` を追加してみてください。 |
| Azure エンドポイントへの接続拒否 | 会社のファイアウォールや Proxy で `*.cognitiveservices.azure.com` への HTTPS (443) が許可されているか確認してください。 |

---

## 5. セットアップと起動手順

### 5-1. リポジトリのクローン

```powershell
git clone https://github.com/gitzhaozibo/document-intelligence-connected-container-poc.git
cd document-intelligence-connected-container-poc
```

### 5-2. 環境変数ファイルの作成

```powershell
# .env.example をコピーして .env を作成
Copy-Item .env.example .env
```

テキストエディターで `.env` を開き、以下の値を設定します：

```env
# Azure Portal の「キーとエンドポイント」から取得した値を設定
DI_BILLING_ENDPOINT=https://your-resource-name.cognitiveservices.azure.com/
DI_API_KEY=your_api_key_here
```

> ⚠️ `.env` ファイルは `.gitignore` で除外されています。**このファイルを Git にコミットしないでください。**

### 5-3. コンテナーイメージの確認（重要）

`compose.yaml` で使用している Read コンテナーイメージのタグを確認します。

```powershell
# 現在設定されているイメージを確認
Select-String "DI_CONTAINER_IMAGE" .env.example
```

> **重要**: コンテナーイメージのタグ（`latest` を含む）、対応する API バージョン、モデル ID、Swagger エンドポイントは、[Microsoft の公式ドキュメント](https://learn.microsoft.com/azure/ai-services/document-intelligence/containers/install-run)で実際に使用するイメージを確認してください。`latest` タグは予告なく変更される場合があります。

### 5-4. サービスの起動

```powershell
# コンテナーイメージをプル（初回は数分かかります）
docker compose pull

# バックグラウンドで起動
docker compose up -d
```

### 5-5. 起動状態の確認

```powershell
# サービスの状態確認
docker compose ps

# ログの確認（Ctrl+C で終了）
docker compose logs -f

# FastAPI のログのみ確認
docker compose logs -f fastapi

# Read コンテナーのログのみ確認（認証情報は含まれません）
docker compose logs -f di-read
```

> **注意**: Read コンテナーは起動後、モデルのロードに数分かかります。ログに `"status": "ready"` が表示されるまで待機してください。

### 5-6. ヘルスチェック

```powershell
# FastAPI のヘルスエンドポイントを確認
Invoke-RestMethod -Uri http://localhost:8000/api/v1/health

# または curl を使用
curl http://localhost:8000/api/v1/health
```

レスポンス例（コンテナー準備完了時）：

```json
{
  "status": "ok",
  "fastapi": "ok",
  "container": {
    "reachable": true,
    "status": "ready",
    "message": null
  }
}
```

### 5-7. Swagger UI を開く

- **Streamlit フロント画面**: http://localhost:8501
- **FastAPI Swagger**: http://localhost:8000/docs
- **FastAPI ReDoc**: http://localhost:8000/redoc
- **Read コンテナー Swagger**: コンテナーイメージによって異なります。Microsoft ドキュメントまたはコンテナーのログで確認してください（例: `http://localhost:5000/formrecognizer/swagger/index.html`）

### 5-8. PDF を送信して OCR を実行する

#### Streamlit フロント画面を使用する場合

1. http://localhost:8501 をブラウザーで開きます。
2. PDF または対応画像をアップロードします。
3. 必要に応じて対象ページ（例: `1-3,5`）、言語ロケール、出力形式、追加解析機能を設定します。
4. **実行**を選択すると、FastAPI 経由で OCR が実行され、抽出テキストと JSON 結果が表示されます。

Streamlit は PoC 用の同期 API を使用します。大きなファイルでは、下記の非同期 API を使用してください。

#### 非同期フロー（推奨）

```powershell
# PDF を送信してジョブを開始（HTTP 202 が返ります）
$result = Invoke-RestMethod `
    -Method Post `
    -Uri http://localhost:8000/api/v1/ocr/jobs `
    -Form @{ file = Get-Item -Path ".\your-document.pdf" }

# ジョブ ID を取得
$jobId = $result.job_id
Write-Host "Job ID: $jobId"

# ステータスをポーリング（succeeded になるまで繰り返す）
do {
    Start-Sleep -Seconds 2
    $status = Invoke-RestMethod `
        -Uri "http://localhost:8000/api/v1/ocr/jobs/$jobId"
    Write-Host "Status: $($status.status)"
} while ($status.status -eq "running" -or $status.status -eq "notStarted")

# 結果を確認
$status | ConvertTo-Json -Depth 10
```

#### curl を使用する場合

```bash
# PDF を送信
JOB_ID=$(curl -s -X POST http://localhost:8000/api/v1/ocr/jobs \
  -F "file=@./your-document.pdf;type=application/pdf" | jq -r '.job_id')

echo "Job ID: $JOB_ID"

# ステータスをポーリング
curl http://localhost:8000/api/v1/ocr/jobs/$JOB_ID | jq '.status'
```

#### PoC 専用: 同期エンドポイント（小さなファイルのみ）

```powershell
# 警告: 本番環境では使用しないでください
$result = Invoke-RestMethod `
    -Method Post `
    -Uri http://localhost:8000/api/v1/ocr/jobs/sync `
    -Form @{ file = Get-Item -Path ".\small-document.pdf" }

$result | ConvertTo-Json -Depth 10
```

> **同期エンドポイントについて**: 大きなファイルや複数ページのドキュメントでは、クライアントのタイムアウト前に処理が完了しない場合があります。非同期フローの使用を推奨します。

---

## 6. タイムアウトと非同期処理

### タイムアウトの層構造

OCR 処理には複数のタイムアウト層が存在します：

```
クライアント（curl / PowerShell）
    ↓ タイムアウト設定
FastAPI アプリケーション
    ↓ HTTPX タイムアウト（HTTPX_READ_TIMEOUT など）
Read コンテナー（ローカル OCR 処理）
    ↓ アウトバウンド HTTPS
Azure（課金情報のみ）
```

| タイムアウト | 設定箇所 | デフォルト | 説明 |
|------------|---------|----------|------|
| HTTPX 接続 | `HTTPX_CONNECT_TIMEOUT` | 10 秒 | コンテナーへの接続確立 |
| HTTPX 読み取り | `HTTPX_READ_TIMEOUT` | 60 秒 | コンテナーからの応答待機 |
| アプリ全体 | `SYNC_TIMEOUT_SECONDS` | 120 秒 | 同期エンドポイントのみ |
| クライアント | curl / PowerShell 設定 | 各ツールの設定 | クライアント側のタイムアウト |

### 非同期フローを推奨する理由

`POST /ocr/jobs` + `GET /ocr/jobs/{job_id}` の非同期フローを推奨します。

- **大きな PDF**: 数十ページの PDF では OCR に数十秒以上かかることがあります。
- **タイムアウト回避**: HTTP 接続を長時間保持するとリバースプロキシやロードバランサーがタイムアウトします。
- **リトライ可能**: ネットワーク切断後もジョブ ID があればポーリングを再開できます。

> **注意**: アプリケーションの待機タイムアウト（`SYNC_TIMEOUT_SECONDS`）が発生しても、コンテナー側の OCR 処理はキャンセルされません。タイムアウト後に `GET /ocr/jobs/{operation_id}` で結果を確認できる場合があります。

---

## 7. 起動・停止と週末運用

### 通常の停止（データを保持）

```powershell
# コンテナーを停止（データは保持）
docker compose stop

# 状態確認
docker compose ps
```

### 完全な削除（データも削除）

```powershell
# コンテナーとネットワークを削除（ボリュームは保持）
docker compose down

# ボリュームも含めて完全削除（データが消えます）
docker compose down --volumes
```

### 再起動

```powershell
# 停止したコンテナーを再起動
docker compose start

# または再ビルドして起動
docker compose up -d
```

### 週末運用のガイドライン

接続コンテナーは**停止中は Azure に課金情報を送信しません**。PoC 期間中は週末に停止することでコストを節約できます。

**金曜日の停止手順:**

1. 進行中のジョブが完了するまで待機します。
2. コンテナーを停止します：
   ```powershell
   docker compose stop
   ```
3. Docker Desktop を終了します（任意）。

**月曜日の起動チェックリスト:**

1. Docker Desktop を起動します。
2. コンテナーを起動します：
   ```powershell
   docker compose start
   ```
3. ログを確認します（エラーがないことを確認）：
   ```powershell
   docker compose logs --tail=50
   ```
4. Read コンテナーの準備完了を確認します：
   ```powershell
   # status が "ok" になるまで待機
   do {
       Start-Sleep -Seconds 10
       $health = Invoke-RestMethod http://localhost:8000/api/v1/health
       Write-Host "Container status: $($health.container.status)"
   } while ($health.status -ne "ok")
   ```
5. 小さなテスト用ドキュメントで動作確認します（個人情報を含まないもの）。
6. 問題がなければ通常業務を再開します。

> **再起動時の注意**: 停止中に接続コンテナーが Azure 課金エンドポイントに接続できなかった場合でも、停止中の課金は発生しません。ただし、再起動後は Azure の課金エンドポイントに接続できることが必要です。

---

## 8. トラブルシューティング

### 8-1. Billing / ApiKey の不一致

**症状**: コンテナーのログに認証エラー、コンテナーがすぐに再起動する。

**対処**:
- `.env` の `DI_BILLING_ENDPOINT` と `DI_API_KEY` が同じ Azure リソースのものであることを確認してください。
- エンドポイントの末尾にスラッシュが含まれているか確認してください。
- Azure Portal でキーが有効かどうか確認してください（キーをローテーションした場合）。

```powershell
# .env の内容を確認（キーの値は表示されません）
Get-Content .env | Where-Object { $_ -notmatch "API_KEY" }
```

### 8-2. Azure エンドポイントに接続できない

**症状**: コンテナーのログに `Connection refused` または `Network unreachable`。

**対処**:
- `DI_BILLING_ENDPOINT` の URL が正しいか確認してください。
- 会社のファイアウォール / Proxy が `*.cognitiveservices.azure.com` への HTTPS 通信を許可しているか確認してください。
- VPN を一時的に切断してテストしてみてください。

```powershell
# Azure エンドポイントへの接続を確認
Test-NetConnection -ComputerName "your-resource.cognitiveservices.azure.com" -Port 443
```

### 8-3. コンテナーは起動しているが OCR の準備ができていない

**症状**: ヘルスチェックが `"status": "degraded"` を返す。

**対処**:
- Read コンテナーはモデルのロードに数分かかります。しばらく待ってから再度確認してください。
- コンテナーのログでエラーを確認してください：
  ```powershell
  docker compose logs di-read --tail=50
  ```
- `/status` エンドポイントが使用するイメージに存在しない場合があります。Microsoft ドキュメントで確認してください。

### 8-4. Docker Desktop のリソース不足

**症状**: コンテナーが `OOMKilled`（メモリ不足）で停止する。

**対処**:
- Docker Desktop の Settings → Resources でメモリ割り当てを増やしてください。
- PC の物理メモリが十分かどうか確認してください（Read コンテナーには 8 GB 以上を推奨）。
- 不要なアプリケーションを閉じてメモリを解放してください。

### 8-5. ポートの競合

**症状**: `Bind: address already in use` エラー。

**対処**:
- ポート 5000 または 8000 を使用している別のプロセスを確認してください：
  ```powershell
  netstat -ano | findstr ":5000"
  netstat -ano | findstr ":8000"
  ```
- `compose.yaml` でポートマッピングを変更することができます。

### 8-6. HTTP 202 が返るがポーリングで結果が取得できない

**症状**: `POST /ocr/jobs` が 202 を返すが、`GET /ocr/jobs/{id}` が 404 を返す。

**対処**:
- コンテナーが再起動してジョブが失われた可能性があります（接続コンテナーはインメモリでジョブを管理します）。
- コンテナーのログを確認してください。
- 再度ドキュメントを送信してください。

### 8-7. HTTPX / クライアントタイムアウト

**症状**: タイムアウトエラー（504 Gateway Timeout）。

**対処**:
- `.env` の `HTTPX_READ_TIMEOUT` を増やしてください（大きな PDF の場合）。
- `SYNC_TIMEOUT_SECONDS` を増やしてください（同期エンドポイント使用時）。
- 非同期フロー（`POST /ocr/jobs` + `GET /ocr/jobs/{id}`）を使用してください。

### 8-8. API パス / バージョン / イメージの不一致

**症状**: Read コンテナーが 404 または 400 を返す。

**対処**:
- 使用しているコンテナーイメージのバージョンに対応する API バージョンを確認してください。
- `DI_API_VERSION` と `DI_MODEL_ID` が使用するイメージに対応しているか確認してください。
- コンテナーの Swagger UI（URL はイメージによって異なります）で実際のエンドポイントを確認してください。

### 8-9. ログを安全に確認する方法

**重要**: ログに認証情報やドキュメントの内容が含まれないよう注意してください。

```powershell
# 安全なログ確認（--no-log-prefix は使用しない）
docker compose logs --tail=100 2>&1 | Select-String -NotMatch "ApiKey|Billing|api_key"

# または特定のエラーパターンを検索
docker compose logs di-read --tail=100 | Select-String "error|Error|ERROR"
```

> このアプリケーションは OCR 結果やドキュメント内容をログに出力しません。ただし、コンテナー自体のログには一部の設定情報が含まれる場合があります。

---

## 9. セキュリティ注意事項

### 認証情報の管理

- `.env` ファイルは `.gitignore` で Git 追跡から除外されています。
- `.env` ファイルを Slack、メール、GitHub に貼り付けないでください。
- `.env.example` にはプレースホルダーのみが含まれており、Git に含まれています。
- 定期的に API キーをローテーション（Azure Portal の「キーのローテーション」機能を使用）してください。

### テスト用ドキュメント

- PoC の初期テストには**個人情報を含まないドキュメント**を使用してください。
- 本番データを使用する前にセキュリティレビューを行ってください。

### ログの安全性

- このアプリケーションはリクエストボディ（ドキュメント内容）を**ログに出力しません**。
- OCR 結果（テキスト内容）もログに出力されません。
- API キーはログに出力されません。

### ネットワーク制限

- 接続コンテナーは Azure の課金エンドポイント（`*.cognitiveservices.azure.com`）への HTTPS 通信が必要です。
- 他のアウトバウンド通信は不要なため、ファイアウォールで制限することを推奨します。
- 組織のポリシーですべてのアウトバウンド通信が禁止されている場合は、切断コンテナーを検討してください（別途 Microsoft への申請が必要）。

---

## 10. クリーンアップ

### PoC 終了時のクリーンアップ

```powershell
# コンテナーの停止と削除
docker compose down

# コンテナーイメージの削除（ディスクスペースを解放）
docker rmi $(docker images -q "mcr.microsoft.com/azure-cognitive-services/form-recognizer/*")

# ビルドされた FastAPI イメージの削除
docker compose down --rmi local
```

### ローカルデータの削除

```powershell
# OCR 結果・ログ・一時ファイルの削除
Remove-Item -Recurse -Force output, logs, uploads -ErrorAction SilentlyContinue
```

### API キーが漏洩した場合の対応

1. **Azure Portal で即座にキーをローテーション**します：
   - Azure Portal → 対象リソース → 「キーとエンドポイント」
   - 「キー 1 の再生成」または「キー 2 の再生成」をクリック
2. 新しいキーで `.env` を更新します。
3. コンテナーを再起動します。
4. 漏洩した可能性のあるキーが使用されたか Azure Monitor のログを確認します。

> **重要**: キーが GitHub に誤ってコミットされた場合、git history を書き換えても漏洩は防げません。即座にキーをローテーションしてください。

### Azure リソースの削除

PoC が終了して Azure リソースが不要になった場合：

1. Azure Portal → 対象リソースグループ → **「リソースグループの削除」**
2. 削除前に、そのリソースに依存している他のサービスがないことを確認してください。
3. 削除は取り消せません。削除前にデータのバックアップが必要か確認してください。

---

## 開発者向け情報

### ローカル開発環境のセットアップ

```powershell
# 仮想環境の作成と有効化
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 開発依存関係のインストール
pip install -r requirements-dev.txt
```

### テストの実行

```powershell
# 単体・結合・E2E スモークテストを実行（Azure や Read コンテナーは不要）
python -m pytest tests/ -v

# 種別ごとに実行
python -m pytest tests/unit -v
python -m pytest tests/integration -v
python -m pytest tests/e2e -v

# カバレッジ付きで実行
python -m pytest tests/ -v --cov=app
```

### コードフォーマット

```powershell
# リントチェック
ruff check app/ frontend/ tests/

# 自動修正
ruff check --fix app/ frontend/ tests/

# フォーマット
ruff format app/ frontend/ tests/
```

### アプリをローカルで直接起動（コンテナーなし）

```powershell
# .env を読み込んで起動
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 別のターミナルで Streamlit を起動
$env:FASTAPI_BASE_URL = "http://localhost:8000"
streamlit run frontend/app.py
```

---

## プロジェクト構成

```
.
├── app/
│   ├── config.py       # 設定モジュール（pydantic-settings）
│   ├── client.py       # Document Intelligence クライアント
│   ├── main.py         # FastAPI アプリケーション
│   └── models.py       # Pydantic リクエスト/レスポンスモデル
├── frontend/
│   ├── api_client.py   # FastAPI クライアント
│   └── app.py          # Streamlit フロント画面
├── tests/
│   ├── unit/           # 単体テスト
│   ├── integration/    # 結合テスト
│   ├── e2e/            # E2E スモークテスト
│   ├── conftest.py     # テストフィクスチャ
│   ├── test_health.py  # ヘルスチェックテスト
│   ├── test_upload.py  # アップロードバリデーションテスト
│   └── test_jobs.py    # ジョブ送受信テスト
├── .env.example        # 環境変数テンプレート（認証情報なし）
├── .gitignore          # Git 除外設定
├── compose.yaml        # Docker Compose 設定
├── Dockerfile          # FastAPI コンテナーイメージ
├── Dockerfile.streamlit # Streamlit コンテナーイメージ
├── pyproject.toml      # pytest / ruff 設定
├── requirements.txt    # 本番依存関係
├── requirements-frontend.txt # Streamlit 依存関係
└── requirements-dev.txt # 開発・テスト依存関係
```

---

## ライセンス

このプロジェクトは MIT ライセンスの下で公開されています。

Azure AI Document Intelligence の利用については、[Microsoft のサービス利用規約](https://www.microsoft.com/licensing/terms)に従ってください。
# 決算短信の情報抽出

Streamlit 画面では、アップロードした PDF のプレビューに加えて、Azure OpenAI を
利用した会社名・証券コード・決算期の抽出を実行できます。抽出結果には OCR 行の
元情報が表示され、選択した項目の根拠位置が PDF 上でオレンジ色にマークされます。

利用する場合は `.env` に `AZURE_OPENAI_ENDPOINT`、`AZURE_OPENAI_API_KEY`、
`AZURE_OPENAI_DEPLOYMENT` を設定してください。

## PostgreSQL、キャッシュ、Excel 出力

決算短信 PDF と解析結果は PostgreSQL に保存されます。PDF 内容の SHA-256 と
`ANALYSIS_PROCESSING_VERSION` が同じ成功済み結果は、OCR と Azure OpenAI を再実行せず
DB から返します。処理バージョンを変更すると、同じ PDF を新しい抽出仕様で再解析できます。

保存対象は次のとおりです。

- PDF 本体、元ファイル名、MIME タイプ、サイズ、SHA-256
- OCR 全文・JSON、会社名、証券コード、決算期、根拠位置
- 処理状態、operation ID、キャッシュ利用、処理時間、失敗情報

抽出結果画面の「Excelを作成」から、PDF名・会社名・コード名（証券コード）・決算期を
一行にした `.xlsx` をダウンロードできます。

### DB の起動とマイグレーション

`.env.example` をコピーし、`POSTGRES_PASSWORD` を強固な値へ変更してください。
`docker compose up -d` では PostgreSQL の正常起動後に
FastAPI が Alembic のマイグレーションを自動適用します。手動適用する場合は次を実行します。

```powershell
docker compose run --rm fastapi alembic upgrade head
```

### バックアップと復元

```powershell
# バックアップ
docker compose exec postgres pg_dump -U postgres -d document_app -Fc -f /tmp/document_app.dump
docker compose cp postgres:/tmp/document_app.dump ./document_app.dump

# 復元（対象 DB の内容を置き換えるため、事前に確認してください）
docker compose cp ./document_app.dump postgres:/tmp/document_app.dump
docker compose exec postgres pg_restore -U postgres -d document_app --clean --if-exists /tmp/document_app.dump
```

PDF と OCR 全文を保存するため、本番利用前にアクセス制御、保存期間、削除、暗号化、
バックアップ保護の方針を定めてください。通常のアプリログには PDF・OCR 本文を出力しません。
