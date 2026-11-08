"""Buy/sell role + buyer-type classification over real company data.

Honesty contract (master prompt):
- Classification is a KEYWORD SIGNAL read from fields we actually store
  (name, category, product_summary, address, products). It is never a
  verified fact about the company.
- Directory / trade-fair presence is NOT evidence of active buying
  activity — every API payload built on these helpers must carry the
  directory_discovery disclaimer.
- No data is invented: if nothing matches, the company is "Unclassified".

Pure functions only — no DB, no network — so they are trivially testable.
"""
from __future__ import annotations

import json
import re

# Canonical buyer types (master prompt categories) in signal priority order:
# buy-side signals win over the generic manufacturer signal when several
# match the same company text, because buyer discovery cares about the
# purchasing role first. All matched types are kept for filtering/facets.
BUYER_TYPES = [
    "Importers",
    "Distributors",
    "Wholesalers",
    "Retailers",
    "Manufacturers",
    "Trading",
    "Procurement",
    "Corporate gifting",
    "Unclassified",
]

# type -> compiled pattern (explicit suffixes; never bare \w* which would
# match "important" for import, "workshop" for shop, "storage" for store…)
_TYPE_PATTERNS = {
    "Importers": re.compile(
        r"\b(?:importer|importers|import|imports|importing|imported|"
        r"sourcing|buying\s+agent|buying\s+house|foreign\s+trade)\b", re.I),
    "Distributors": re.compile(
        r"\b(?:distributor|distributors|distribution|distributing|"
        r"distributorship)\b", re.I),
    "Wholesalers": re.compile(
        r"\b(?:wholesaler|wholesalers|wholesale|wholesaling|"
        r"bulk\s+supplier)\b", re.I),
    "Retailers": re.compile(
        r"\b(?:retailer|retailers|retail|retailing|boutique|"
        r"online\s+shop|concept\s+store)\b|\bstore\b|\bshops?\b", re.I),
    "Manufacturers": re.compile(
        r"\b(?:manufacturer|manufacturers|manufacturing|manufactured|"
        r"factory|factories|own\s+production|oem|odm|handmade\s+by)\b", re.I),
    "Trading": re.compile(
        r"\b(?:trading|trader|traders|general\s+trading|"
        r"export\s+house|import\s+export\s+house)\b", re.I),
    "Procurement": re.compile(
        r"\b(?:procurement|purchasing|purchase\s+department|"
        r"sourcing\s+office|buying\s+office)\b", re.I),
    "Corporate gifting": re.compile(
        r"\b(?:corporate\s+gift|corporate\s+gifts|corporate\s+gifting|"
        r"business\s+gift|business\s+gifting|promotional\s+gift|"
        r"promotional\s+items|promotional\s+products|gifting\s+solutions?|"
        r"custom\s+gift|customised\s+gift|customized\s+gift)\b", re.I),
}

ROLE_BY_TYPE = {
    "Importers": "buyer",
    "Distributors": "buyer",
    "Wholesalers": "buyer",
    "Retailers": "buyer",
    "Procurement": "buyer",
    "Corporate gifting": "buyer",
    "Manufacturers": "supplier",
    "Trading": "trading",
    "Unclassified": "unclassified",
}

ROLES = ["buyer", "supplier", "trading", "unclassified"]

# Cross-border trade activity: text signal only (we hold no shipment data).
_IMPORT_PATTERNS = {
    "import": re.compile(
        r"\b(?:importer|importers|import|imports|importing|imported)\b", re.I),
    "export": re.compile(
        r"\b(?:exporter|exporters|export|exports|exporting|exported)\b", re.I),
    "sourcing": re.compile(r"\bsourcing\b", re.I),
    "international trade": re.compile(
        r"\b(?:international\s+trade|global\s+trade|foreign\s+trade|"
        r"cross[-\s]border\s+trade)\b", re.I),
    "overseas": re.compile(r"\boverseas\b", re.I),
}

# Fields scanned for signals (only fields the app actually stores).
TEXT_FIELDS = ("name", "category", "product_summary", "address", "products")

DISCLAIMER = (
    "Classified from directory listings and public company text using "
    "keyword signals. Presence in a directory or trade fair is NOT "
    "evidence of active buying activity — verify before outreach."
)


def company_text_fields(company, extra_products: list | None = None) -> dict:
    """Extract the scannable text fields from a Company row (no invention)."""
    products = extra_products
    if products is None and getattr(company, "products_json", None):
        try:
            raw = json.loads(company.products_json)
        except Exception:
            raw = []
        products = raw if isinstance(raw, list) else []
    product_text = " ".join(
        str(p.get("name", "")) + " " + str(p.get("category", ""))
        for p in (products or []) if isinstance(p, dict))
    return {
        "name": company.name or "",
        "category": company.category or "",
        "product_summary": company.product_summary or "",
        "address": company.address or "",
        "products": product_text,
    }


def classify_fields(fields: dict) -> dict:
    """Classify a company from its text fields.

    Returns:
      buyer_type   highest-priority matching type (or "Unclassified")
      types        every matching type (filtering uses membership)
      role         role of the primary type: buyer|supplier|trading|unclassified
      roles        roles of every matching type (facet/filter membership)
      signals      [{type, keyword, field}] evidence rows — what matched
                   and in which stored field
    """
    signals: list[dict] = []
    matched_types: list[str] = []
    for btype, pattern in _TYPE_PATTERNS.items():
        for field in TEXT_FIELDS:
            text = fields.get(field) or ""
            if not text:
                continue
            m = pattern.search(text)
            if m:
                matched_types.append(btype)
                signals.append({"type": btype, "keyword": m.group(0).strip(),
                                "field": field})
                break  # one evidence row per type is enough
    if not matched_types:
        return {"buyer_type": "Unclassified", "types": ["Unclassified"],
                "role": "unclassified", "roles": ["unclassified"],
                "signals": []}
    roles = []
    for t in matched_types:
        r = ROLE_BY_TYPE[t]
        if r not in roles:
            roles.append(r)
    return {
        "buyer_type": matched_types[0],   # priority order
        "types": matched_types,
        "role": ROLE_BY_TYPE[matched_types[0]],
        "roles": roles,
        "signals": signals,
    }


def classify_company(company) -> dict:
    """Convenience wrapper: classify a Company row from its stored fields."""
    return classify_fields(company_text_fields(company))


def import_activity(fields: dict) -> dict:
    """Cross-border import/export activity as a TEXT SIGNAL.

    Returns {"active": bool, "signals": [{"signal", "keyword", "field"}]}.
    """
    found: list[dict] = []
    for signal, pattern in _IMPORT_PATTERNS.items():
        for field in TEXT_FIELDS:
            text = fields.get(field) or ""
            if not text:
                continue
            m = pattern.search(text)
            if m:
                found.append({"signal": signal,
                              "keyword": m.group(0).strip(),
                              "field": field})
                break
    return {"active": bool(found), "signals": found}


def type_definitions() -> list[dict]:
    """Static buyer-type catalogue for filter UIs (label -> role)."""
    return [{"value": t, "role": ROLE_BY_TYPE[t]} for t in BUYER_TYPES]
