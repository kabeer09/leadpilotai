"""Public directory API — the landing page's free buyer/product search.

Business surface for the long-term model: guests search the PUBLISHED
exhibitor directory (the operator account's crawled fair data) with a free
daily limit; contact reveal requires an account — the conversion gate from
"free for a limited time" into paying customers later.

Honesty contract (same as the workspace API):
  * every result carries source attribution (fair + source URL) and the
    fair-participation disclaimer — presence at a fair is NOT current
    buying/selling activity and not verified contactability;
  * guest payloads NEVER contain emails, phones or websites (they stay
    locked behind sign-in) and nothing is guessed or fabricated;
  * reveal returns each contact with its source URL + extraction method;
  * only rows attributable to a registered trade fair are published.

Endpoints (prefix /api/directory):
  GET /meta             public totals + filter options (hero counter)
  GET /search           free-limited guest search (per-IP daily cap)
  GET /company/{id}     contact reveal — signed-in only, evidence per row
"""
from datetime import date
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Company, Contact, Source
from app.deps import get_current_user, rate_limit, audit
from app.api_exhibitors import _find_registry, _host
from app.source_registry import get_all_sources

router = APIRouter(prefix="/api/directory", tags=["directory"])

# The operator account whose crawls back the public directory.
# (Tests may re-point this at their own tenant.)
PUBLISHED_USER_ID = 1
# Free-for-limited-time guest allowance — the honest conversion hook.
FREE_SEARCHES_PER_DAY = 40
SEARCH_RATE_PER_MIN = 20
REVEAL_RATE_PER_MIN = 30
META_RATE_PER_MIN = 60
PAGE_SIZE_MAX = 10
Q_MAX_LEN = 100

RECORD_TYPE = "fair_directory_listing"
DISCLAIMER = (
    "Exhibitor listing from the named trade-fair directory. Participation "
    "indicates presence at that fair — NOT current buying/selling activity "
    "and not verified contactability. Verify before outreach."
)
LOCKED_NOTE = (
    "Websites and contact details are locked for guests — create a free "
    "account to reveal them (directory free for a limited time)."
)

# (host, label) for every registered fair — static registry data.
_REG_HOSTS = [(_host(s["base_url"]), s["name"]) for s in get_all_sources()]

# ip -> (utc-date, searches used that day)
_day_hits: dict[str, tuple[str, int]] = {}


# ----------------------------------------------------------------- helpers

def _host_any(u: str | None) -> str:
    """Host of a URL (or bare host) with a leading www. stripped."""
    if not u:
        return ""
    try:
        h = urlparse(u if "://" in u else "//" + u).netloc or ""
    except ValueError:
        # Malformed stored URL ("http://foo[bar.com") — no host, never a
        # 500 on the public search.
        return ""
    h = h.lower()
    return h[4:] if h.startswith("www.") else h


def _like_esc(s: str) -> str:
    return (s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))


def _registry_sources(db: Session) -> list[dict]:
    """Published account's crawl sources that map to a registered fair."""
    out: dict[str, dict] = {}
    for s in db.query(Source).filter(Source.user_id == PUBLISHED_USER_ID):
        reg = _find_registry(s.url or "")
        if not reg:
            continue
        cell = out.setdefault(reg["id"], {
            "id": reg["id"], "label": reg["name"],
            "host": _host(reg["base_url"]), "source_ids": []})
        cell["source_ids"].append(s.id)
    return list(out.values())


def _any_fair_conds() -> list:
    """Rows attributable to SOME registered fair (published set only).

    Attribution = crawl source is registry-matched, or the profile URL sits
    on a registered fair's own domain (covers rows whose source is missing).
    Google/research rows without fair attribution are never published."""
    conds: list = []
    for h, _label in _REG_HOSTS:
        conds.append(Company.profile_url.ilike(f"%{h}%"))
    return conds


def _published_base(db: Session) -> tuple[list, list[dict]]:
    """(sql conditions for the published fair-attributed set, fair sources)."""
    regs = _registry_sources(db)
    sids = [i for r in regs for i in r["source_ids"]]
    conds: list = [Company.user_id == PUBLISHED_USER_ID,
                   Company.lifecycle == "active"]
    any_fair = ([Company.source_id.in_(sids)] if sids else []) + _any_fair_conds()
    conds.append(or_(*any_fair))
    return conds, regs


def _fair_filter_cond(fair: str, regs: list[dict]):
    """SQL condition narrowing to one registry fair id (or None if unknown)."""
    reg = next((r for r in regs if r["id"] == fair), None)
    if reg is None:
        return None
    parts = ([Company.source_id.in_(reg["source_ids"])]
             if reg["source_ids"] else [])
    parts.append(Company.profile_url.ilike(f"%{reg['host']}%"))
    return or_(*parts)


def _fair_label(c: Company, sid_label: dict[int, str]) -> str | None:
    if c.source_id in sid_label:
        return sid_label[c.source_id]
    for u in (c.profile_url, c.website):
        h = _host_any(u)
        if not h:
            continue
        for bh, label in _REG_HOSTS:
            if h == bh or h.endswith("." + bh):
                return label
    return "Trade-fair directory"


def _products_of(c: Company, cap: int = 4) -> list[dict]:
    import json as _json
    try:
        rows = _json.loads(c.products_json or "[]")
    except Exception:
        return []
    out = []
    for r in rows[:cap]:
        if isinstance(r, dict) and (r.get("name") or "").strip():
            out.append({"name": str(r["name"])[:200]})
    return out


def _bump_daily(request: Request) -> int:
    """Consume one free guest search; returns remaining. 429 at the cap."""
    ip = request.client.host if request.client else "unknown"
    today = date.today().isoformat()
    d, n = _day_hits.get(ip, (today, 0))
    if d != today:
        n = 0
    if n >= FREE_SEARCHES_PER_DAY:
        raise HTTPException(status_code=429, detail=(
            "Free daily search limit reached. Directory search is free for "
            "a limited time — create a free account to keep searching and "
            "reveal contacts."))
    _day_hits[ip] = (today, n + 1)
    return FREE_SEARCHES_PER_DAY - (n + 1)


# ---------------------------------------------------------------- endpoints

@router.get("/meta")
def meta(db: Session = Depends(get_db),
         _rl: None = Depends(rate_limit("dirmeta", META_RATE_PER_MIN))):
    """Public totals + filter options for the landing page."""
    conds, regs = _published_base(db)
    total = db.query(func.count(Company.id)).filter(*conds).scalar() or 0
    with_email = (db.query(func.count(Company.id)).filter(
        *conds,
        Company.id.in_(db.query(Contact.company_id).filter(
            Contact.kind == "EMAIL"))).scalar() or 0)
    with_phone = (db.query(func.count(Company.id)).filter(
        *conds,
        Company.id.in_(db.query(Contact.company_id).filter(
            Contact.kind.in_(["PHONE", "WHATSAPP"])))).scalar() or 0)

    by_fair = []
    for r in regs:
        fcond = _fair_filter_cond(r["id"], regs)
        if fcond is None:
            continue
        # count via the same attribution condition the search uses
        n = db.query(func.count(Company.id)).filter(
            Company.user_id == PUBLISHED_USER_ID,
            Company.lifecycle == "active",
            fcond).scalar() or 0
        by_fair.append({"id": r["id"], "label": r["label"], "count": n})

    countries = [c for (c,) in (db.query(Company.country).filter(
        *conds, Company.country.isnot(None), Company.country != "")
        .distinct().order_by(Company.country).limit(150).all())]
    updated = db.query(func.max(Company.updated_at)).filter(*conds).scalar()
    return {
        "record_type": RECORD_TYPE,
        "disclaimer": DISCLAIMER,
        "locked_note": LOCKED_NOTE,
        "total": total,
        "with_email": with_email,
        "with_phone": with_phone,
        "by_fair": sorted(by_fair, key=lambda r: -r["count"]),
        "fairs": [{"id": r["id"], "label": r["label"]} for r in regs],
        "countries": countries,
        "free_per_day": FREE_SEARCHES_PER_DAY,
        "updated_at": updated.isoformat() if updated else None,
    }


@router.get("/search")
def search(
    request: Request,
    q: str | None = None,
    fair: str | None = None,
    country: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("dirsearch", SEARCH_RATE_PER_MIN)),
):
    """Guest search over the published directory. Contacts stay locked."""
    q = (q or "").strip()[:Q_MAX_LEN]
    fair = (fair or "").strip()
    country = (country or "").strip()[:100]
    if len(q) == 1:
        raise HTTPException(status_code=422,
                            detail="Keep the search to 2+ characters")
    if not q and not fair and not country:
        raise HTTPException(status_code=422, detail=(
            "Type a product or company name, or pick a fair / country "
            "filter"))

    conds, regs = _published_base(db)
    fcond = None
    if fair:
        fcond = _fair_filter_cond(fair, regs)
        if fcond is None:
            raise HTTPException(status_code=422, detail="Unknown fair filter")

    remaining = _bump_daily(request)   # all validation done; never burn on 422

    if fcond is not None:
        conds.append(fcond)
    if country:
        conds.append(func.lower(Company.country) == country.lower())
    if q:
        like = f"%{_like_esc(q)}%"
        conds.append(or_(
            Company.name.ilike(like, escape="\\"),
            Company.product_summary.ilike(like, escape="\\"),
            Company.category.ilike(like, escape="\\"),
            Company.products_json.ilike(like, escape="\\")))

    page = max(1, min(page, 200))
    qry = (db.query(Company).filter(*conds)
           .order_by(Company.name.asc())
           .offset((page - 1) * PAGE_SIZE_MAX).limit(PAGE_SIZE_MAX))
    total = db.query(func.count(Company.id)).filter(*conds).scalar() or 0
    rows = qry.all()

    sid_label = {r["source_ids"][i]: r["label"]
                 for r in regs for i in range(len(r["source_ids"]))}
    ids = [c.id for c in rows]
    email_ids: set[int] = set()
    phone_ids: set[int] = set()
    if ids:
        email_ids = {i for (i,) in db.query(Contact.company_id).filter(
            Contact.company_id.in_(ids), Contact.kind == "EMAIL").all()}
        phone_ids = {i for (i,) in db.query(Contact.company_id).filter(
            Contact.company_id.in_(ids),
            Contact.kind.in_(["PHONE", "WHATSAPP"])).all()}

    return {
        "record_type": RECORD_TYPE,
        "source": {
            "name": "Trade-fair exhibitor directories (crawled)",
            "record_type": RECORD_TYPE,
            "coverage": "published exhibitor profiles attributable to a "
                        "registered trade fair",
        },
        "disclaimer": DISCLAIMER,
        "locked_note": LOCKED_NOTE,
        "free": {"per_day": FREE_SEARCHES_PER_DAY, "remaining": remaining},
        "total": total, "page": page, "page_size": PAGE_SIZE_MAX,
        "items": [{
            "id": c.id, "name": c.name,
            "city": c.city, "country": c.country,
            "category": c.category,
            "product_summary": (c.product_summary or "")[:220],
            "products": _products_of(c),
            "fair": _fair_label(c, sid_label),
            "hall": c.hall, "booth": c.booth,
            "source_url": c.profile_url,
            "record_type": RECORD_TYPE,
            "has_email": c.id in email_ids,
            "has_phone": c.id in phone_ids,
            # locked for guests — the conversion gate (never guessed):
            "emails": [], "phones": [], "website": None,
        } for c in rows],
    }


@router.get("/company/{company_id}")
def reveal_company(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _rl: None = Depends(rate_limit("dirreveal", REVEAL_RATE_PER_MIN)),
):
    """Signed-in reveal: website + contacts with per-row evidence.

    Published rows (operator account) are readable by any signed-in user —
    that IS the public directory; other tenants' rows are never exposed.
    """
    c = (db.query(Company)
         .filter(Company.id == company_id,
                 Company.user_id.in_([user.id, PUBLISHED_USER_ID]),
                 Company.lifecycle == "active")
         .first())
    if not c:
        raise HTTPException(status_code=404, detail="Company not found")
    contacts = (db.query(Contact)
                .filter(Contact.company_id == c.id,
                        Contact.kind.in_(["EMAIL", "PHONE", "WHATSAPP"]))
                .order_by(Contact.is_primary.desc(), Contact.kind)
                .all())
    sid_label: dict[int, str] = {}
    for r in _registry_sources(db):
        for i, sid in enumerate(r["source_ids"]):
            sid_label[sid] = r["label"]
    audit(db, user.id, "directory_reveal", "company", c.id,
          f"{(c.name or '')[:80]}")
    db.commit()
    return {
        "record_type": RECORD_TYPE,
        "disclaimer": DISCLAIMER,
        "evidence_note": "Every contact below was literally published on "
                         "the linked source page — the source URL and "
                         "verification status are kept per row.",
        "company": {
            "id": c.id, "name": c.name,
            "city": c.city, "country": c.country,
            "address": c.address, "category": c.category,
            "product_summary": (c.product_summary or "")[:500],
            "products": _products_of(c, cap=20),
            "fair": _fair_label(c, sid_label),
            "hall": c.hall, "booth": c.booth,
            "website": c.website, "source_url": c.profile_url,
            "record_type": RECORD_TYPE,
        },
        "contacts": [{
            "kind": ct.kind, "value": ct.value,
            "source_url": ct.source_url, "source_page": ct.source_page,
            "verification": ct.verification,
            "is_primary": bool(ct.is_primary),
            "last_verified": (ct.last_verified.isoformat()
                              if ct.last_verified else None),
        } for ct in contacts],
    }
