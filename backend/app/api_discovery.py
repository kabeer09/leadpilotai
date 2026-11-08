"""Buyer/Supplier Discovery API — filters over real workspace data.

Extends (never replaces) the existing CRM: discovery classifies companies
already ingested from trade-fair directories, website crawls and the GB
business-lead sync. No data is fabricated; classification is a keyword
signal with per-row evidence (matched keyword + stored field).

Endpoints (prefix /api/discovery):
  GET    /buyers            filtered + classified company discovery
  GET    /export            CSV export with source-attribution header
  GET    /saved-searches    saved discovery searches (kind="buyer")
  POST   /saved-searches
  DELETE /saved-searches/{id}
"""
from datetime import datetime

import csv
import io
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Company, Contact, Source
from app.models_trade import SavedSearch
from app.deps import get_current_user, audit, rate_limit
from app.config import settings
from app import discovery as disc

router = APIRouter(prefix="/api/discovery", tags=["discovery"])

RECORD_TYPE = "directory_discovery"
MAX_SCAN = 5000  # classification is in-process; bound the scan
MAX_EXPORT = 5000


def _source_map(db: Session, user_id: int) -> dict[int, Source]:
    rows = db.query(Source).filter(Source.user_id == user_id).all()
    return {s.id: s for s in rows}


def _contact_map(db: Session, company_ids: list[int]) -> dict[int, list]:
    rows = db.query(Contact).filter(Contact.company_id.in_(company_ids)).all()
    out: dict[int, list] = {}
    for c in rows:
        out.setdefault(c.company_id, []).append(c)
    return out


def _prefilter(db: Session, user_id: int, *, q=None, country=None,
               category=None, source_id=None, hall=None, has_website=None,
               has_email=None, has_phone=None):
    """SQL-level filters (cheap) before in-process classification."""
    qry = db.query(Company).filter(Company.user_id == user_id,
                                   Company.lifecycle == "active")
    if q:
        like = f"%{q}%"
        qry = qry.filter(Company.name.ilike(like) |
                         Company.product_summary.ilike(like) |
                         Company.category.ilike(like) |
                         Company.address.ilike(like))
    if country:
        qry = qry.filter(Company.country == country)
    if category:
        qry = qry.filter(Company.category.ilike(f"%{category}%"))
    if source_id:
        qry = qry.filter(Company.source_id == source_id)
    if hall:
        qry = qry.filter(Company.hall.ilike(f"%{hall}%"))
    if has_website is True:
        qry = qry.filter(Company.website.isnot(None), Company.website != "")
    if has_website is False:
        qry = qry.filter((Company.website.isnull()) | (Company.website == ""))
    if has_email is True:
        qry = qry.filter(Company.id.in_(
            db.query(Contact.company_id).filter(
                Contact.user_id == user_id, Contact.kind == "EMAIL")))
    if has_email is False:
        qry = qry.filter(~Company.id.in_(
            db.query(Contact.company_id).filter(
                Contact.user_id == user_id, Contact.kind == "EMAIL")))
    if has_phone is True:
        qry = qry.filter(Company.id.in_(
            db.query(Contact.company_id).filter(
                Contact.user_id == user_id,
                Contact.kind.in_(["PHONE", "WHATSAPP"]))))
    if has_phone is False:
        qry = qry.filter(~Company.id.in_(
            db.query(Contact.company_id).filter(
                Contact.user_id == user_id,
                Contact.kind.in_(["PHONE", "WHATSAPP"]))))
    return qry.order_by(Company.name.asc()).limit(MAX_SCAN)


def _classify_rows(rows) -> list[dict]:
    """Attach classification + import-activity signals to raw Company rows."""
    out = []
    for c in rows:
        fields = disc.company_text_fields(c)
        cls = disc.classify_fields(fields)
        act = disc.import_activity(fields)
        out.append({"row": c, "fields": fields, "cls": cls, "act": act})
    return out


def _python_filters(items, *, buyer_type=None, role=None,
                    import_activity=None):
    out = items
    if buyer_type:
        out = [i for i in out if buyer_type in i["cls"]["types"]]
    if role:
        out = [i for i in out if role in i["cls"]["roles"]]
    if import_activity is not None:
        out = [i for i in out if i["act"]["active"] is import_activity]
    return out


def _facets(items) -> dict:
    """Signal counts (multi-label: a company can match several types) over
    the SQL-prefiltered set, ignoring type/role/import filters so dropdown
    counts stay meaningful."""
    type_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    for i in items:
        for t in i["cls"]["types"]:
            type_counts[t] = type_counts.get(t, 0) + 1
        for r in i["cls"]["roles"]:
            role_counts[r] = role_counts.get(r, 0) + 1
    return {
        "buyer_types": [{"value": t, "count": type_counts.get(t, 0)}
                        for t in disc.BUYER_TYPES],
        "roles": [{"value": r, "count": role_counts.get(r, 0)}
                  for r in disc.ROLES],
    }


def _global_facets(db: Session, user_id: int) -> dict:
    """Stable filter dropdowns: distinct countries/categories/fairs with
    counts over the whole workspace (never shrink as filters change)."""
    def _grp(col, nonempty: bool = False):
        conds = [Company.user_id == user_id,
                 Company.lifecycle == "active", col.isnot(None)]
        if nonempty:
            conds.append(col != "")
        return (db.query(col, func.count(Company.id))
                .filter(*conds)
                .group_by(col).order_by(func.count(Company.id).desc())
                .limit(80).all())
    return {
        "countries": [{"value": k, "count": v} for k, v in _grp(
            Company.country, nonempty=True)],
        "categories": [{"value": k, "count": v} for k, v in _grp(
            Company.category, nonempty=True)],
        "source_ids": [{"value": k, "count": v} for k, v in _grp(
            Company.source_id)],
    }


def _sort_items(items, sort, order):
    def _default(i):
        return (i["row"].name or "").lower()
    keyfn = {
        "name": _default,
        "country": lambda i: (i["row"].country or "\uffff").lower(),
        "type": lambda i: i["cls"]["buyer_type"].lower(),
        "updated": lambda i: i["row"].updated_at or datetime.min,
    }.get(sort, _default)
    items = sorted(items, key=keyfn)
    if order == "desc":
        items = list(reversed(items))
    return items


def _item_out(i, smap, cmap) -> dict:
    c = i["row"]
    cs = cmap.get(c.id, [])
    emails = sorted({x.value for x in cs if x.kind == "EMAIL"})
    phones = sorted({x.value for x in cs
                     if x.kind in ("PHONE", "WHATSAPP")})
    src = smap.get(c.source_id) if c.source_id else None
    return {
        "id": c.id, "name": c.name,
        "country": c.country, "city": c.city, "website": c.website,
        "category": c.category, "hall": c.hall, "booth": c.booth,
        "product_summary": (c.product_summary or "")[:220],
        "profile_url": c.profile_url,
        "source_id": c.source_id,
        "fair": src.name if src else None,
        "buyer_type": i["cls"]["buyer_type"],
        "buyer_types": i["cls"]["types"],
        "role": i["cls"]["role"],
        "roles": i["cls"]["roles"],
        "signals": i["cls"]["signals"],
        "import_activity": i["act"]["active"],
        "import_signals": i["act"]["signals"],
        "emails": emails, "phones": phones,
        "contact_complete": bool(emails or phones),
        "lead_status": c.lead_status, "updated_at": c.updated_at,
    }


def _run_query(db, user, *, q=None, country=None, category=None,
               source_id=None, hall=None, has_website=None, has_email=None,
               has_phone=None, buyer_type=None, role=None,
               import_activity=None):
    pre = _prefilter(db, user.id, q=q, country=country, category=category,
                     source_id=source_id, hall=hall, has_website=has_website,
                     has_email=has_email, has_phone=has_phone).all()
    items = _classify_rows(pre)
    facets = _facets(items)
    facets.update(_global_facets(db, user.id))
    items = _python_filters(items, buyer_type=buyer_type, role=role,
                            import_activity=import_activity)
    return items, facets, len(pre)


def _envelope_source(db: Session, user_id: int) -> dict:
    linked = db.query(func.count(Company.id)).filter(
        Company.user_id == user_id, Company.lifecycle == "active",
        Company.source_id.isnot(None)).scalar() or 0
    active = db.query(func.count(Company.id)).filter(
        Company.user_id == user_id, Company.lifecycle == "active").scalar() or 0
    return {
        "name": "LeadPilot workspace company database",
        "record_type": RECORD_TYPE,
        "origins": {
            "crawled_directories_and_fairs": linked,
            "unlinked_e.g._gb_sync": active - linked,
        },
        "coverage": f"{active} active companies in this workspace",
        "classification": "keyword signals over stored company text "
                          "(name/category/summary/address/products)",
        "disclaimer": disc.DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# GET /buyers
# ---------------------------------------------------------------------------

@router.get("/buyers")
def discover_buyers(
    q: str | None = None, country: str | None = None,
    category: str | None = None, source_id: int | None = None,
    hall: str | None = None,
    buyer_type: str | None = None, role: str | None = None,
    import_activity: bool | None = None,
    has_website: bool | None = None, has_email: bool | None = None,
    has_phone: bool | None = None,
    sort: str = "name", order: str = "asc",
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    if buyer_type and buyer_type not in disc.BUYER_TYPES:
        raise HTTPException(status_code=422,
                            detail=f"Unknown buyer_type. Valid: "
                                   f"{', '.join(disc.BUYER_TYPES)}")
    if role and role not in disc.ROLES:
        raise HTTPException(status_code=422,
                            detail=f"Unknown role. Valid: "
                                   f"{', '.join(disc.ROLES)}")
    items, facets, scanned = _run_query(
        db, user, q=q, country=country, category=category,
        source_id=source_id, hall=hall, has_website=has_website,
        has_email=has_email, has_phone=has_phone, buyer_type=buyer_type,
        role=role, import_activity=import_activity)
    items = _sort_items(items, sort, order)
    total = len(items)
    page_rows = items[(page - 1) * page_size: page * page_size]
    smap = _source_map(db, user.id)
    cmap = _contact_map(db, [i["row"].id for i in page_rows])
    return {
        "record_type": RECORD_TYPE,
        "source": _envelope_source(db, user.id),
        "disclaimer": disc.DISCLAIMER,
        "facet_note": "Signal counts are multi-label — one company can "
                      "match several buyer types.",
        "total": total, "page": page, "page_size": page_size,
        "scanned": scanned,
        "facets": facets,
        "type_definitions": disc.type_definitions(),
        "items": [_item_out(i, smap, cmap) for i in page_rows],
    }


# ---------------------------------------------------------------------------
# GET /export  (CSV with attribution header)
# ---------------------------------------------------------------------------

def _export_rows(db, user, params) -> list[dict]:
    items, _, _ = _run_query(db, user, **params)
    items = _sort_items(items, "name", "asc")[:MAX_EXPORT]
    smap = _source_map(db, user.id)
    cmap = _contact_map(db, [i["row"].id for i in items])
    return [_item_out(i, smap, cmap) for i in items]


@router.get("/export")
def export_buyers(
    q: str | None = None, country: str | None = None,
    category: str | None = None, source_id: int | None = None,
    hall: str | None = None,
    buyer_type: str | None = None, role: str | None = None,
    import_activity: bool | None = None,
    has_website: bool | None = None, has_email: bool | None = None,
    has_phone: bool | None = None,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    rows = _export_rows(db, user, dict(
        q=q, country=country, category=category, source_id=source_id,
        hall=hall, buyer_type=buyer_type, role=role,
        import_activity=import_activity, has_website=has_website,
        has_email=has_email, has_phone=has_phone))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# Source: LeadPilot workspace company database "
                "(trade-fair directories, website crawls, GB sync)"])
    w.writerow(["# Record type: directory_discovery"])
    w.writerow(["# Buyer type / role / import activity: KEYWORD SIGNALS "
                "over stored company text — not verified facts"])
    w.writerow([f"# Retrieved: {datetime.utcnow().isoformat()}Z"])
    w.writerow([f"# Rows: {len(rows)}"])
    w.writerow(["# Disclaimer: Directory or trade-fair presence is NOT "
                "evidence of active buying activity. Verify before "
                "outreach."])
    w.writerow(["ID", "Name", "Buyer Type", "Role", "Matched Keywords",
                "Import Activity", "Import Signals", "Country", "City",
                "Category", "Fair/Source", "Hall", "Booth", "Website",
                "Profile URL", "Emails", "Phones", "Product Summary",
                "Lead Status", "Updated At"])
    for r in rows:
        w.writerow([
            r["id"], r["name"], r["buyer_type"], r["role"],
            "; ".join(f"{s['keyword']} ({s['field']})"
                      for s in r["signals"]),
            "yes" if r["import_activity"] else "no",
            "; ".join(s["keyword"] for s in r["import_signals"]),
            r["country"] or "", r["city"] or "", r["category"] or "",
            r["fair"] or "", r["hall"] or "", r["booth"] or "",
            r["website"] or "", r["profile_url"] or "",
            "; ".join(r["emails"]), "; ".join(r["phones"]),
            r["product_summary"], r["lead_status"],
            r["updated_at"].isoformat() if r["updated_at"] else "",
        ])
    buf.seek(0)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M")
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename=buyer-discovery-{stamp}.csv"})


# ---------------------------------------------------------------------------
# saved searches (kind="buyer", tenant-scoped; same table as trade searches)
# ---------------------------------------------------------------------------

class SavedIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    query: dict = Field(default_factory=dict)


def _saved_out(s: SavedSearch) -> dict:
    try:
        qry = json.loads(s.query_json or "{}")
    except Exception:
        qry = {}
    return {"id": s.id, "name": s.name, "kind": s.kind, "query": qry,
            "created_at": s.created_at}


@router.get("/saved-searches")
def list_saved(db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    rows = (db.query(SavedSearch)
            .filter(SavedSearch.user_id == user.id,
                    SavedSearch.kind == "buyer")
            .order_by(SavedSearch.id.desc()).limit(100).all())
    return {"count": len(rows), "items": [_saved_out(s) for s in rows]}


@router.post("/saved-searches", status_code=201,
             dependencies=[Depends(rate_limit(
                 "discovery_saved", settings.RATE_SENSITIVE_PER_MIN))])
def create_saved(payload: SavedIn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name is required")
    exists = (db.query(SavedSearch)
              .filter(SavedSearch.user_id == user.id,
                      SavedSearch.kind == "buyer",
                      SavedSearch.name == name).first())
    if exists:
        raise HTTPException(status_code=409,
                            detail=f"A saved search named '{name}' already "
                                   "exists for this account")
    s = SavedSearch(user_id=user.id, name=name, kind="buyer",
                    query_json=json.dumps(payload.query,
                                          ensure_ascii=False))
    db.add(s)
    db.commit()
    db.refresh(s)
    audit(db, user.id, "discovery_saved_search_create", "saved_search",
          s.id, f"buyer: {name}")
    db.commit()
    return _saved_out(s)


@router.delete("/saved-searches/{search_id}", status_code=204)
def delete_saved(search_id: int, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    s = (db.query(SavedSearch)
         .filter(SavedSearch.id == search_id,
                 SavedSearch.user_id == user.id,
                 SavedSearch.kind == "buyer").first())
    if s is None:
        raise HTTPException(status_code=404, detail="Saved search not found")
    db.delete(s)
    db.commit()
    audit(db, user.id, "discovery_saved_search_delete", "saved_search",
          s.id, s.name[:200])
    db.commit()
    return None
