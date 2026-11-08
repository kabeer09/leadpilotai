"""Global Trade Intelligence API — search, statistics, HS codes, countries,
saved searches, CSV/Excel export, data-source registry.

Contracts (spec sections 2/16/18):
- Every payload carries `record_type: "aggregated_statistics"` + a `source`
  block (provider name, URL, coverage, periods, disclaimer). Aggregated
  statistics are never dressed up as individual shipment records.
- Typed provider errors map honestly: TradeConfigError -> 503,
  TradeQuotaError -> 429 (after trying a cached payload, flagged stale),
  TradeAPIError -> 502. Never a silent "0 results".
- Results cache in trade_statistics (TTL) so repeat searches are instant
  and a quota error can still serve the last known payload, marked stale.
- Tenant-scoped saved searches; writes rate-limited + audited; export
  streams CSV/XLSX with source-attribution columns.
"""
import csv
import hashlib
import io
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user, rate_limit, audit
from app.models import User
from app.models_trade import SavedSearch, TradeStatistic, DataProvider
from app import trade_intel
from app.trade_providers import (
    get_trade_provider, TradeError, TradeConfigError, TradeQuotaError,
    TradeAPIError, expand_periods, normalize_flow, normalize_cmd,
    MAX_RECORDS,
)

router = APIRouter(prefix="/api", tags=["trade"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _err_http(e: TradeError) -> HTTPException:
    """Typed provider error -> honest HTTP status (never a fake empty)."""
    if isinstance(e, TradeConfigError):
        return HTTPException(status_code=503, detail=str(e))
    if isinstance(e, TradeQuotaError):
        return HTTPException(status_code=429, detail=str(e))
    if isinstance(e, TradeAPIError):
        return HTTPException(status_code=502, detail=str(e))
    return HTTPException(status_code=502, detail=str(e))


def _jloads(s: str | None, default):
    try:
        v = json.loads(s) if s else default
        return v if isinstance(v, type(default)) else default
    except (ValueError, TypeError):
        return default


def _cache_key(kind: str, params: dict) -> str:
    canon = json.dumps({"kind": kind, **params}, sort_keys=True,
                       ensure_ascii=False)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()


def _cache_get(db: Session, key: str) -> TradeStatistic | None:
    return (db.query(TradeStatistic)
            .filter(TradeStatistic.cache_key == key).first())


def _cache_put(db: Session, key: str, params: dict, payload: dict,
               row_count: int, status: int | None = None) -> None:
    now = datetime.utcnow()
    row = _cache_get(db, key)
    if row is None:
        row = TradeStatistic(cache_key=key, provider="un_comtrade")
        db.add(row)
    row.params_json = json.dumps(params, ensure_ascii=False)
    row.payload_json = json.dumps(payload, ensure_ascii=False)
    row.row_count = row_count
    row.last_status = status
    row.fetched_at = now
    db.commit()


def _fresh(row: TradeStatistic | None) -> bool:
    if row is None or not row.payload_json:
        return False
    age = datetime.utcnow() - (row.fetched_at or datetime.utcnow())
    return age < timedelta(hours=settings.TRADE_CACHE_TTL_HOURS)


def _stale_wrap(row: TradeStatistic, reason: str) -> dict:
    """Serve a cached payload flagged stale when the provider fails."""
    payload = _jloads(row.payload_json, {})
    payload["stale"] = True
    payload["stale_reason"] = reason
    payload["cached_at"] = (row.fetched_at.isoformat() + "Z"
                            if row.fetched_at else None)
    return payload


def _run_search(db: Session, params: dict) -> dict:
    """Fetch (or reuse cache) a decorated search payload.

    On quota/API failure: serve fresh-enough cache if present, else cache
    of any age (flagged stale), else raise the typed error.
    """
    key = _cache_key("search", params)
    cached = _cache_get(db, key)
    if _fresh(cached):
        payload = _jloads(cached.payload_json, {})
        payload["cached"] = True
        return payload

    provider = get_trade_provider()
    try:
        raw = provider.search(
            reporter_code=params["reporter_code"],
            periods=params["periods"],
            flow=params["flow"],
            cmd_code=params.get("cmd_code"),
            partner_code=params.get("partner_code"),
            max_records=params.get("max_records") or MAX_RECORDS)
    except TradeError as e:
        _record_source(db, ok=False, error=str(e))
        if cached is not None and cached.payload_json:
            payload = _stale_wrap(cached, str(e))
            payload["cached"] = True
            return payload
        raise _err_http(e)

    payload = {
        "record_type": "aggregated_statistics",
        "source": raw.get("source") or {},
        "count": len(raw.get("rows") or []),
        "truncated": bool(raw.get("truncated")),
        "stale": False,
        "cached": False,
        "rows": raw.get("rows") or [],
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }
    _cache_put(db, key, params, payload, payload["count"], status=200)
    return payload


def _record_source(db: Session, ok: bool, error: str | None = None) -> None:
    """Track last success/error on the UN Comtrade registry row."""
    row = db.query(DataProvider).filter(DataProvider.key == "un_comtrade").first()
    if row is None:
        return
    if ok:
        row.last_success_at = datetime.utcnow()
        row.status = "active"
        row.last_error = None
    else:
        row.last_error = (error or "")[:2000]
        row.last_error_at = datetime.utcnow()
        row.status = "error"
    db.commit()


def _build_params(reporter_code: int, period_from: str, period_to: str,
                  flow: str, cmd_code: str | None,
                  partner_code: int | None, max_records: int) -> dict:
    """Validate inputs -> canonical provider params (typed config errors)."""
    try:
        periods = expand_periods(period_from, period_to)
        norm_flow = normalize_flow(flow)
        norm_cmd = normalize_cmd(cmd_code)
    except TradeConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if reporter_code <= 0:
        raise HTTPException(status_code=422,
                            detail="reporter_code is required")
    if partner_code is not None and partner_code < 0:
        raise HTTPException(status_code=422,
                            detail="partner_code must be >= 0 (0 = World)")
    return {
        "reporter_code": reporter_code,
        "period_from": min(periods[0], periods[-1]),
        "period_to": max(periods[0], periods[-1]),
        "periods": periods,
        "flow": norm_flow,
        "cmd_code": norm_cmd,
        "partner_code": partner_code,
        "max_records": min(max(1, max_records), MAX_RECORDS),
    }


# ---------------------------------------------------------------------------
# search / statistics
# ---------------------------------------------------------------------------

@router.get("/trade/search")
def trade_search(
        reporter_code: int = Query(..., gt=0,
                                   description="UN Comtrade reporter code"),
        period_from: str = Query("2023"),
        period_to: str = Query("2023"),
        flow: str = Query("both", description="M | X | both"),
        cmd_code: str | None = Query(None, description="HS code, e.g. 7113"),
        partner_code: int | None = Query(
            None, ge=0, description="Partner code; 0 = World; omit = all"),
        max_records: int = Query(500, ge=1, le=500),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user)):
    params = _build_params(reporter_code, period_from, period_to, flow,
                           cmd_code, partner_code, max_records)
    payload = _run_search(db, params)
    _record_source(db, ok=True)
    payload["params"] = {k: v for k, v in params.items() if k != "periods"}
    return payload


def _run_statistics(db: Session, params: dict) -> dict:
    """Fetch (or reuse cache) a decorated statistics payload.

    Shared by /trade/statistics and /trade/compare (cache is per
    reporter+params, so comparison countries reuse warmed series).
    On quota/API failure: serve cache if present (flagged stale), else
    raise the typed error — never a fake empty series.
    """
    key = _cache_key("statistics", params)
    cached = _cache_get(db, key)
    if _fresh(cached):
        payload = _jloads(cached.payload_json, {})
        payload["cached"] = True
        return payload

    provider = get_trade_provider()
    try:
        raw = provider.statistics(
            reporter_code=params["reporter_code"],
            periods=params["periods"],
            flow=params["flow"],
            cmd_code=params.get("cmd_code"),
            partner_code=params.get("partner_code"))
    except TradeError as e:
        _record_source(db, ok=False, error=str(e))
        if cached is not None and cached.payload_json:
            payload = _stale_wrap(cached, str(e))
            payload["cached"] = True
            return payload
        raise _err_http(e)

    payload = {
        "record_type": "aggregated_statistics",
        "source": raw.get("source") or {},
        "series": raw.get("series") or [],
        "total_value": raw.get("total_value") or 0.0,
        "truncated": bool(raw.get("truncated")),
        "stale": False,
        "cached": False,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }
    _cache_put(db, key, params, payload, len(payload["series"]), status=200)
    _record_source(db, ok=True)
    return payload


@router.get("/trade/statistics")
def trade_statistics(
        reporter_code: int = Query(..., gt=0),
        period_from: str = Query("2019"),
        period_to: str = Query("2023"),
        flow: str = Query("both"),
        cmd_code: str | None = Query(None),
        partner_code: int | None = Query(None, ge=0),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user)):
    params = _build_params(reporter_code, period_from, period_to, flow,
                           cmd_code, partner_code, MAX_RECORDS)
    payload = _run_statistics(db, params)
    payload["params"] = {k: v for k, v in params.items() if k != "periods"}
    return payload


# ---------------------------------------------------------------------------
# reference data (HS codes, countries)
# ---------------------------------------------------------------------------

@router.get("/trade/hs-codes")
def trade_hs_codes(
        q: str | None = Query(None, max_length=200,
                              description="Product name text"),
        code: str | None = Query(None, max_length=10,
                                 description="Exact HS code"),
        limit: int = Query(50, ge=1, le=200),
        user: User = Depends(get_current_user)):
    if not q and not code:
        raise HTTPException(status_code=422,
                            detail="Provide q (product text) or code")
    provider = get_trade_provider()
    try:
        rows = provider.hs_search(query=q, code=code, limit=limit)
    except TradeError as e:
        raise _err_http(e)
    out = []
    for r in rows:
        rid = str(r.get("id") or "")
        text = str(r.get("text") or "")
        out.append({
            "code": rid,
            "description": text,
            "parent": r.get("parent"),
            "is_leaf": str(r.get("isLeaf") or "0") == "1",
            "aggr_level": r.get("aggrLevel"),
        })
    return {
        "record_type": "reference_data",
        "source": {"name": "UN Comtrade HS classification",
                   "url": "https://comtradeapi.un.org/files/v1/app/reference/HS.json",
                   "disclaimer": ("Official HS classifications — use as "
                                  "guidance; confirm uncertain codes with a "
                                  "customs broker.")},
        "count": len(out),
        "items": out,
    }


@router.get("/trade/countries")
def trade_countries(user: User = Depends(get_current_user)):
    provider = get_trade_provider()
    try:
        return provider.countries()
    except TradeError as e:
        raise _err_http(e)


# ---------------------------------------------------------------------------
# Phase 4: competitor analysis, HS intelligence, country comparison
# ---------------------------------------------------------------------------

def _reporter_name(provider, code: int) -> str:
    """Resolve a reporter code to its official name (never invented —
    degrades to the numeric code when the reference file is unavailable)."""
    try:
        row = provider.reporter_map().get(int(code)) or {}
    except Exception:
        row = {}
    return (row.get("reporterDesc") or row.get("text") or str(code))


def _parse_reporter_codes(raw: str) -> list[int]:
    """Comma-separated reporter codes -> unique positive ints, order kept."""
    codes: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or int(part) <= 0:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid reporter code '{part}' — use numeric UN "
                       "codes separated by commas (e.g. 826,276,380)")
        c = int(part)
        if c not in codes:
            codes.append(c)
    return codes


@router.get("/trade/competitors")
def trade_competitors(
        destination_code: int = Query(
            ..., gt=0,
            description="Import market (UN reporter code of the "
                        "destination country)"),
        cmd_code: str | None = Query(
            None, description="HS product code to compare supply of"),
        period: str = Query("2023", description="Single year (4 digits)"),
        top_n: int = Query(15, ge=1, le=100),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user)):
    """Who supplies a market: destination imports ranked by partner country.

    Aggregated country-level statistics only — UN Comtrade does not expose
    company identities, and this payload says so.
    """
    params = _build_params(destination_code, period, period, "M", cmd_code,
                           None, MAX_RECORDS)  # destination's imports, all partners
    payload = _run_search(db, params)

    ranked = trade_intel.rank_suppliers(payload.get("rows") or [],
                                        top_n=top_n)
    provider = get_trade_provider()  # config error -> honest 503
    return {
        "record_type": "aggregated_statistics",
        "source": {
            **(payload.get("source") or {}),
            "destination_code": destination_code,
            "destination_name": _reporter_name(provider, destination_code),
        },
        "stale": bool(payload.get("stale")),
        "stale_reason": payload.get("stale_reason"),
        "cached": bool(payload.get("cached")),
        "fetched_at": payload.get("fetched_at"),
        "params": {"destination_code": destination_code,
                   "cmd_code": params.get("cmd_code"),
                   "period": period, "flow": "M", "top_n": top_n},
        **ranked,
        "note": ("Country-level export sources for this market — trade "
                 "statistics do not identify individual companies. Use "
                 "Buyer Discovery for company-level leads."),
    }


@router.get("/trade/hs-intel")
def trade_hs_intel(
        cmd_code: str = Query(..., description="HS code, e.g. 7113"),
        reporter_code: int | None = Query(
            None, gt=0, description="Include this country's trade trend "
                                    "for the code"),
        period_from: str = Query("2019"),
        period_to: str = Query("2023"),
        flow: str = Query("both"),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user)):
    """HS intelligence: official classification hierarchy (+ optional
    reporter trade trend for the code).

    Classification comes from the official UN HS reference file — unknown
    codes are an honest 404, never an invented description.
    """
    try:
        code = normalize_cmd(cmd_code)  # raises TradeConfigError
    except TradeConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not code:
        raise HTTPException(status_code=422,
                            detail="cmd_code is required (e.g. 7113)")
    provider = get_trade_provider()  # config error -> honest 503
    try:
        # Hierarchy needs the WHOLE reference (parents live above the
        # exact node); fall back to the exact+children slice when a
        # provider has no full list — ancestors then degrade honestly.
        rows_fn = getattr(provider, "hs_rows", None)
        if callable(rows_fn):
            nodes = rows_fn()
        else:
            nodes = provider.hs_search(code=code)
    except TradeError as e:
        raise _err_http(e)

    hierarchy = trade_intel.hs_hierarchy(nodes, code)
    if hierarchy is None:
        raise HTTPException(
            status_code=404,
            detail=f"HS code '{code}' not found in the official UN "
                   "Comtrade HS classification")

    classification = {
        "record_type": "reference_data",
        "source": {"name": "UN Comtrade HS classification",
                   "url": "https://comtradeapi.un.org/files/v1/app/"
                          "reference/HS.json",
                   "disclaimer": ("Official HS classifications — use as "
                                  "guidance; confirm uncertain codes with a "
                                  "customs broker.")},
        **hierarchy,
    }

    trend = None
    if reporter_code is not None:
        params = _build_params(reporter_code, period_from, period_to, flow,
                               code, None, MAX_RECORDS)
        trend = _run_statistics(db, params)
        trend["params"] = {k: v for k, v in params.items()
                           if k != "periods"}

    return {
        "record_type": "hs_intelligence",
        "classification": classification,
        "trend": trend,
        "note": ("Hierarchy from the official UN reference; any trade "
                 "trend block is aggregated statistics for the selected "
                 "reporter — not shipment records."),
    }


@router.get("/trade/compare")
def trade_compare(
        reporter_codes: str = Query(
            ..., description="2-4 comma-separated UN reporter codes"),
        period_from: str = Query("2022"),
        period_to: str = Query("2023"),
        flow: str = Query("both"),
        cmd_code: str | None = Query(None),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user)):
    """Side-by-side country comparison (aggregated annual statistics).

    Each country's series is fetched through the shared statistics cache,
    so countries/years already queried elsewhere are answered instantly.
    """
    codes = _parse_reporter_codes(reporter_codes)
    if len(codes) < 2:
        raise HTTPException(status_code=422,
                            detail="Pick at least 2 countries to compare")
    if len(codes) > 4:
        raise HTTPException(status_code=422,
                            detail="Comparison is limited to 4 countries "
                                   "per query")

    # Bound the outbound burst: one preview call per (reporter x year).
    try:
        periods = expand_periods(period_from, period_to)
    except TradeConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if len(periods) > 5:
        raise HTTPException(
            status_code=422,
            detail="Comparison is limited to 5 years per query — narrow "
                   "the range")

    provider = get_trade_provider()  # config error -> honest 503

    countries = []
    any_stale = False
    all_cached = True
    for rc in codes:
        params = _build_params(rc, period_from, period_to, flow, cmd_code,
                               None, MAX_RECORDS)
        p = _run_statistics(db, params)
        any_stale = any_stale or bool(p.get("stale"))
        all_cached = all_cached and bool(p.get("cached"))
        countries.append({
            "reporter_code": rc,
            "name": _reporter_name(provider, rc),
            "series": p.get("series") or [],
            "total_value": p.get("total_value") or 0.0,
            "stale": bool(p.get("stale")),
            "cached": bool(p.get("cached")),
        })

    # per (period, flow) matrix with shares among the compared countries
    matrix: dict[tuple[str, str], dict] = {}
    for c in countries:
        for s in c["series"]:
            key = (str(s.get("period") or ""), str(s.get("flow") or ""))
            cell = matrix.setdefault(key, {"period": key[0], "flow": key[1],
                                           "values": {}})
            v = s.get("value")
            cell["values"][str(c["reporter_code"])] = (
                float(v) if isinstance(v, (int, float)) else 0.0)
    rows_out = []
    for (period, fl), cell in sorted(matrix.items()):
        total = sum(cell["values"].values())
        cell["combined"] = round(total, 2)
        cell["shares"] = {
            rc: (round(100.0 * v / total, 2) if total > 0 else 0.0)
            for rc, v in cell["values"].items()}
        cell["values"] = {rc: round(v, 2)
                          for rc, v in cell["values"].items()}
        rows_out.append(cell)

    return {
        "record_type": "aggregated_statistics",
        "source": {"name": "UN Comtrade",
                   "url": "https://comtradeapi.un.org/public/v1/preview/"
                          "C/A/HS",
                   "record_type": "aggregated_statistics",
                   "coverage": ("Annual merchandise trade statistics as "
                                "reported by countries (HS classification)"),
                   "periods": periods,
                   "flow": flow,
                   "cmd_code": cmd_code,
                   "disclaimer": ("Aggregated trade statistics — NOT "
                                  "individual shipment records and NOT "
                                  "buyer/supplier identities.")},
        "params": {"reporter_codes": codes, "period_from": period_from,
                   "period_to": period_to, "flow": flow,
                   "cmd_code": cmd_code},
        "countries": countries,
        "matrix": rows_out,
        "stale": any_stale,
        "cached": all_cached and bool(countries),
        "note": ("Shares are each country's piece of the COMPARED set "
                 "for that period/flow — not its share of world trade. "
                 "Aggregated statistics, not company data."),
    }


# ---------------------------------------------------------------------------
# saved searches (tenant-scoped)
# ---------------------------------------------------------------------------

class SavedSearchIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: str = Field(default="trade", pattern="^(trade|statistics)$")
    query: dict = Field(default_factory=dict)


def _saved_out(s: SavedSearch) -> dict:
    return {"id": s.id, "name": s.name, "kind": s.kind,
            "query": _jloads(s.query_json, {}),
            "created_at": s.created_at}


@router.get("/trade/saved-searches")
def list_saved_searches(db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    rows = (db.query(SavedSearch)
            .filter(SavedSearch.user_id == user.id)
            .order_by(SavedSearch.id.desc()).limit(100).all())
    return {"count": len(rows), "items": [_saved_out(s) for s in rows]}


@router.post("/trade/saved-searches", status_code=201,
             dependencies=[Depends(rate_limit("trade_saved",
                                              settings.RATE_SENSITIVE_PER_MIN))])
def create_saved_search(payload: SavedSearchIn,
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name is required")
    exists = (db.query(SavedSearch)
              .filter(SavedSearch.user_id == user.id,
                      SavedSearch.kind == payload.kind,
                      SavedSearch.name == name).first())
    if exists:
        raise HTTPException(status_code=409,
                            detail=f"A saved search named '{name}' already "
                                   "exists for this account")
    s = SavedSearch(user_id=user.id, name=name, kind=payload.kind,
                    query_json=json.dumps(payload.query,
                                          ensure_ascii=False))
    db.add(s)
    db.commit()
    db.refresh(s)
    audit(db, user.id, "trade_saved_search_create", "saved_search", s.id,
          f"{payload.kind}: {name}")
    db.commit()
    return _saved_out(s)


@router.delete("/trade/saved-searches/{search_id}", status_code=204)
def delete_saved_search(search_id: int,
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    s = (db.query(SavedSearch)
         .filter(SavedSearch.id == search_id,
                 SavedSearch.user_id == user.id).first())
    if s is None:
        raise HTTPException(status_code=404, detail="Saved search not found")
    db.delete(s)
    db.commit()
    audit(db, user.id, "trade_saved_search_delete", "saved_search",
          search_id, s.name[:200])
    db.commit()


# ---------------------------------------------------------------------------
# export (CSV / XLSX) — re-runs the query (cache-aware), streams a file
# ---------------------------------------------------------------------------

_EXPORT_COLS = [
    ("Period", "period"), ("Flow", "flowDesc"), ("Flow Code", "flowCode"),
    ("Reporter", "reporterDesc"), ("Reporter Code", "reporterCode"),
    ("Partner", "partnerDesc"), ("Partner Code", "partnerCode"),
    ("HS Code", "cmdCode"), ("Product", "cmdDesc"),
    ("Trade Value (USD)", "primaryValue"),
    ("FOB Value (USD)", "fobvalue"), ("CIF Value (USD)", "cifvalue"),
    ("Quantity", "qty"), ("Quantity Unit", "qtyUnitAbbr"),
    ("Net Weight (kg)", "netWgt"), ("Gross Weight (kg)", "grossWgt"),
    ("Classification", "classificationCode"),
]


@router.get("/trade/export")
def trade_export(
        format: str = Query("csv", pattern="^(csv|xlsx)$"),
        reporter_code: int = Query(..., gt=0),
        period_from: str = Query("2023"),
        period_to: str = Query("2023"),
        flow: str = Query("both"),
        cmd_code: str | None = Query(None),
        partner_code: int | None = Query(None, ge=0),
        max_records: int = Query(500, ge=1, le=500),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user)):
    params = _build_params(reporter_code, period_from, period_to, flow,
                           cmd_code, partner_code, max_records)
    payload = _run_search(db, params)
    rows = payload.get("rows") or []
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M")
    base = f"leadpilot-trade-{stamp}"
    src = payload.get("source") or {}

    # Attribution preamble (CSV comment lines / XLSX header rows)
    attr = [
        ("Source", src.get("name") or "UN Comtrade"),
        ("Endpoint", src.get("url") or ""),
        ("Record type", payload.get("record_type") or
         "aggregated_statistics"),
        ("Coverage", src.get("coverage") or ""),
        ("Periods", ", ".join(str(p) for p in (src.get("periods") or []))),
        ("Retrieved", payload.get("fetched_at") or ""),
        ("Stale", "yes (cached)" if payload.get("stale") else "no"),
        ("Disclaimer", src.get("disclaimer") or ""),
    ]

    if format == "csv":
        buf = io.StringIO()
        for k, v in attr:
            buf.write(f"# {k}: {v}\n")
        w = csv.writer(buf)
        w.writerow([h for h, _ in _EXPORT_COLS])
        for r in rows:
            w.writerow([r.get(a) for _, a in _EXPORT_COLS])
        audit(db, user.id, "trade_export", "trade", None,
              f"csv rows={len(rows)}")
        db.commit()
        return StreamingResponse(
            iter([buf.getvalue()]), media_type="text/csv",
            headers={"Content-Disposition":
                     f"attachment; filename={base}.csv"})

    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Trade data"
    for k, v in attr:
        ws.append([k, v])
    ws.append([])
    ws.append([h for h, _ in _EXPORT_COLS])
    header_row = ws.max_row
    for cell in ws[header_row]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append([r.get(a) for _, a in _EXPORT_COLS])
    for i, (_h, _a) in enumerate(_EXPORT_COLS, start=1):
        width = max(12, min(45, max(
            (len(str(ws.cell(row=r, column=i).value or ""))
             for r in range(header_row, min(ws.max_row, 200) + 1)),
            default=12) + 2))
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    audit(db, user.id, "trade_export", "trade", None,
          f"xlsx rows={len(rows)}")
    db.commit()
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return StreamingResponse(
        iter([out.getvalue()]), media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition":
                 f"attachment; filename={base}.xlsx"})


# ---------------------------------------------------------------------------
# data sources (spec section 16 — never exposes credentials)
# ---------------------------------------------------------------------------

@router.get("/data-sources")
def list_data_sources(db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Provider registry + live integration status (booleans only)."""
    provider = get_trade_provider()
    live = provider.status()  # may raise TradeConfigError -> 503

    rows = {r.key: r for r in db.query(DataProvider).all()}

    def _merge(key: str, base: dict) -> dict:
        row = rows.get(key)
        return {
            **base,
            "last_success_at": (row.last_success_at if row else None),
            "last_error": (row.last_error if row else None),
            "last_error_at": (row.last_error_at if row else None),
            "db_status": (row.status if row else None),
        }

    # registry rows for sources that exist but are not connected yet —
    # status "not_configured" (built adapter, no credentials claimed).
    planned = [
        {"key": "india_trade_stat", "name": "India TradeStat (NIRYAT)",
         "endpoint": "https://tradestat.commerce.gov.in",
         "auth_type": "api_key", "auth_configured": False,
         "mode": "not connected", "status": "not_configured",
         "limits": "pending authorized access",
         "coverage_note": "India export/import data — adapter planned; "
                          "requires permitted access"},
        {"key": "itc_trade_map", "name": "ITC Trade Map",
         "endpoint": "https://www.trademap.org",
         "auth_type": "api_key", "auth_configured": False,
         "mode": "not connected", "status": "not_configured",
         "limits": "licensed access required",
         "coverage_note": "ITC Trade Map — authorized institutional access "
                          "only; not connected"},
        {"key": "commercial_shipments", "name":
         "Commercial shipment-data providers",
         "endpoint": None,
         "auth_type": "api_key", "auth_configured": False,
         "mode": "not connected", "status": "not_configured",
         "limits": "paid license required",
         "coverage_note": "Bill-of-lading style providers — no license "
                          "configured; no shipment records are shown"},
    ]

    items = [_merge("un_comtrade", live)]
    items.extend(_merge(p["key"], p) for p in planned)

    # Only registry rows we actually track get persisted; the live one is
    # upserted so last_success/last_error have somewhere to land.
    if "un_comtrade" not in rows:
        db.add(DataProvider(
            key="un_comtrade", name="UN Comtrade",
            endpoint=live.get("endpoint"),
            auth_type=live.get("auth_type", "none"),
            auth_configured=bool(live.get("auth_configured")),
            data_categories=json.dumps(
                ["trade statistics", "HS codes", "country reference"]),
            country_coverage=json.dumps(["Global (UN member reporting)"]),
            available_fields=json.dumps(
                [h for _, h in _EXPORT_COLS]),
            usage_limits=live.get("limits"),
            status=live.get("status", "active")))
        db.commit()

    return {"count": len(items), "items": items,
            "note": ("Status reflects configuration and the last real "
                     "attempt — integrations are never claimed working "
                     "without a successful authorized call.")}
