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

Provider calls are mocked — tests never send real emails, never hit the
Google Places API and never touch the dev database (isolated
`test_trial.db`).

## Google Business Lead Finder

Second discovery channel beside the trade-fair crawler: keyword + location
searches against **Google Places API (New)**, product-relevance scoring,
official-website enrichment for published contacts, CSV/Excel export.

### Enable it (Google Cloud)

1. [Google Cloud Console](https://console.cloud.google.com/) → select or
   create a project and link a **billing account** (required even inside
   the free tier). Places API (New) bills per request at the highest SKU
   the field mask touches; with phone/website/rating fields that's
   **Text Search Enterprise ≈ $0.035/request, first 1,000 free each
   month** (free caps reset the 1st, Pacific time).
2. **APIs & Services → Library** → enable **Places API (New)** and
   **Place Details (New)**.
3. **APIs & Services → Credentials → Create credentials → API key**.
4. **Restrict the key**: application restrictions → your server's IP;
   API restrictions → only the two APIs above.
5. **Set a quota** (APIs & Services → Quotas) as a spend safety net; the
   app additionally enforces its own per-user budget
   (`GB_DAILY_API_CALLS`, default 500 requests/day).
6. Put the key in `backend/.env` (gitignored — never commit it):
   `GOOGLE_MAPS_API_KEY=your-key-here`
7. Restart the backend — **Settings → Google Business Lead Finder** then
   shows *configured*.

Without a key the UI reports "not configured" and refuses to search.
Automated tests mock the provider and never spend API credits.

### Tunables (`backend/.env`)

| Variable | Default | Meaning |
|---|---|---|
| `GB_MAX_RESULTS_PER_SEARCH` | 60 | Google's cap (20/page × 3 pages) |
| `GB_MAX_PAGES_PER_SITE` | 6 | bounded crawl per website |
| `GB_REQUEST_DELAY` | 1.0 | seconds between website fetches |
| `GB_SEARCH_TIMEOUT` | 20 | seconds per Google request |
| `GB_SEARCH_MAX_RETRIES` | 3 | exponential backoff on 429/5xx |
| `GB_COST_PER_CALL_USD` | 0.035 | cost estimate per request (Enterprise SKU) |
| `GB_DAILY_API_CALLS` | 500 | per-user daily Google budget |
| `GB_ENRICHMENT_ENABLED` | true | website enrichment on/off |

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

- `backend/app/` — FastAPI: auth, crawls, CRM, campaigns, analytics, admin,
  Google Business Lead Finder (Places API provider, enrichment, scoring)
- `backend/alembic/` — migrations (never destructive; no `create_all` on boot)
- `backend/tests/` — pytest suite (auth, crawler/SSRF, CRM, email, isolation,
  business leads)
- `frontend/src/` — React+TS+Vite+Tailwind pages (see Navigation below)
- `render.yaml` — Render blueprint (Supabase Postgres + web + static)

## Navigation

Overview, Companies, Leads, Business Leads (Google Business Lead Finder),
Website Crawling, Email Campaigns, Email Activity, Follow-ups, Analytics,
Settings (+ public Landing/Login/Signup, password reset, one-click
Unsubscribe).

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
- WhatsApp: dry-run by default; live sends/checks require Meta Cloud API
  credentials, an approved template and double confirmation.
