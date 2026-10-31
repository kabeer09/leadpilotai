"""WhatsApp: country-code fixing, availability checks, providers (dryrun|meta).

Honesty rules (mirrored from emailing.py):
- Format/region checks are FREE and offline (Google libphonenumber). They
  prove a number is dialable in the right country — they NEVER claim that
  WhatsApp is installed on it.
- Registration is only known when Meta Cloud API answers: accepted =>
  on_whatsapp; error 131026 / undeliverable => not_on_whatsapp.
- dryrun never touches a network and never marks anything 'on_whatsapp'.
- Cold (first) messages must be an approved Meta template; freeform text
  only works inside a 24h session the user opened.
"""
import hashlib
import logging
import re
from datetime import datetime

import httpx
import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberType, PhoneNumberFormat

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Country / region handling
# ---------------------------------------------------------------------------

# Company country name (as published by fair directories) -> ISO 3166-1 alpha-2
_COUNTRY_REGION = {
    "united states": "US", "usa": "US", "united states of america": "US",
    "canada": "CA", "mexico": "MX", "guatemala": "GT", "costa rica": "CR",
    "panama": "PA", "dominican republic": "DO", "puerto rico": "PR",
    "colombia": "CO", "venezuela": "VE", "ecuador": "EC", "peru": "PE",
    "chile": "CL", "argentina": "AR", "brazil": "BR", "uruguay": "UY",
    "paraguay": "PY", "bolivia": "BO",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "england": "GB",
    "ireland": "IE", "france": "FR", "germany": "DE", "netherlands": "NL",
    "belgium": "BE", "luxembourg": "LU", "switzerland": "CH", "austria": "AT",
    "italy": "IT", "spain": "ES", "portugal": "PT", "greece": "GR",
    "denmark": "DK", "sweden": "SE", "norway": "NO", "finland": "FI",
    "iceland": "IS", "poland": "PL", "czech republic": "CZ", "czechia": "CZ",
    "slovakia": "SK", "hungary": "HU", "romania": "RO", "bulgaria": "BG",
    "croatia": "HR", "serbia": "RS", "slovenia": "SI", "bosnia and herzegovina": "BA",
    "north macedonia": "MK", "albania": "AL", "montenegro": "ME",
    "lithuania": "LT", "latvia": "LV", "estonia": "EE", "belarus": "BY",
    "ukraine": "UA", "russia": "RU", "russian federation": "RU",
    "turkey": "TR", "turkiye": "TR",
    "israel": "IL", "lebanon": "LB", "jordan": "JO", "syria": "SY",
    "iraq": "IQ", "saudi arabia": "SA", "united arab emirates": "AE", "uae": "AE",
    "qatar": "QA", "kuwait": "KW", "bahrain": "BH", "oman": "OM", "yemen": "YE",
    "egypt": "EG", "morocco": "MA", "tunisia": "TN", "algeria": "DZ",
    "libya": "LY", "nigeria": "NG", "ghana": "GH", "kenya": "KE",
    "tanzania": "TZ", "uganda": "UG", "ethiopia": "ET", "rwanda": "RW",
    "south africa": "ZA", "senegal": "SN", "ivory coast": "CI", "cameroon": "CM",
    "pakistan": "PK", "bangladesh": "BD", "sri lanka": "LK", "nepal": "NP",
    "india": "IN",
    "bhutan": "BT", "maldives": "MV", "afghanistan": "AF",
    "china": "CN", "hong kong": "HK", "macau": "MO", "taiwan": "TW",
    "japan": "JP", "south korea": "KR", "korea": "KR", "north korea": "KP",
    "vietnam": "VN", "thailand": "TH", "cambodia": "KH", "laos": "LA",
    "malaysia": "MY", "singapore": "SG", "indonesia": "ID", "philippines": "PH",
    "brunei": "BN",
    "australia": "AU", "new zealand": "NZ",
    "russia (federation)": "RU",
}


def region_for(country_name: str | None) -> str | None:
    if not country_name:
        return None
    key = country_name.strip().lower().replace("&", "and")
    key = re.sub(r"\s+", " ", key)
    return _COUNTRY_REGION.get(key)


# Fair directories that omit exhibitor country: their default dial plan.
_SOURCE_DEFAULT_REGION = (
    ("highpointmarket.org", "US"),
)


def source_default_region(source_url: str | None) -> str:
    if not source_url:
        return ""
    low = source_url.lower()
    for host, region in _SOURCE_DEFAULT_REGION:
        if host in low:
            return region
    return ""


def phone_e164(raw: str | None, country_name: str | None = None,
               default_region: str | None = None) -> tuple[str, str]:
    """Resolve a scraped phone to strict E.164 ('+<digits>').

    Returns (e164_or_empty, how) with how in:
      empty | already_intl | from_country | default_region | cc_missing_plus
      | invalid | no_country
    Order for ambiguous digits: company country first, then the fair's
    default region (e.g. High Point = US), then 'digits already contain the
    country code but the + was lost'.
    """
    text = (raw or "").strip()
    if not text:
        return "", "empty"
    digits = re.sub(r"\D", "", text)
    if len(digits) < 7:
        return "", "invalid"

    # (number_to_parse, iso_region_or_None, how) — national-format candidates
    # must be parsed WITH their region; '+'-forms carry their own country.
    candidates: list[tuple[str, str | None, str]] = []
    if text.startswith("+"):
        candidates.append(("+" + digits, None, "already_intl"))
    elif text.startswith("00"):
        candidates.append(("+" + digits[2:], None, "already_intl"))
    else:
        region = region_for(country_name)
        if region:
            candidates.append((digits, region, "from_country"))
        # A bare local number the fair's own dial plan explains (e.g. High
        # Point = US: '4045551234') beats the '+'-guess below — 10-digit NANP
        # could otherwise be misread as +41 (Switzerland) etc.
        if default_region and default_region != region:
            candidates.append((digits, default_region, "default_region"))
        # Number may already carry its country code (e.g. '905070754171',
        # '9779802343024') — only the '+' was lost in scraping. Such numbers
        # are >= 11 digits, so a correct national parse above wins first.
        candidates.append(("+" + digits, None, "cc_missing_plus"))

    for num, region, how in candidates:
        try:
            pn = phonenumbers.parse(num, region)
        except NumberParseException:
            continue
        if phonenumbers.is_valid_number(pn):
            return phonenumbers.format_number(pn, PhoneNumberFormat.E164), how
    # Nothing resolved: distinguish "country unknown" from "not a real number".
    if text.startswith("+") or text.startswith("00"):
        return "", "invalid"
    if not (region_for(country_name) or default_region):
        return "", "no_country"
    return "", "invalid"


_LINE_TYPES = {
    PhoneNumberType.MOBILE: "mobile",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_or_mobile",
    PhoneNumberType.FIXED_LINE: "landline",
    PhoneNumberType.VOIP: "voip",
    PhoneNumberType.PAGER: "pager",
    PhoneNumberType.PERSONAL_NUMBER: "personal",
    PhoneNumberType.TOLL_FREE: "toll_free",
    PhoneNumberType.PREMIUM_RATE: "premium",
    PhoneNumberType.SHARED_COST: "shared_cost",
    PhoneNumberType.UAN: "uan",
    PhoneNumberType.UNKNOWN: "unknown",
}


def inspect_phone(e164: str | None) -> dict:
    """Offline verdict for one E.164 number (no network, no WhatsApp claim)."""
    out = {"e164": e164 or "", "valid": False, "region": None,
           "line_type": "unknown", "wa_likely": False}
    if not e164 or not e164.startswith("+"):
        return out
    try:
        pn = phonenumbers.parse(e164, None)
    except NumberParseException:
        return out
    out["valid"] = phonenumbers.is_valid_number(pn)
    if not out["valid"]:
        return out
    out["region"] = phonenumbers.region_code_for_number(pn)
    nt = phonenumbers.number_type(pn)
    out["line_type"] = _LINE_TYPES.get(nt, "unknown")
    out["wa_likely"] = nt in (PhoneNumberType.MOBILE,
                              PhoneNumberType.FIXED_LINE_OR_MOBILE,
                              PhoneNumberType.VOIP)
    return out


def wa_link(e164: str | None, message: str = "") -> str:
    import urllib.parse
    digits = re.sub(r"\D", "", e164 or "")
    if digits.startswith("00"):
        digits = digits[2:]
    q = f"?text={urllib.parse.quote(message)}" if message else ""
    return f"https://wa.me/{digits}{q}"


# ---------------------------------------------------------------------------
# Providers — same honesty contract as emailing.py
# ---------------------------------------------------------------------------

class DryRunWhatsAppProvider:
    name = "whatsapp-dryrun"

    def send(self, to_e164: str, variables: list[str], message: str) -> dict:
        logger.info(f"[wa-dryrun] would send to {to_e164}: {message[:60]}")
        return {"ok": True, "accepted_only": True,
                "provider_message_id": "wa-dryrun-" + hashlib.md5(
                    to_e164.encode()).hexdigest()[:12],
                # dryrun proves nothing about registration:
                "wa_status": None}

    def check(self, to_e164: str) -> dict:
        return {"ok": True, "registered": None,
                "note": "dryrun: format validated offline only — "
                        "registration not verified"}


class MetaWhatsAppProvider:
    """WhatsApp Business Cloud API (Meta). Template required for cold sends."""
    name = "whatsapp-meta"
    BASE = "https://graph.facebook.com/v20.0"

    def _configured(self) -> str | None:
        if not (settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID):
            return "Meta not configured — set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID"
        return None

    def _post(self, payload: dict) -> dict:
        try:
            resp = httpx.post(
                f"{self.BASE}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
                headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"},
                json=payload, timeout=25.0)
        except Exception as e:
            return {"ok": False, "error": f"network: {str(e)[:200]}"}
        text = resp.text
        if resp.status_code >= 400:
            err = {"ok": False, "error": f"Meta HTTP {resp.status_code}: {text[:250]}"}
            # 131026 / undeliverable = recipient not reachable on WhatsApp.
            low = text.lower()
            if "131026" in text or "not on whatsapp" in low \
                    or "undeliverable" in low or "recipient phone number is not" in low:
                err["wa_status"] = "not_on_whatsapp"
            return err
        try:
            mid = (resp.json().get("messages") or [{}])[0].get("id")
        except Exception:
            mid = None
        return {"ok": True, "provider_message_id": mid, "accepted_only": True,
                "wa_status": "on_whatsapp"}

    def send(self, to_e164: str, variables: list[str], message: str) -> dict:
        err = self._configured()
        if err:
            return {"ok": False, "error": err}
        to_digits = re.sub(r"\D", "", to_e164)
        if settings.WHATSAPP_TEMPLATE_NAME:
            comps = []
            if variables:
                comps.append({"type": "body", "parameters": [
                    {"type": "text", "text": str(v)[:200]} for v in variables]})
            payload = {"messaging_product": "whatsapp", "recipient_type": "individual",
                       "to": to_digits, "type": "template",
                       "template": {"name": settings.WHATSAPP_TEMPLATE_NAME,
                                    "language": {"code": settings.WHATSAPP_TEMPLATE_LANG},
                                    "components": comps}}
        else:
            # No approved template: freeform only works if that number opened
            # a 24h session with you. Meta will say so honestly if not.
            payload = {"messaging_product": "whatsapp", "recipient_type": "individual",
                       "to": to_digits, "type": "text",
                       "text": {"preview_url": False, "body": message[:1024]}}
        return self._post(payload)

    def check(self, to_e164: str) -> dict:
        """Live registration check = a real template probe (costs one Meta
        conversation). Meta publishes no free lookup endpoint."""
        err = self._configured()
        if err:
            return {"ok": False, "registered": None, "error": err}
        if not settings.WHATSAPP_TEMPLATE_NAME:
            return {"ok": False, "registered": None,
                    "error": "live check needs WHATSAPP_TEMPLATE_NAME"}
        res = self.send(to_e164, [getattr(self, "_probe_var", "check")], "probe")
        if res.get("ok"):
            return {"ok": True, "registered": True,
                    "note": "accepted by Meta — number is on WhatsApp"}
        if res.get("wa_status") == "not_on_whatsapp":
            return {"ok": True, "registered": False, "note": "Meta 131026"}
        return {"ok": False, "registered": None,
                "error": res.get("error", "unknown")}


def get_whatsapp_provider():
    if settings.WHATSAPP_PROVIDER.lower() == "meta":
        return MetaWhatsAppProvider()
    return DryRunWhatsAppProvider()


def whatsapp_sent_today(db, user_id: int) -> int:
    from sqlalchemy import func
    from app.models import WhatsAppMessage
    day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(func.count(WhatsAppMessage.id)).filter(
        WhatsAppMessage.user_id == user_id,
        WhatsAppMessage.created_at >= day).scalar() or 0


def template_variables(company_name: str | None, country: str | None,
                       phone: str | None, message: str | None) -> list[str]:
    """Fill WHATSAPP_TEMPLATE_VARS (comma list) in order."""
    pool = {"company": company_name or "", "country": country or "",
            "phone": phone or "", "message": (message or "")[:200]}
    names = [n.strip() for n in settings.WHATSAPP_TEMPLATE_VARS.split(",") if n.strip()]
    return [pool.get(n, "") for n in names]
