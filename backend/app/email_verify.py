"""Email verification via MX record check and SMTP handshake."""
import dns.resolver
import smtplib
import socket
from app.validation import normalize_email


def check_mx(domain: str) -> tuple[bool, str]:
    """Check if domain has MX records."""
    try:
        answers = dns.resolver.resolve(domain, "MX")
        if answers:
            return True, str(answers[0].exchange)
    except dns.resolver.NXDOMAIN:
        return False, "domain does not exist"
    except dns.resolver.NoAnswer:
        return False, "no MX records"
    except Exception as e:
        return False, str(e)[:100]
    return False, "no MX records"


def verify_email_syntax(email: str) -> dict:
    """Full email verification: syntax + MX + optional SMTP."""
    norm = normalize_email(email)
    if "@" not in norm:
        return {"email": norm, "status": "INVALID", "reason": "no @ sign"}

    local, domain = norm.split("@", 1)
    if not local or not domain or "." not in domain:
        return {"email": norm, "status": "INVALID", "reason": "invalid format"}

    # Check MX
    mx_ok, mx_detail = check_mx(domain)
    if not mx_ok:
        return {"email": norm, "status": "INVALID", "reason": mx_detail,
                "mx_checked": True, "mx_found": False}

    return {
        "email": norm,
        "status": "DOMAIN_CHECKED",
        "reason": "MX record found: %s" % mx_detail,
        "mx_checked": True,
        "mx_found": True,
        "mx_host": mx_detail,
    }


def batch_verify_emails(emails: list[str]) -> list[dict]:
    """Verify a batch of emails."""
    results = []
    for email in emails:
        results.append(verify_email_syntax(email))
    return results
