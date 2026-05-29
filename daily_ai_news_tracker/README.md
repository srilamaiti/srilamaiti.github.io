# Daily AI News Tracker

A two-part Databricks project that:

1. **Refreshes** `daily_ai_news_tracker` every morning with the top AI stories
   from public RSS feeds, classified and summarized by `ai_query` (Llama 3.3 70B).
2. **Visualizes** the table in a Streamlit-based Databricks App.

```
+----------------------+      +-----------------------+      +-----------------------+
|  Public RSS feeds    | ---> | Refresh notebook +    | ---> | Delta table:          |
|  (TechCrunch, arXiv, |      | ai_query (Llama 3.3)  |      | daily_ai_news_tracker |
|  DeepMind, OpenAI,   |      | MERGE (idempotent)    |      +-----------------------+
|  HuggingFace ...)    |      +-----------------------+                 |
+----------------------+                                                v
                                                            +-----------------------+
                                                            | Streamlit Databricks  |
                                                            | App (this folder)     |
                                                            +-----------------------+
```

---

## 1. Daily refresh notebook

Already deployed at:

```
/Users/srila/daily_ai_news_refresh
```

### Parameters

| Name            | Default        | Purpose                                                 |
|-----------------|----------------|---------------------------------------------------------|
| `news_date`     | today (UTC)    | Date label written to new rows                          |
| `lookback_days` | `1`            | Only consider feed entries published in the last N days |
| `max_stories`   | `10`           | Max curated stories per run                             |

### Behavior

- Pulls up to ~30 most-recent entries from each of 11 public AI feeds.
- Filters to the lookback window.
- Sends one `ai_query` call (Llama 3.3 70B, JSON response format) that returns a curated array of
  `{category, headline, source, source_url, summary, relevance_notes}`.
- `MERGE INTO daily_ai_news_tracker` keyed on
  `(news_date, lower(headline))` -- safe to rerun any number of times.

### Run it ad-hoc

```bash
databricks jobs run-now --json '{
  "notebook_task": {
    "notebook_path": "/Users/srila/daily_ai_news_refresh",
    "base_parameters": { "lookback_days": "1", "max_stories": "10" }
  },
  "existing_cluster_id": "0214-190352-w10bzt00"
}'
```

### Schedule it daily (Lakeflow Job)

Save as `daily_ai_news_job.json` then `databricks jobs create --json @daily_ai_news_job.json`:

```json
{
  "name": "daily_ai_news_refresh",
  "tasks": [
    {
      "task_key": "refresh",
      "notebook_task": {
        "notebook_path": "/Users/srila/daily_ai_news_refresh",
        "base_parameters": { "lookback_days": "1", "max_stories": "10" }
      },
      "existing_cluster_id": "0214-190352-w10bzt00",
      "timeout_seconds": 1200,
      "max_retries": 1,
      "retry_on_timeout": true
    }
  ],
  "schedule": {
    "quartz_cron_expression": "0 0 8 * * ?",
    "timezone_id": "America/New_York",
    "pause_status": "UNPAUSED"
  },
  "email_notifications": {
    "on_failure": ["app_notify@gmail.com"]
  },
  "max_concurrent_runs": 1
}
```

Cron `0 0 8 * * ?` = every day at **8:00 AM America/New_York**. Adjust the hour/timezone to taste.

---

## 2. Databricks App (this folder)

### Files

| File              | Purpose                                                      |
|-------------------|--------------------------------------------------------------|
| `app.py`          | Streamlit UI (cards, filters, charts, CSV export)            |
| `app.yaml`        | Databricks Apps runtime config                               |
| `requirements.txt`| Python dependencies                                          |
| `README.md`       | This file                                                    |

### Local preview

```powershell
cd C:\Users\srmaiti\daily_ai_news_app

# one-time
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Set credentials (use your PAT or service principal)
$env:DATABRICKS_HOST = "<your-workspace>.cloud.databricks.com"
$env:DATABRICKS_TOKEN = "<personal-access-token>"
$env:DATABRICKS_WAREHOUSE_ID = "76f5a569205afced"

streamlit run app.py
```

### Deploy to Databricks Apps

```powershell
cd C:\Users\srila\daily_ai_news_app

# 1. Create the app (once)
databricks apps create daily-ai-news-tracker `
    --description "Daily AI news tracker on Unity Catalog"

# 2. Upload the source files to a workspace path
databricks workspace import-dir . /Workspace/Users/srila/daily_ai_news_app `
    --overwrite

# 3. Deploy
databricks apps deploy daily-ai-news-tracker `
    --source-code-path /Workspace/Users/srila/daily_ai_news_app
```

### Authorization model: on-behalf-of-user (OBO)

This app runs every SQL query **as the signed-in viewer**, not as a service principal.
The app SP therefore needs **no** warehouse / catalog / schema / table grants — the
viewer's existing Unity Catalog permissions are what authorize the read. The runtime
forwards a per-request user token to the app via the `X-Forwarded-Access-Token` HTTP
header, which `app.py` passes straight to the Databricks SQL connector.

For OBO to work, the app must declare the `sql` scope. Set it once with:

```powershell
databricks apps update daily-ai-news-tracker `
  --json '{"name":"daily-ai-news-tracker","user_api_scopes":["sql"]}'

# Bounce the app so the next sign-in re-issues a token with the new scope.
databricks apps stop  daily-ai-news-tracker
databricks apps start daily-ai-news-tracker
```

The first time each user opens the app after a scope change, Databricks shows a one-time
**consent screen** asking the user to approve SQL access for the app. Click *Allow*.

#### Permissions each viewer needs (which they already have today as a workspace user):
- `CAN USE` on the SQL warehouse the app queries (default: `76f5a569205afced`)
- `USE CATALOG`, `USE SCHEMA`, `SELECT` on `daily_ai_news_tracker`

### Open the app

After deploy, the app URL appears in the CLI output, e.g.

```
https://daily-ai-news-tracker-<workspace>.databricksapps.com
```

---

## How idempotency works

The MERGE is keyed on `(news_date, lower(headline))`. If the same story is picked up two days in a
row, the row is **updated in place** (refreshed `ingest_timestamp`, latest summary), never
duplicated. To reprocess a specific day:

```sql
DELETE FROM daily_ai_news_tracker
WHERE news_date = DATE'2026-05-28';
```

Then rerun the notebook with `news_date=2026-05-28`.
