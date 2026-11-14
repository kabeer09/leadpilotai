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
| `GEMINI_API_KEY` | Optional — AI analyst/narratives; without it the AI endpoints answer with an honest template fallback (`engine` + `fallback_reason`), never a fake "AI" answer |
| `GOOGLE_MAPS_API_KEY` | Optional — Google Places for Business Lead Finder; unset = free OpenStreetMap provider |
| `WHATSAPP_PROVIDER` | `dryrun` (default) to start; `meta` for real Cloud API sends |
| `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` | Only for `meta` sends (with an approved template) |
| `TRADE_PROVIDER` | `un_comtrade` (default) or `none` to disable trade endpoints with a typed 503 |
| `UN_COMTRADE_API_KEY` | Optional — the public preview API works without a key; set only if you have a subscription |
| `RATE_TRADE_PER_MIN` | `30` — inbound cap on outbound trade-API traffic (429 past the limit) |

Health check path: `/api/health`. First boot runs migrations, then serves.
All keys stay server-side: `.env.example` in the repo holds placeholders
only, and config endpoints report booleans (configured true/false), never
values.

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
- [ ] Global Trade Search returns rows with source attribution — or a
      typed quota/config error, never fabricated statistics
- [ ] AI analyst responses carry `engine` + `ai_generated` labels and
      fall back to templates (with `fallback_reason`) when no AI key is set
- [ ] Trade endpoints answer 429 past `RATE_TRADE_PER_MIN`
- [ ] `.env` (real secrets) is gitignored; `.env.example` still contains
      placeholders only

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
