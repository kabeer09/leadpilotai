"""Contact validation: syntax, junk, domain-match scoring, primary picking.

Ported from the proven single-tenant pipeline, adapted to the trial's
per-value Contact rows. Rules:
- role emails (info@, sales@) are VALID business contacts — only
  non-contactable senders (noreply, bounce, mailer-daemon) are rejected;
- the address matching the company's own domain wins primary;
- invalid rows are flagged, never deleted.
"""
import re

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
JUNK_MARKERS = ("example.com", "example.org", "test.com", ".png", ".jpg",
                ".jpeg", ".gif", ".webp", ".svg", "sentry", "wixpress")
NON_CONTACTABLE = ("noreply", "no-reply", "donotreply", "do-not-reply",
                   "bounce", "bounces", "mailer-daemon", "postmaster")
FREE_PROVIDERS = ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
                  "live.com", "icloud.com", "aol.com", "protonmail.com")


def normalize_email(value: str | None) -> str:
    if not value:
        return ""
    email = value.strip().lower()
    if "@" not in email:
        return ""
    local, _, domain = email.partition("@")
    local = local.split("+")[0]
    return f"{local}@{domain}" if local and domain else ""


def normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) < 7 or len(digits) > 16:
        return ""
    if len(digits) == 8 and digits[:2] in ("19", "20") \
            and "01" <= digits[4:6] <= "12" and "01" <= digits[6:8] <= "31":
        return ""
    return ("+" + digits) if text.startswith("+") else digits


def validate_contact_email(email: str | None) -> dict:
    """Returns {valid, reason}. Scraped first, judged second."""
    if not email or not EMAIL_RE.match(email.strip()):
        return {"valid": False, "reason": "bad syntax"}
    low = email.strip().lower()
    if any(j in low for j in JUNK_MARKERS):
        return {"valid": False, "reason": "junk/marker domain"}
    local = low.split("@")[0]
    if any(nc in local for nc in NON_CONTACTABLE):
        return {"valid": False, "reason": "non-contactable sender"}
    return {"valid": True, "reason": "ok"}


def filter_valid_emails(emails: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for e in emails or []:
        norm = normalize_email(e)
        if norm and norm not in seen and validate_contact_email(norm)["valid"]:
            seen.add(norm)
            out.append(norm)
    return out


def domain_of(value: str | None) -> str:
    if not value:
        return ""
    v = value.strip().lower()
    host = v.split("@")[-1] if "@" in v and "://" not in v else v
    if "://" in host:
        from urllib.parse import urlparse
        host = urlparse(host).hostname or host
    host = host.lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _base_domain(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def domain_matches(email: str, website: str | None) -> bool:
    if not email or not website:
        return False
    eh, wh = domain_of(email), domain_of(website)
    return bool(eh and wh) and _base_domain(eh) == _base_domain(wh)


def pick_best_email(emails: list[str], website: str | None) -> str | None:
    """Primary pick: own-domain match first, free mailboxes last, else first."""
    valid = filter_valid_emails(emails)
    if not valid:
        return None
    for e in valid:
        if domain_matches(e, website):
            return e
    non_free = [e for e in valid if domain_of(e) not in FREE_PROVIDERS]
    return (non_free or valid)[0]
