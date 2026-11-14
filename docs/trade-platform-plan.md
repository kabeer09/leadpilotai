# LeadPilot → AI-Powered Global Trade Intelligence & Buyer Discovery Platform

Phase 1 deliverable: project audit + integration plan for extending the
existing LeadPilot AI application (never replacing it).

---

## 1. Project audit (as-is)

### Stack
| Layer | Technology |
|---|---|
| Backend | Python / FastAPI, SQLAlchemy ORM, Pydantic v2 settings (`app/config.py`) |
| Database | SQLite in dev (`trial.db`, WAL, busy_timeout=30s), PostgreSQL-ready via `DATABASE_URL` |
| Migrations | Alembic (`backend/alembic`, 4 revisions `0001`–`0004`) + startup `Base.metadata.create_all` + ad-hoc ALTERs |
| Frontend | React 18 + Vite 6 + TypeScript, react-router v6, axios, lucide-react, recharts, Tailwind (custom `.card`/`.btn-*`/`.table-*` utilities) |
| Auth | JWT access/refresh (`app/security.py`), `get_current_user`/`get_admin` deps, RBAC = `is_admin` flag only |
| Tests | pytest (154 passing), isolated SQLite test DB, external APIs always mocked |

### Backend shape
- 14 routers included in `app/main.py` (`/api/auth`, `/api/crawls`, `/api/companies`,
  `/api/leads`, `/api/campaigns`, `/api/analytics`, `/api/gbleads`, `/api/research`, …).
- Startup: `create_all`, stale-job recovery (crawl + GB), config warnings; no network at import.
- Existing model modules: `models.py` (users, companies, contacts, leads, campaigns,
  audit), `models_research.py`, `models_gbleads.py`. Tables are additive per feature
  module — the pattern to follow.
- Provider abstraction precedent: `google_places.py` / `osm_places.py` with typed
  exceptions (`PlacesConfigError/QuotaError/APIError`), factory `get_places_provider()`,
  injectable HTTP client for tests, honest typed errors instead of fake "0 results".
- Cross-cutting deps: `rate_limit(key, per_min)` sliding window, `audit()` helper,
  tenant scoping via `user_id` on every query.
- Export precedent: CSV via `csv.writer` + `StreamingResponse`, XLSX via `openpyxl`
  (`api_gbleads.py:525-583`).

### Frontend shape
- Routes in `src/App.tsx` under an auth-guarded `<Layout>` shell; nav arrays in
  `src/Layout.tsx` (`menu` + `other`, items `{to, icon, label}`).
- API layer: single axios instance in `src/lib/api.ts` with token interceptor +
  401 refresh; one exported function per endpoint; blob downloads for exports.
- Page conventions: `.card`, `.input`, `.label`, `.btn-primary/secondary/lime`,
  `.table-th/td`, local `errText()` helper, flash banners, loading/empty states.

### Gaps / problems found
1. **Zero trade, HS-code, buyer/supplier, market or competitor code exists** —
   greenfield feature area (verified by repo-wide keyword search).
2. Gemini is referenced (`GEMINI_API_KEY`) but `google-generativeai` is not
   installed — AI calls always fall back to templates (relevant for Phase 5).
3. No rate limiting toward *outbound* third-party trade APIs (only inbound).
4. No data-source registry: provider status/limits are implicit in config.
5. RBAC is a single admin flag — acceptable to preserve for now; new endpoints
   stay tenant-scoped like the rest of the app.

---

## 2. Integration architecture (to-be)

### Principles (from the master prompt)
- **Extend, never replace**: new tables are additive (`models_trade.py`), new router
  (`api_trade.py`) wired into `main.py` next to the existing 14.
- **Real data only**: every row carries `source` attribution; no invented shipments,
  buyers, or statistics. Aggregated statistics are labelled
  `record_type: "aggregated_statistics"` and never presented as shipment records.
- **Typed errors**: `TradeError` → `TradeConfigError` (503) / `TradeQuotaError` (429,
  stale-cache fallback) / `TradeAPIError` (502) — never a silent empty result.
- **Keys server-side only**: `.env.example` gets placeholders; the browser never
  sees a provider key; `/api/data-sources` reports `auth_configured: bool` only.
- **Tests mock the network**: `httpx.MockTransport` / fake providers → zero credits.

### New tables (additive)
| Table | Purpose | Tenant |
|---|---|---|
| `saved_searches` | saved trade/statistics query configs | per user |
| `trade_statistics` | cache of aggregated UN Comtrade results (JSON payload + fetched_at) | global cache |
| `data_providers` | provider registry: endpoint, categories, limits, last success/error | global |

Migration: `alembic/versions/0005_trade_tables.py` (Docker/PG path); SQLite dev
picks the tables up via `create_all` at startup like every other feature module.

### Provider layer (`app/trade_providers.py`)
- `UNComtradeProvider` — public preview API
  (`GET /public/v1/preview/C/A/HS`, no key, ≤500 records/call), optional
  subscription key for `/data/v1/get`. Injected `httpx.Client` + `sleep` for tests;
  own minimum interval between calls + exponential backoff on 429.
- Reference data (reporters / partners / HS descriptions) fetched from UN
  `files/v1/app/reference/*.json` and cached (the API returns `*Desc = null`).
- Factory `get_trade_provider()` via `TRADE_PROVIDER` setting
  (`un_comtrade` | `none`); paid adapters (ITC Trade Map, India TradeStat,
  commercial shipment providers) are registered as `not_configured` — built and
  listed honestly, never claimed working without real credentials.

### API (`app/api_trade.py`, prefix `/api`)
| Endpoint | Purpose |
|---|---|
| `GET /trade/search` | import/export statistics search (reporter, period range, flow, HS code, partner) |
| `GET /trade/statistics` | time series + partner breakdown for charts |
| `GET /trade/hs-codes` | HS code search by product name or code |
| `GET /trade/countries` | reporter/partner country reference lists |
| `GET|POST|DELETE /trade/saved-searches` | saved search CRUD (tenant-scoped) |
| `GET /trade/export` | CSV / XLSX export with source-attribution rows |
| `GET /data-sources` | provider registry + integration status (no secrets) |

All endpoints: JWT auth, rate limits, Pydantic/Query validation, audit on writes.

### Frontend
- New page `src/pages/GlobalTrade.tsx` at route `/trade`, nav item
  “Global Trade Data” (`Globe2`) in `Layout.tsx`.
- Sections: search form → source banner → results table → trend chart (recharts)
  → save/load searches → CSV/Excel export → “Find matching companies in CRM”
  cross-link (ties trade results back into the existing CRM) → data-source status.

### Phases (master prompt order)
| Phase | Scope | Status |
|---|---|---|
| 1 | Inspect, audit, integration plan | ✅ this document |
| 2 | Adapter architecture + UN Comtrade + trade statistics search + country/HS lookup | ✅ committed (`4edc2c9`) |
| 3 | Buyer/supplier discovery, exhibitor extraction improvements, company research | ✅ this round — 216/216 tests green, build clean, pages DOM-verified |
| 4 | Competitor analysis, HS intelligence, country comparison | ✅ this round — 243/243 tests green, build clean, 3 new endpoints + 4-tab UI DOM-verified |
| 5 | AI Trade Analyst, lead scoring, AI research reports | ✅ this round — 274/274 tests green, build clean, 3 AI endpoints + 5-tab UI + Research AI cards |
| 6 | CRM improvements, email automation, dashboard analytics | ✅ this round — 296/296 tests green, build clean, funnel/pipeline endpoints + send-time re-mail guards + audience filters DOM-verified |
| 7 | Testing, security review, performance, docs, deployment prep | pending |

Each phase ends with: migrations run, backend tests green (existing 154 must stay
green), frontend `npm run build` clean, modified-file report, completed/pending list.
