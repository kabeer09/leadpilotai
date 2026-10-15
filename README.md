# LeadPilot Public Trial

SaaS-style B2B lead research + email automation. Anyone can sign up (password
or Google), crawl fair directories and company websites, build lead lists,
preview campaigns and send authorized emails. Dry-run by default — nothing
leaves the server until a provider is configured **and** the owner confirms twice.

## Run locally

```powershell
# backend (needs Python 3.12 + Playwright Chromium for JS pages)
cd backend
pip install -r requirements.txt
python -m playwright install chromium
python -m alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8002

# frontend (needs Node 18+, new terminal)
cd frontend
npm install
npm run dev     # http://localhost:5174
```

Copy `.env.example` → `backend/.env` to override defaults (SQLite dev DB,
dry-run mailer). First registered account becomes admin.

## Sign in with Google (optional)

Paste your Google OAuth client on the signup page (inline 2-minute guide
included) or set `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` in `backend/.env`.
One consent signs users in **and** connects Gmail sending
(`EMAIL_PROVIDER=gmail`). Refresh tokens stay server-side. See
[`docs/providers.md`](docs/providers.md) → Option C.

## Crawling (fair directories + company sites)

- **Fair adapters** enumerate whole directories in seconds: Las Vegas +
  Atlanta (ANDMORE A–Z API, ~1,600 profiles), Ambiente (search API, ~1,800
  full records), High Point (autocomplete JSON, ~1,759 profiles).
- **JS rendering fallback**: static fetch first, headless Chromium when the
  page is a JS shell; exhibitor details cards (contact block, booth,
  address) pulled from fair APIs where available.
- **Stage 3 enrichment**: official site + contact/about pages per exhibitor,
  every value stamped with its source URL; own-domain primary picked,
  junk/noreply flagged, nothing ever deleted.
- Polite by design: robots.txt, 1s delays, quotas (25/50/100 page presets,
  up to 500), skip-complete re-crawls, drop site-less junk, 3 workers.

## Tests

```powershell
cd backend
python -m pytest tests/ -q -p no:cacheprovider -W ignore   # 51 passing
```

Provider calls are mocked — tests never send real emails and never touch
the dev database (isolated `test_trial.db`).

## Production build

```powershell
cd frontend
npm run build   # tsc + vite build → dist/
```

## Deploy

See [`docs/deployment.md`](docs/deployment.md) (Render + Supabase),
[`docs/providers.md`](docs/providers.md) (SMTP/Resend/Gmail setup),
[`docs/troubleshooting.md`](docs/troubleshooting.md). `render.yaml` holds
the Render blueprint (Docker backend with Chromium + static frontend).

## Project layout

- `backend/app/` — FastAPI: auth (+Google OAuth), crawls, CRM, campaigns,
  analytics, admin, fair adapters, validation
- `backend/alembic/` — migrations (never destructive; no `create_all` on boot)
- `backend/tests/` — pytest suite (auth, crawler/SSRF, CRM, email, isolation)
- `frontend/src/` — React+TS+Vite+Tailwind, dark-sidebar CRM design
- `render.yaml` — Render blueprint (Supabase Postgres + web + static)

## Navigation

Overview, Companies, Leads, Website Crawling, Email Campaigns, Email
Activity, Follow-ups, Analytics, Settings (+ public Landing/Login/Signup,
password reset, one-click Unsubscribe).

## Safety model (short version)

- bcrypt passwords, JWT access (60 min) + rotating refresh tokens,
  per-IP rate limits, tenant isolation in every query.
- SSRF-guarded crawler (blocks localhost/private/metadata incl. after
  redirects and DNS tricks), robots.txt, quotas, per-user daily caps.
- Campaigns: preview → test-to-self → double-confirm send; idempotency
  keys stop concurrent double-sends; suppressions always win.
- Follow-ups: off until enabled, max 1, 24h default, re-checked before
  send, skipped+flagged when state is unknown.
- `accepted` ≠ `delivered` — delivery only on provider proof.
- Fair-owner mailboxes are stripped from exhibitor profiles (no
  misattribution); fair contact data comes from fair APIs only.
- No WhatsApp automation in this release (provider interface reserved).
