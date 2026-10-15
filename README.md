# LeadPilot Public Trial

SaaS-style B2B lead research + email automation. Anyone can sign up, crawl
public company websites, build lead lists, preview campaigns and send
authorized test emails. Dry-run by default — nothing leaves the server until
a provider is configured **and** the owner confirms twice.

## Run locally

```powershell
# backend (needs Python 3.12)
cd backend
pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002

# frontend (needs Node 18+, new terminal)
cd frontend
npm install
npm run dev     # http://127.0.0.1:5174
```

Copy `.env.example` → `backend/.env` to override defaults (SQLite dev DB,
dry-run mailer). First registered account becomes admin.

## Tests

```powershell
cd backend
python -m pytest tests/ -q -p no:cacheprovider -W ignore
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
[`docs/providers.md`](docs/providers.md) (SMTP/Resend setup),
[`docs/troubleshooting.md`](docs/troubleshooting.md).

## Project layout

- `backend/app/` — FastAPI: auth, crawls, CRM, campaigns, analytics, admin
- `backend/alembic/` — migrations (never destructive; no `create_all` on boot)
- `backend/tests/` — pytest suite (auth, crawler/SSRF, CRM, email, isolation)
- `frontend/src/` — React+TS+Vite+Tailwind pages (see Navigation below)
- `render.yaml` — Render blueprint (Supabase Postgres + web + static)

## Navigation

Overview, Companies, Leads, Website Crawling, Email Campaigns, Email
Activity, Follow-ups, Analytics, Settings (+ public Landing/Login/Signup,
password reset, one-click Unsubscribe).

## Safety model (short version)

- bcrypt passwords, JWT access (60 min) + rotating refresh tokens,
  per-IP rate limits, tenant isolation in every query.
- SSRF-guarded crawler (blocks localhost/private/metadata incl. after
  redirects), robots.txt, quotas, per-user daily caps.
- Campaigns: preview → test-to-self → double-confirm send; idempotency
  keys stop concurrent double-sends; suppressions always win.
- Follow-ups: off until enabled, max 1, 24h default, re-checked before
  send, skipped+flagged when state is unknown.
- `accepted` ≠ `delivered` — delivery only on provider proof.
- No WhatsApp automation in this release (provider interface reserved).
