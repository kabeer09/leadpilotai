"""Source capability registry — maps each fair to its access method and status."""
from enum import Enum


class SourceStatus(str, Enum):
    READY = "READY"
    READY_WITH_LIMITATIONS = "READY_WITH_LIMITATIONS"
    MANUAL_IMPORT_REQUIRED = "MANUAL_IMPORT_REQUIRED"
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    DISABLED_BY_POLICY = "DISABLED_BY_POLICY"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"


class AccessMethod(str, Enum):
    AUTOMATED_API = "automated_api"
    AUTOMATED_CRAWL = "automated_crawl"
    BROWSER_RENDERED = "browser_rendered"
    MANUAL_CSV = "manual_csv"
    MANUAL_URL = "manual_url"
    DISABLED = "disabled"


# Source capability matrix
SOURCES = [
    {
        "id": "ambiente",
        "name": "Ambiente Frankfurt",
        "base_url": "https://ambiente.messefrankfurt.com",
        "adapter": "fair_adapters._ambiente_records",
        "status": SourceStatus.READY,
        "access_method": AccessMethod.AUTOMATED_API,
        "credentials": "None (public API)",
        "automation_permitted": True,
        "notes": "OpenDetails API provides emails, phones, addresses, booth numbers. Verified working.",
        "fallback": "Manual CSV import",
    },
    {
        "id": "atlanta",
        "name": "Atlanta Market",
        "base_url": "https://www.atlantamarket.com",
        "adapter": "fair_adapters._atlanta_profiles",
        "status": SourceStatus.READY,
        "access_method": AccessMethod.AUTOMATED_API,
        "credentials": "None (public API)",
        "automation_permitted": True,
        "notes": "OpenDetails API provides emails, phones, addresses, booth numbers. Verified working.",
        "fallback": "Manual CSV import",
    },
    {
        "id": "highpoint",
        "name": "High Point Market",
        "base_url": "https://www.highpointmarket.org",
        "adapter": "fair_adapters._highpoint_profiles",
        "status": SourceStatus.READY_WITH_LIMITATIONS,
        "access_method": AccessMethod.AUTOMATED_API,
        "credentials": "None (public API)",
        "automation_permitted": True,
        "notes": "Autocomplete API provides profile URLs. Emails not published on pages. Phones, addresses, booth numbers available.",
        "fallback": "Manual CSV import or contact form outreach",
    },
    {
        "id": "hometex",
        "name": "Hometex Turkey",
        "base_url": "https://hometex.com.tr",
        "adapter": "fair_adapters._hometex_profiles",
        "status": SourceStatus.READY,
        "access_method": AccessMethod.AUTOMATED_CRAWL,
        "credentials": "None (public pages)",
        "automation_permitted": True,
        "notes": "Exhibitor profile pages contain emails (punycode-encoded), phones, addresses, booth numbers. Adapter added.",
        "fallback": "Manual CSV import",
    },
    {
        "id": "lasvegas",
        "name": "Las Vegas Market",
        "base_url": "https://www.lasvegasmarket.com",
        "adapter": "fair_adapters._lasvegas_profiles",
        "status": SourceStatus.READY,
        "access_method": AccessMethod.AUTOMATED_API,
        "credentials": "None (public API)",
        "automation_permitted": True,
        "notes": "A-Z API provides emails, phones, addresses. Verified working.",
        "fallback": "Manual CSV import",
    },
    {
        "id": "ihgf",
        "name": "IHGF Delhi Fair",
        "base_url": "https://www.ihgfdelhifair.com",
        "adapter": None,
        "status": SourceStatus.MANUAL_IMPORT_REQUIRED,
        "access_method": AccessMethod.MANUAL_CSV,
        "credentials": "None",
        "automation_permitted": False,
        "notes": "Member directory is Drupal search form requiring JS interaction. Not reliably automatable.",
        "fallback": "Manual CSV import or manual profile URL entry",
    },
    {
        "id": "maisonobjet",
        "name": "Maison&Objet",
        "base_url": "https://www.maison-objet.com",
        "adapter": None,
        "status": SourceStatus.MANUAL_IMPORT_REQUIRED,
        "access_method": AccessMethod.MANUAL_CSV,
        "credentials": "None",
        "automation_permitted": False,
        "notes": "Fully JavaScript-rendered, empty HTML even with Playwright. Not automatable.",
        "fallback": "Manual CSV import or manual profile URL entry",
    },
    {
        "id": "inspiredhome",
        "name": "The Inspired Home Show",
        "base_url": "https://www.theinspiredhomeshow.com",
        "adapter": None,
        "status": SourceStatus.TEMPORARILY_UNAVAILABLE,
        "access_method": AccessMethod.MANUAL_CSV,
        "credentials": "None",
        "automation_permitted": False,
        "notes": "Returns 403 Forbidden. Blocks all automated access.",
        "fallback": "Manual CSV import",
    },
    {
        "id": "nynow",
        "name": "NY NOW",
        "base_url": "https://nynow.a2zinc.net",
        "adapter": None,
        "status": SourceStatus.TEMPORARILY_UNAVAILABLE,
        "access_method": AccessMethod.MANUAL_CSV,
        "credentials": "None",
        "automation_permitted": False,
        "notes": "Returns 403 Forbidden. Blocks all automated access.",
        "fallback": "Manual CSV import",
    },
    {
        "id": "springfair",
        "name": "Spring Fair",
        "base_url": "https://www.springfair.com",
        "adapter": "fair_adapters._springfair_profiles",
        "status": SourceStatus.READY_WITH_LIMITATIONS,
        "access_method": AccessMethod.AUTOMATED_CRAWL,
        "credentials": "None (public pages)",
        "automation_permitted": True,
        "notes": "Exhibitor profile pages exist. Company name and social links available. No emails or phones published.",
        "fallback": "Manual CSV import",
    },
    {
        "id": "topdrawer",
        "name": "Top Drawer",
        "base_url": "https://www.topdrawer.co.uk",
        "adapter": "fair_adapters._topdrawer_profiles",
        "status": SourceStatus.READY_WITH_LIMITATIONS,
        "access_method": AccessMethod.AUTOMATED_CRAWL,
        "credentials": "None (public pages)",
        "automation_permitted": True,
        "notes": "Exhibitor modal pages have phone numbers and websites but no emails.",
        "fallback": "Manual CSV import",
    },
    {
        "id": "reedgift",
        "name": "Reed Gift Fairs",
        "base_url": "https://www.reedgiftfairs.com.au",
        "adapter": None,
        "status": SourceStatus.MANUAL_IMPORT_REQUIRED,
        "access_method": AccessMethod.MANUAL_CSV,
        "credentials": "None",
        "automation_permitted": False,
        "notes": "No exhibitor directory links found on the website.",
        "fallback": "Manual CSV import",
    },
]


def get_source(source_id: str) -> dict | None:
    for s in SOURCES:
        if s["id"] == source_id:
            return s
    return None


def get_all_sources() -> list[dict]:
    return SOURCES


def get_automated_sources() -> list[dict]:
    return [s for s in SOURCES if s["automation_permitted"]]


def get_manual_sources() -> list[dict]:
    return [s for s in SOURCES if not s["automation_permitted"]]
