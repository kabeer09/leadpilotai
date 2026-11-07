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
