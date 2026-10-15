# Email provider setup (SMTP + Resend)

The app ships in **dry-run**: campaigns preview, test and "send" without any
network call (messages recorded as `accepted (dry-run)`). Going live needs
ONE of the below, set as server env vars (Render → Environment). Secrets
stay server-side — the browser never sees them.

## Option A — SMTP (documented default for self-hosting)

Works with Gmail (app password), Outlook, or any SMTP host.

```env
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=<app-password-not-your-login-password>
SMTP_FROM=you@gmail.com
SMTP_USE_TLS=true
```

- Gmail: enable 2-step verification → create an **App password** → use it
  as `SMTP_PASSWORD`. Never use your login password.
- `/api/health` → `provider_configured: true` confirms the wiring (it never
  prints values).

## Option B — Resend HTTP API

```env
EMAIL_PROVIDER=resend
RESEND_API_KEY=re_…
RESEND_FROM=Trial <trial@yourdomain.com>
```

Verify your sending domain in Resend first (SPF/DKIM). Resend webhooks can
be pasted into `POST /api/email-events/inbound` (authenticated) to record
delivery/reply/bounce events.

## Option C — Sign in with Google (login + Gmail send, one consent)

1. Google Cloud → APIs & Services → enable **Gmail API** → Credentials →
   create **OAuth client ID** (Web application).
2. Authorized redirect URI: exactly your backend callback, e.g.
   `http://127.0.0.1:8002/api/auth/google/callback` locally
   (production: `https://<your-api>/api/auth/google/callback`).
3. Set server env: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
   `GOOGLE_REDIRECT_URI` (+ `EMAIL_PROVIDER=gmail` to send via Gmail).
4. Users click **Continue with Google** on login/signup: Google verifies
   their email automatically and grants `gmail.send` — the refresh token
   stays in your database, never in the browser. Settings shows
   “Gmail connected”.
5. Test: Campaign → Send test → goes through the user's Gmail
   (check Email Activity for the provider message ID).

## Going live (required order)

1. Set provider env vars → redeploy → `/api/health` shows configured.
2. Sign up → **verify your email** (Settings → Verify now).
3. Campaign → Preview → **Send test** to your own address → confirm receipt.
4. Preview recipients → **double-confirm** (button + exact count) → Send.
5. Every live email carries a one-click unsubscribe link
   (`/unsubscribe?email=…`); unsubscribes suppress instantly.

## Limits & law

- Trial defaults: 50 live emails/day/account, 100 recipients/campaign.
- Only message addresses with a legitimate basis; identify the sender;
  honor unsubscribes immediately (automatic). Comply with CAN-SPAM /
  GDPR / your local rules and the provider's policies.
- Replies/bounces/unsubscribes stop follow-ups automatically.
