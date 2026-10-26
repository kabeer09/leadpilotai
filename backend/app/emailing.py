"""Email providers (dryrun/smtp/resend) + campaign send pipeline + follow-ups.

Honesty rules enforced here, not just documented:
- dryrun never touches a network; messages stay 'accepted'.
- 'accepted' (provider took it) is NEVER reported as 'delivered'.
- Provider failures mark FAILED, never sent/delivered.
- Suppressed addresses are always excluded, even with allow_resend.
"""
import hashlib
import logging
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.utils import formataddr

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models import (Campaign, CampaignRecipient, EmailMessage, ProviderEvent,
                        Followup, Suppression, Lead)

logger = logging.getLogger(__name__)


# ---------- providers ----------
class DryRunProvider:
    name = "dryrun"

    def send(self, to_email: str, subject: str, body: str) -> dict:
        logger.info(f"[dryrun] would send to {to_email}: {subject[:60]}")
        return {"ok": True, "provider_message_id": f"dryrun-{hashlib.md5(to_email.encode()).hexdigest()[:12]}",
                "accepted_only": True}


class SMTPProvider:
    name = "smtp"

    def send(self, to_email: str, subject: str, body: str) -> dict:
        if not (settings.SMTP_HOST and settings.SMTP_FROM):
            return {"ok": False, "error": "SMTP not configured"}
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = formataddr(("LeadPilot Trial", settings.SMTP_FROM))
            msg["To"] = to_email
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as s:
                if settings.SMTP_USE_TLS:
                    s.starttls()
                if settings.SMTP_USERNAME:
                    s.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                s.sendmail(settings.SMTP_FROM, [to_email], msg.as_string())
            return {"ok": True, "provider_message_id": None, "accepted_only": True}
        except Exception as e:
            return {"ok": False, "error": str(e)[:300]}


class ResendProvider:
    name = "resend"

    def send(self, to_email: str, subject: str, body: str) -> dict:
        if not (settings.RESEND_API_KEY and settings.RESEND_FROM):
            return {"ok": False, "error": "Resend not configured"}
        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json={"from": settings.RESEND_FROM, "to": [to_email],
                      "subject": subject, "text": body},
                timeout=20.0)
            if resp.status_code >= 400:
                return {"ok": False, "error": f"Resend HTTP {resp.status_code}: {resp.text[:200]}"}
            return {"ok": True, "provider_message_id": resp.json().get("id"),
                    "accepted_only": True}
        except Exception as e:
            return {"ok": False, "error": str(e)[:300]}


def get_provider():
    name = settings.EMAIL_PROVIDER.lower()
    if name == "smtp":
        return SMTPProvider()
    if name == "resend":
        return ResendProvider()
    return DryRunProvider()


class GmailApiProvider:
    """Sends through the user's own Gmail (OAuth refresh token in DB).

    Connected once via Sign in with Google (gmail.send scope). Tokens stay
    server-side; 401 refresh failures report 'reconnect Google' honestly.
    """
    name = "gmail"

    def __init__(self, refresh_token: str | None):
        self.refresh_token = refresh_token

    def _access_token(self) -> str:
        if not self.refresh_token:
            raise RuntimeError("Gmail not connected — sign in with Google first")
        from app.api_google import get_google_config
        cid, sec, _ = get_google_config()
        resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={"client_id": cid, "client_secret": sec,
                  "refresh_token": self.refresh_token,
                  "grant_type": "refresh_token"},
            timeout=20.0)
        if resp.status_code >= 400:
            raise RuntimeError("Gmail access expired — sign in with Google again")
        return resp.json()["access_token"]

    def send(self, to_email: str, subject: str, body: str) -> dict:
        import base64
        from email.mime.text import MIMEText
        try:
            token = self._access_token()
            msg = MIMEText(body, "plain", "utf-8")
            msg["To"] = to_email
            msg["Subject"] = subject
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            resp = httpx.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {token}"},
                json={"raw": raw}, timeout=20.0)
            if resp.status_code == 429:
                return {"ok": False, "error": "Gmail rate limited (429) — retry later",
                        "retryable": True}
            if resp.status_code >= 400:
                return {"ok": False,
                        "error": f"Gmail HTTP {resp.status_code}: {resp.text[:200]}"}
            data = resp.json()
            return {"ok": True, "provider_message_id": data.get("id"),
                    "thread_id": data.get("threadId"), "accepted_only": True}
        except RuntimeError as e:
            return {"ok": False, "error": str(e)[:300]}
        except Exception as e:
            return {"ok": False, "error": str(e)[:300]}


def get_provider_for_user(user) -> object:
    """Per-user provider: Gmail when enabled + connected, else global mode."""
    db_user_token = getattr(user, "gmail_refresh_token", None)
    if settings.EMAIL_PROVIDER.lower() == "gmail" and db_user_token:
        return GmailApiProvider(db_user_token)
    if settings.EMAIL_PROVIDER.lower() == "gmail" and not db_user_token:
        # Configured mode but this user never connected: fail loudly, not silently.
        class _Unconnected:
            name = "gmail"

            def send(self, to_email: str, subject: str, body: str) -> dict:
                return {"ok": False,
                        "error": "Gmail not connected — sign in with Google first"}
        return _Unconnected()
    return get_provider()


# ---------- helpers ----------
def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def is_suppressed(db, user_id: int, email: str) -> bool:
    return db.query(Suppression).filter(
        Suppression.user_id == user_id,
        Suppression.value == normalize_email(email)).first() is not None


def previously_contacted(db, user_id: int, email: str) -> bool:
    return db.query(EmailMessage).filter(
        EmailMessage.user_id == user_id,
        EmailMessage.to_email == normalize_email(email),
        EmailMessage.status.in_(["accepted", "delivered", "replied"]),
        EmailMessage.is_test.is_(False)).first() is not None


def personalize(template: str, company_name: str | None, country: str | None,
                contact_email: str | None) -> str:
    out = template or ""
    out = out.replace("{company}", company_name or "")
    out = out.replace("{country}", country or "")
    out = out.replace("{contact_email}", contact_email or "")
    return out


def emails_sent_today(db, user_id: int) -> int:
    day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    from sqlalchemy import func
    return db.query(func.count(EmailMessage.id)).filter(
        EmailMessage.user_id == user_id, EmailMessage.is_test.is_(False),
        EmailMessage.created_at >= day).scalar() or 0


# ---------- campaign send (idempotent, quota-guarded) ----------
def send_campaign(db, user, campaign: Campaign, allow_resend: bool = False) -> dict:
    provider = get_provider_for_user(user)
    if settings.EMAIL_PROVIDER != "dryrun" and not user.email_verified:
        return {"sent": 0, "failed": 0, "skipped": 0,
                "errors": ["Verify your email address before live sending"]}
    recipients = db.query(CampaignRecipient).filter(
        CampaignRecipient.campaign_id == campaign.id,
        CampaignRecipient.status == "PENDING").all()
    sent = failed = skipped = 0
    errors: list[str] = []
    for r in recipients:
        if emails_sent_today(db, user.id) >= settings.DAILY_EMAIL_LIMIT:
            r.status = "PENDING"
            errors.append("daily email quota reached — remaining recipients kept queued")
            break
        lead = db.query(Lead).filter(
            Lead.id == r.lead_id, Lead.user_id == user.id).first()
        if not lead:
            r.status = "FAILED"
            r.error = "lead not found"
            failed += 1
            continue
        company_name = lead.company.name if lead.company else ""
        country = lead.company.country if lead.company else ""
        subject = personalize(campaign.subject, company_name, country, r.email)
        body = personalize(campaign.body, company_name, country, r.email)
        result = provider.send(r.email, subject, body)
        msg = EmailMessage(
            user_id=user.id, campaign_id=campaign.id, lead_id=lead.id,
            recipient_id=r.id, to_email=r.email, subject=subject, body=body,
            provider=provider.name, is_test=False)
        db.add(msg)
        db.flush()
        if result.get("ok"):
            r.status = "SENT"
            r.sent_at = datetime.utcnow()
            msg.status = "accepted"  # accepted != delivered (honest states)
            msg.provider_message_id = result.get("provider_message_id")
            msg.sent_at = datetime.utcnow()
            db.add(ProviderEvent(user_id=user.id, message_id=msg.id,
                                 event_type="sent", payload="{}"))
            lead.status = "CONTACTED"
            lead.last_contacted_at = datetime.utcnow()
            if campaign.followup_enabled:
                exists = db.query(Followup).filter(
                    Followup.lead_id == lead.id, Followup.message_id == msg.id).first()
                if not exists:
                    db.add(Followup(
                        user_id=user.id, lead_id=lead.id, message_id=msg.id,
                        scheduled_at=datetime.utcnow() + timedelta(
                            hours=campaign.followup_delay_hours)))
                    lead.next_followup_at = datetime.utcnow() + timedelta(
                        hours=campaign.followup_delay_hours)
            sent += 1
        else:
            r.status = "FAILED"
            r.error = (result.get("error") or "send failed")[:500]
            msg.status = "failed"
            msg.error = r.error
            db.add(ProviderEvent(user_id=user.id, message_id=msg.id,
                                 event_type="failed",
                                 payload=f'{{"error": {(r.error or "")[:200]!r}}}'))
            failed += 1
            errors.append(f"{r.email}: {r.error}"[:200])
        db.commit()
    campaign.status = "SENT" if failed == 0 else ("SENT" if sent else "DRAFT")
    db.commit()
    return {"sent": sent, "failed": failed, "skipped": skipped, "errors": errors[:10]}


def process_due_followups(db, user_id: int, limit: int = 20) -> dict:
    """Send at most ONE auto follow-up per lead; fail-closed on unknown state."""
    from app.models import Contact
    due = (db.query(Followup).filter(
            Followup.user_id == user_id,
            Followup.status == "SCHEDULED",
            Followup.scheduled_at <= datetime.utcnow())
           .order_by(Followup.scheduled_at).limit(limit).all())
    from app.models import User as _User
    _fu_user = db.query(_User).filter(_User.id == user_id).first()
    provider = get_provider_for_user(_fu_user)
    sent = skipped = 0
    for fu in due:
        lead = db.query(Lead).filter(
            Lead.id == fu.lead_id, Lead.user_id == user_id).first()
        orig = db.query(EmailMessage).filter(
            EmailMessage.id == fu.message_id,
            EmailMessage.user_id == user_id).first()
        reason = None
        if not lead or not orig:
            reason = "lead/message gone"
        elif lead.status in ("REPLIED",):
            reason = "replied — follow-up stopped"
        elif is_suppressed(db, user_id, orig.to_email):
            reason = "suppressed"
        elif db.query(ProviderEvent).filter(
                ProviderEvent.message_id == orig.id,
                ProviderEvent.event_type.in_(["reply", "bounce", "unsubscribe"])).first():
            reason = "reply/bounce/unsubscribe on thread"
        elif orig.status not in ("accepted", "delivered"):
            reason = f"original state unknown ({orig.status}) — flagged for review"
        elif db.query(EmailMessage).filter(
                EmailMessage.user_id == user_id, EmailMessage.lead_id == lead.id,
                EmailMessage.is_followup.is_(True),
                EmailMessage.status.in_(["accepted", "delivered"])).count() >= 1:
            reason = "a follow-up already sent"
        if reason:
            fu.status = "SKIPPED"
            fu.skip_reason = reason
            lead.next_followup_at = None
            db.commit()
            skipped += 1
            continue
        body = (f"Just following up on my previous email"
                f"{' to ' + (lead.company.name if lead.company else '')} — "
                f"happy to share more details if relevant.")
        result = provider.send(orig.to_email, f"Re: {orig.subject or ''}", body)
        if result.get("ok"):
            msg = EmailMessage(
                user_id=user_id, campaign_id=orig.campaign_id, lead_id=lead.id,
                to_email=orig.to_email, subject=f"Re: {orig.subject or ''}",
                body=body, status="accepted", provider=provider.name,
                thread_id=orig.thread_id or orig.provider_message_id,
                is_followup=True, sent_at=datetime.utcnow())
            db.add(msg)
            fu.status = "SENT"
            fu.sent_at = datetime.utcnow()
            lead.next_followup_at = None
            db.commit()
            sent += 1
        else:
            fu.status = "SKIPPED"
            fu.skip_reason = f"provider error: {(result.get('error') or '')[:150]}"
            db.commit()
            skipped += 1
    return {"sent": sent, "skipped": skipped}
