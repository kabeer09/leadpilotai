# Troubleshooting

## Backend won't start / 500 on signup
- Check `backend/server.err` tail for the traceback.
- `InvalidRequestError` on mappers → a relationship misses its reverse side.
- SQLite `database is locked` → stop duplicate servers; tests use an
  isolated DB, the dev server uses `trial.db`.

## Port already in use
- 8000 is often taken (Docker/WSL relay). The trial backend defaults here
  are: API `8002`, web `5174` locally — adjust if needed.

## Emails stuck at `accepted`, never `delivered`
- Expected in dry-run and SMTP mode: only provider webhooks/Resend confirm
  delivery (`POST /api/email-events/inbound`). `accepted` = provider took it.

## Gmail 429s (if using Gmail SMTP)
- Lower batch sizes, raise pauses between sends. Failed sends show the
  provider error per recipient; remainder stays queued.

## Crawl finds nothing
- Stub pages (e.g. example.com) legitimately yield no company — the crawler
  never invents data. Check job Details → per-page errors (robots block,
  non-HTML, timeout).
- `URL not allowed` → SSRF guard blocked it (localhost/private/metadata).

## Login loops / 401s
- Access tokens live 60 min; refresh auto-renews. If stuck: sign out,
  clear `lp_access`/`lp_refresh` in devtools → log in again.

## Database
- Local: `backend/trial.db` (SQLite). Reset dev only:
  delete the file → `alembic upgrade head`.
- Prod: Supabase Postgres; migrations run at container boot. Never run
  `downgrade` or recreate the production DB.
