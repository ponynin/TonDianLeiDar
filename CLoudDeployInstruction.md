第 2 部分：雲端部署指導文件
這是一份為測試團隊準備的，關於如何將「痛點雷達」部署到 Google Cloud 的詳細指南。

markdown
# 「痛點雷達」Google Cloud 部署指南 (MVP V1.0)

本文檔旨在指導測試與運維團隊將「痛點雷達」應用的後端、前端及數據管道部署到 Google Cloud Platform (GCP)。

## 1. 核心架構

我們將採用以下 GCP 服務來構建一個無伺服器 (Serverless)、可擴展且易於維護的架構：

*   **後端 API**: 使用 **Cloud Run** 部署 FastAPI 應用容器。
*   **前端應用**: 使用 **Firebase Hosting** 部署 Next.js 靜態導出網站，以獲得全球 CDN 加速。
*   **數據管道**:
    *   **Cloud Run Jobs**: 用於運行一次性的 `run_scraper.py` 腳本容器。
    *   **Cloud Scheduler**: 用於每日定時觸發 Cloud Run Job。
*   **資料庫**: 使用 **Cloud SQL for PostgreSQL** 作為我們的主資料庫。
*   **密鑰管理**: 使用 **Secret Manager** 統一管理所有敏感信息（如 API Key、資料庫密碼）。
*   **容器倉庫**: 使用 **Artifact Registry** 存儲我們的 Docker 鏡像。
*   **持續整合/持續部署 (CI/CD)**: 使用 **Cloud Build** 自動從源代碼構建和部署容器。

## 2. 部署前準備

1.  **安裝工具**:
    *   安裝並初始化 Google Cloud SDK (`gcloud` CLI)。
    *   安裝 Docker。
    *   安裝 Firebase CLI: `npm install -g firebase-tools`

2.  **GCP 項目配置**:
    *   確保您擁有一個 GCP 項目，並已啟用計費。
    *   啟用以下 API 服務：
        *   Cloud Run API
        *   Cloud SQL Admin API
        *   Secret Manager API
        *   Artifact Registry API
        *   Cloud Build API
        *   Cloud Scheduler API
        *   Identity and Access Management (IAM) API

3.  **環境變量文件**:
    *   在本地項目根目錄下創建一個 `.env.gcp` 文件，用於存放雲端部署的配置信息。**此文件不應提交到 Git**。

    ```bash
    # .env.gcp

    # --- GCP Configuration ---
    GCP_PROJECT_ID="your-gcp-project-id"
    GCP_REGION="asia-east1" # e.g., asia-east1 for Hong Kong
    GCP_ARTIFACT_REPO="tondianleidar-repo"

    # --- Service Names ---
    BACKEND_SERVICE_NAME="tondianleidar-api"
    PIPELINE_JOB_NAME="tondianleidar-scraper-job"

    # --- Database (To be filled after Cloud SQL creation) ---
    DB_USER="postgres"
    DB_NAME="tondianleidar"
    ```

## 3. 基礎設施搭建

### 3.1. 創建 Cloud SQL 資料庫

```bash
# 1. 創建 PostgreSQL 實例
gcloud sql instances create tondianleidar-db \
  --database-version=POSTGRES_15 \
  --region=$GCP_REGION \
  --cpu=1 \
  --memory=4GB

# 2. 設置 postgres 用戶的密碼 (請務必記下此密碼)
gcloud sql users set-password postgres \
  --instance=tondianleidar-db \
  --prompt-for-password

# 3. 創建應用資料庫
gcloud sql databases create tondianleidar \
  --instance=tondianleidar-db
3.2. 配置 Secret Manager
我們需要將資料庫密碼和第三方 API Key 存儲在 Secret Manager 中。

bash
 Show full code block 
# 1. 存儲資料庫密碼 (輸入上一步設置的密碼)
gcloud secrets create DB_PASSWORD --replication-policy="automatic"
echo -n "YOUR_DB_PASSWORD" | gcloud secrets versions add DB_PASSWORD --data-file=-

# 2. 存儲 JWT 密鑰 (生成一個新的隨機密鑰)
openssl rand -hex 32 | tr -d "\n" | gcloud secrets versions add JWT_SECRET_KEY --data-file=-

# 3. 存儲 Reddit API 憑證
echo -n "YOUR_PRAW_CLIENT_ID" | gcloud secrets versions add PRAW_CLIENT_ID --data-file=-
echo -n "YOUR_PRAW_CLIENT_SECRET" | gcloud secrets versions add PRAW_CLIENT_SECRET --data-file=-
# ... 對 PRAW_USER_AGENT, GEMINI_API_KEY 重複此操作
3.3. 創建 Artifact Registry 倉庫
bash
gcloud artifacts repositories create tondianleidar-repo \
    --repository-format=docker \
    --location=$GCP_REGION \
    --description="Docker repository for TonDianLeiDar"
4. 後端部署 (Cloud Run)
4.1. 構建並推送 Docker 鏡像
我們將使用 Cloud Build 直接從源代碼構建鏡像，這樣更安全高效。

bash
# 提交構建任務
gcloud builds submit ./workspaces/TonDianLeiDar/backend \
  --tag "${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/tondianleidar-repo/tondianleidar-api:latest"
4.2. 部署到 Cloud Run
bash
 Show full code block 
# 獲取 Cloud SQL 連接名
INSTANCE_CONNECTION_NAME=$(gcloud sql instances describe tondianleidar-db --format='value(connectionName)')

# 部署 Cloud Run 服務
gcloud run deploy tondianleidar-api \
  --image "${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/tondianleidar-repo/tondianleidar-api:latest" \
  --platform managed \
  --region $GCP_REGION \
  --allow-unauthenticated \
  --add-cloudsql-instances=$INSTANCE_CONNECTION_NAME \
  --set-secrets="DB_PASSWORD=DB_PASSWORD:latest,SECRET_KEY=JWT_SECRET_KEY:latest,PRAW_CLIENT_ID=PRAW_CLIENT_ID:latest,PRAW_CLIENT_SECRET=PRAW_CLIENT_SECRET:latest,PRAW_USER_AGENT=PRAW_USER_AGENT:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest" \
  --set-env-vars="DB_HOST=/cloudsql/${INSTANCE_CONNECTION_NAME},DB_USER=postgres,DB_NAME=tondianleidar"
部署成功後，GCP 會提供一個 API 服務的 URL。請將此 URL 記錄下來。

5. 數據管道部署 (Cloud Run Job + Scheduler)
數據管道使用與後端相同的 Docker 鏡像。

5.1. 創建 Cloud Run Job
bash
 Show full code block 
gcloud run jobs create tondianleidar-scraper-job \
  --image "${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/tondianleidar-repo/tondianleidar-api:latest" \
  --region $GCP_REGION \
  --add-cloudsql-instances=$INSTANCE_CONNECTION_NAME \
  --set-secrets="DB_PASSWORD=DB_PASSWORD:latest,PRAW_CLIENT_ID=PRAW_CLIENT_ID:latest,PRAW_CLIENT_SECRET=PRAW_CLIENT_SECRET:latest,PRAW_USER_AGENT=PRAW_USER_AGENT:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest" \
  --set-env-vars="DB_HOST=/cloudsql/${INSTANCE_CONNECTION_NAME},DB_USER=postgres,DB_NAME=tondianleidar" \
  --command "python" \
  --args "run_scraper.py,Notion,--limit,100"
5.2. 創建 Cloud Scheduler
bash
 Show full code block 
# 創建一個每日凌晨 3 點運行的調度器
gcloud scheduler jobs create http daily-scraper-job \
  --schedule="0 3 * * *" \
  --time-zone="Asia/Taipei" \
  --uri="https://$(gcloud run jobs describe tondianleidar-scraper-job --region $GCP_REGION --format 'value(latestSucceededExecution.name)')-run.googleapis.com/v1/projects/$GCP_PROJECT_ID/locations/$GCP_REGION/jobs/tondianleidar-scraper-job:run" \
  --http-method POST \
  --oauth-service-account-email="$(gcloud projects describe $GCP_PROJECT_ID --format 'value(projectNumber)')-compute@developer.gserviceaccount.com" \
  --oauth-token-audience="https://run.googleapis.com/"
6. 前端部署 (Firebase Hosting)
6.1. 項目配置
在前端項目 (workspaces/TonDianLeiDar/frontend) 中，創建 .env.local 文件，並填入後端 API 的 URL：
plaintext
# .env.local
NEXT_PUBLIC_API_URL=https://tondianleidar-api-xxxx-an.a.run.app
修改前端代碼中的 fetch 請求，使其使用此環境變量。
登錄並初始化 Firebase:
bash
firebase login
cd workspaces/TonDianLeiDar/frontend
firebase init hosting
在初始化過程中，選擇您的 GCP 項目，並將 public 目錄設置為 out。
6.2. 構建與部署
修改 next.config.mjs，啟用靜態導出：
javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export', // Add this line
};
export default nextConfig;
構建靜態站點：
bash
cd workspaces/TonDianLeiDar/frontend
npm run build
部署到 Firebase Hosting：
bash
firebase deploy --only hosting
部署成功後，Firebase 會提供一個前端應用的 URL。

plaintext

---

### 第 3 部分：上線運行測試指導文件

這是一份為測試工程師準備的，用於驗證系統核心功能的測試手冊。

```markdown
# 「痛點雷達」上線運行測試指南

本文檔旨在指導測試工程師在系統成功部署到 Google Cloud 後，對其核心功能進行驗證。

## 1. 測試前準備

*   **獲取 URL**: 從部署團隊獲取前端應用的公開訪問 URL (由 Firebase Hosting 提供)。
*   **測試賬戶**: 準備一個或多個測試賬戶的用戶名和密碼。如果沒有，需要使用註冊功能創建。
*   **瀏覽器**: 準備 Chrome、Firefox 等主流桌面瀏覽器。

## 2. 核心功能測試案例

請按順序執行以下測試案例。

### TC-01: 用戶註冊與登錄

1.  **步驟**:
    1.  訪問前端 URL，應被自動導向到 `/login` 頁面。
    2.  (如果需要) 找到註冊入口，使用 `testuser01` 和安全密碼創建一個新賬戶。
    3.  使用剛創建的賬戶信息在登錄頁面進行登錄。
2.  **預期結果**:
    *   登錄成功後，頁面應自動跳轉到主頁 (`/`)。
    *   主頁面應顯示機會卡片列表（如果數據管道已運行）或一個空狀態提示。

### TC-02: 數據管道觸發與驗證

1.  **步驟**:
    1.  登錄 GCP Console，導航到 **Cloud Run Jobs**。
    2.  找到名為 `tondianleidar-scraper-job` 的作業，手動點擊「執行」。
    3.  等待幾分鐘，觀察作業狀態變為「成功」。
    4.  刷新前端應用的主頁面。
2.  **預期結果**:
    *   主頁面應出現新的「機會卡片」。
    *   卡片上應包含產品標籤、痛點總結等信息。

### TC-03: 機會報告詳情頁瀏覽

1.  **步驟**:
    1.  在主頁面，點擊任意一張「機會卡片」。
2.  **預期結果**:
    *   頁面成功跳轉到該機會的詳情頁 (`/opportunities/:id`)。
    *   頁面應正確顯示報告的各個模塊：痛點詳情、代表性引用、產品建議、市場分析等。
    *   頁面應包含一個「導出為 CSV」的按鈕。

### TC-04: 數據導出功能

1.  **步驟**:
    1.  在機會報告詳情頁，點擊「導出為 CSV」按鈕。
2.  **預期結果**:
    *   瀏覽器應觸發一個 CSV 文件的下載。
    *   打開下載的 CSV 文件，其內容應與報告頁面顯示的結構化數據一致。

### TC-05: 認證與會話保持

1.  **步驟**:
    1.  在已登錄的狀態下，刷新瀏覽器頁面。
    2.  關閉瀏覽器標籤頁，然後重新打開前端 URL。
2.  **預期結果**:
    *   用戶應保持登錄狀態，無需重新輸入密碼。
    *   可以直接訪問主頁面和詳情頁。

### TC-06: 登出功能

1.  **步驟**:
    1.  在應用中找到並點擊「登出」按鈕 (如果 UI 已實現)。
    2.  如果沒有登出按鈕，手動清除瀏覽器的 `localStorage`。
    3.  刷新頁面。
2.  **預期結果**:
    *   用戶應被重定向到 `/login` 頁面。
    *   直接訪問主頁面 (`/`) 應被攔截並跳轉到登錄頁。

### TC-07: API 健康檢查

1.  **步驟**:
    1.  獲取後端 API 的 URL (由 Cloud Run 提供)。
    2.  直接在瀏覽器中訪問 `https://<api-url>/health`。
2.  **預期結果**:
    *   頁面應顯示 JSON 響應：`{"status":"ok","database":"connected"}`。

Prompts to try
