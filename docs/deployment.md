# Deployment — Render + Supabase (free tier)

## 1. Database (Supabase, free)

1. https://supabase.com → New project → copy the **connection string**
   (Settings → Database → Connection string, `postgres://…`).
2. In SQL Editor you don't need to do anything — Alembic creates the
   schema on first backend boot (`alembic upgrade head` in Docker CMD).

## 2. Backend (Render Web Service, free)

Option A — blueprint: push this repo to GitHub, Render → New → Blueprint,
select repo (uses `render.yaml`; replace `YOUR-USERNAME` first).
Option B — manual: New → Web Service → Docker → root `backend/`.

Set env vars (Render → Environment):

| Key | Value |
|---|---|
| `DATABASE_URL` | Supabase `postgres://…` string |
| `SECRET_KEY` | Generate (Render can generate) |
| `APP_URL` / `FRONTEND_URL` | Your Render URLs |
| `EMAIL_PROVIDER` | `dryrun` to start |
| SMTP/RESEND_* | Only when going live (see `docs/providers.md`) |

Health check path: `/api/health`. First boot runs migrations, then serves.

## 3. Frontend (Render Static Site, free)

New → Static Site → root `frontend/`, build `npm install && npm run build`,
publish `dist`. Add rewrite rules from `render.yaml` (`/api/*` → backend,
`/*` → `/index.html`). Set `VITE_API_URL` to the backend URL.

## 4. Go-live checklist

- [ ] `/api/health` → `{"status":"ok",...}` on the live backend
- [ ] Sign up → first account shows **Admin**
- [ ] Crawl `https://example.com` (max 1 page) → COMPLETED in history
- [ ] Create campaign → preview → test email to yourself
- [ ] `EMAIL_PROVIDER` still `dryrun` until SMTP/Resend verified
- [ ] Verify-email flow works; admin suspend works (Settings)

## Free-tier limitations (handled gracefully)

- **Sleeping backend**: first request after idle takes ~60s; the UI shows
  loading states, axios retries once on 401-refresh only — users just wait.
- **No background workers**: crawls/sends run in-process threads; large
  jobs are capped (30 pages/crawl, 100 recipients/campaign, quotas/day).
- **Ephemeral disk**: uploads don't exist; DB is Supabase (persistent).
- **Provider rate limits**: SMTP/Resend errors mark messages FAILED with
  the reason shown; batches stop at the daily quota, remainder stays queued.
- **DB disconnects**: `pool_pre_ping` + `/api/health` reports `degraded`
  instead of crashing; retry the request.
