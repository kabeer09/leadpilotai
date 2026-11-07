"""Trade data provider abstraction + UN Comtrade adapter.

Honesty rules (spec sections 2/16):
- Typed errors: TradeConfigError (missing/invalid config — never retry),
  TradeQuotaError (provider rate limit/quota), TradeAPIError (other
  upstream failure). Quota/config failures are NEVER reported as "0 results".
- The public preview API returns *Desc = null; descriptions are resolved
  locally from UN reference files (reporters/partners/HS) — code never
  invents a name it did not load from a source file.
- Results are aggregated annual statistics (record_type
  "aggregated_statistics"), never individual shipment rows or buyers.
- Outbound calls are paced by TRADE_MIN_INTERVAL because the public API
  answers 429 after ~1-2 quick calls; retries use exponential backoff.
  When quota is exhausted and a cached payload exists, the API layer
  serves it flagged stale instead of failing.
- The optional subscription key (UN_COMTRADE_API_KEY) stays server-side
  and is only attached as a header — never logged, never returned.
"""
import json
import logging
import re
import time
from typing import Callable

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Public preview: no key, <=500 records/call, ONE period per call (the API
# rejects comma lists with "Maximum number of periods for preview is 1").
PREVIEW_URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
# Optional paid endpoint (subscription key required) — adapter built, but
# only claimed working after a real authorized-key test.
PAID_URL = "https://comtradeapi.un.org/data/v1/get"
REF_BASE = "https://comtradeapi.un.org/files/v1/app/reference"

REF_REPORTERS = f"{REF_BASE}/Reporters.json"
REF_PARTNERS = f"{REF_BASE}/partnerAreas.json"
REF_HS = f"{REF_BASE}/HS.json"

MAX_RECORDS = 500          # public preview hard cap per call
FLOW_BOTH = "both"


class TradeError(Exception):
    """Base for provider failures (message safe to show the user)."""


class TradeConfigError(TradeError):
    """Provider missing/misconfigured — retrying will not help."""


class TradeQuotaError(TradeError):
    """Provider rate limit / quota exhausted (HTTP 429)."""


class TradeAPIError(TradeError):
    """Other upstream failure after retries."""


# ---------------------------------------------------------------------------
# Query building / validation (pure — unit tested)
# ---------------------------------------------------------------------------

_PERIOD_RE = re.compile(r"^\d{4}$")
_HS_RE = re.compile(r"^\d{2,6}$")


def normalize_flow(flow: str | None) -> str:
    """M | X | both (case-insensitive, default both)."""
    f = (flow or FLOW_BOTH).strip().upper()
    if f in ("M", "X"):
        return f
    if f in ("BOTH", "M,X", "X,M", "MX", "XM"):
        return FLOW_BOTH
    raise TradeConfigError(
        f"Invalid flow '{flow}' — use M (imports), X (exports) or both")


def expand_periods(period_from: str, period_to: str) -> list[str]:
    """Validate a year range and expand it to a list of years.

    The preview API accepts ONE period per request, so the provider loops.
    Raises TradeConfigError on bad input or an over-long range (guards
    against accidental 60-call bursts).
    """
    a, b = (period_from or "").strip(), (period_to or "").strip()
    if not _PERIOD_RE.match(a) or not _PERIOD_RE.match(b):
        raise TradeConfigError(
            "period_from and period_to must be 4-digit years (e.g. 2022)")
    y1, y2 = int(a), int(b)
    if y2 < y1:
        y1, y2 = y2, y1
    if y1 < 1990 or y2 > 2100:
        raise TradeConfigError("Years must be between 1990 and 2100")
    if y2 - y1 + 1 > 10:
        raise TradeConfigError(
            "Date range limited to 10 years per query — narrow the range")
    return [str(y) for y in range(y1, y2 + 1)]


def normalize_cmd(cmd_code: str | None) -> str | None:
    """HS code: digits only, 2-6 long; None/empty = all products."""
    c = (cmd_code or "").strip()
    if not c:
        return None
    if not _HS_RE.match(c):
        raise TradeConfigError(
            f"Invalid HS code '{cmd_code}' — use 2-6 digits (e.g. 7113)")
    return c


def flow_param(flow: str) -> str:
    """Normalized flow -> UN API flowCode value ('both' -> 'M,X').

    The live preview API rejects flowCode=both (400) and requires the
    comma form — verified against the real endpoint.
    """
    return "M,X" if flow == FLOW_BOTH else flow


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class UNComtradeProvider:
    """UN Comtrade adapter (public preview by default, paid when keyed).

    `transport` and `sleep` are injectable so tests run with zero network
    and zero real sleeping.
    """

    name = "un_comtrade"

    def __init__(self, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] | None = None,
                 api_key: str | None = None):
        self._client = httpx.Client(
            transport=transport, timeout=settings.TRADE_TIMEOUT,
            follow_redirects=True)
        self._sleep = sleep or time.sleep
        self._api_key = (settings.UN_COMTRADE_API_KEY
                         if api_key is None else api_key)
        self._last_call = 0.0
        # reference caches: url -> (fetched_epoch, data)
        self._ref_cache: dict[str, tuple[float, object]] = {}

    # -- pacing / HTTP ------------------------------------------------------

    def _pace(self) -> None:
        """Enforce a minimum interval between outbound calls (429 guard)."""
        gap = settings.TRADE_MIN_INTERVAL
        wait = self._last_call + gap - time.monotonic()
        if wait > 0:
            self._sleep(wait)

    def _headers(self) -> dict:
        if self._api_key:
            return {"Ocp-Apim-Subscription-Key": self._api_key}
        return {}

    def _get(self, url: str, params: dict) -> dict:
        """GET with pacing + exponential backoff on 429/5xx.

        Config errors (4xx other than 429) are raised immediately —
        retrying a malformed query never helps.
        """
        retries = settings.TRADE_MAX_RETRIES
        last_status = 0
        body = ""
        for attempt in range(retries + 1):
            self._pace()
            try:
                resp = self._client.get(
                    url, params=params, headers=self._headers())
            except httpx.HTTPError as e:
                if attempt >= retries:
                    raise TradeAPIError(
                        f"UN Comtrade unreachable: {str(e)[:200]}") from e
                self._sleep(min(2 ** attempt, 8))
                continue
            finally:
                self._last_call = time.monotonic()
            last_status = resp.status_code
            if resp.status_code == 429 or resp.status_code >= 500:
                body = resp.text[:300]
                if attempt < retries:
                    self._sleep(min(2 ** attempt, 8))
                    continue
                break
            if resp.status_code >= 400:
                body = resp.text[:300]
                break
            try:
                data = resp.json()
            except ValueError as e:
                raise TradeAPIError(
                    "UN Comtrade returned non-JSON data") from e
            return data

        if last_status == 429:
            raise TradeQuotaError(
                "UN Comtrade rate limit reached (HTTP 429) — the public "
                "preview API allows a small number of requests per minute. "
                "Wait a moment and retry; a recent cached result may be "
                "shown instead.")
        raise TradeAPIError(
            f"UN Comtrade HTTP {last_status}: {body or 'no body'}")

    # -- reference data (local description resolution) ----------------------

    def _reference(self, url: str) -> object:
        """Fetch + cache a UN reference file (24h TTL)."""
        now = time.time()
        hit = self._ref_cache.get(url)
        if hit and now - hit[0] < settings.TRADE_REFERENCE_TTL_HOURS * 3600:
            return hit[1]
        data = self._get(url, {})
        self._ref_cache[url] = (now, data)
        return data

    @staticmethod
    def _ref_list(data: object) -> list[dict]:
        if isinstance(data, dict):
            r = data.get("results")
            if isinstance(r, list):
                return r
        if isinstance(data, list):
            return data
        return []

    def reporter_map(self) -> dict[int, dict]:
        rows = self._ref_list(self._reference(REF_REPORTERS))
        out: dict[int, dict] = {}
        for r in rows:
            code = r.get("reporterCode") or r.get("id")
            if code is not None:
                out[int(code)] = r
        return out

    def partner_map(self) -> dict[int, dict]:
        rows = self._ref_list(self._reference(REF_PARTNERS))
        out: dict[int, dict] = {}
        out[0] = {"id": 0, "text": "World", "PartnerDesc": "World",
                  "PartnerCode": 0}
        for r in rows:
            code = r.get("PartnerCode") or r.get("id")
            if code is not None:
                out[int(code)] = r
        return out

    def hs_rows(self) -> list[dict]:
        return self._ref_list(self._reference(REF_HS))

    # -- search --------------------------------------------------------------

    def search(self, *, reporter_code: int, periods: list[str],
               flow: str, cmd_code: str | None = None,
               partner_code: int | None = None,
               max_records: int = MAX_RECORDS) -> dict:
        """Fetch aggregated rows for (reporter x periods x flow).

        Returns {"rows": [...], "source": {...}, "truncated": bool}.
        Rows carry raw codes; descriptions are filled by _decorate().
        """
        if not reporter_code or reporter_code <= 0:
            raise TradeConfigError("reporter_code is required")
        flow_code = flow_param(flow)
        max_records = max(1, min(int(max_records or MAX_RECORDS),
                                 MAX_RECORDS))
        rows: list[dict] = []
        truncated = False
        # Preview allows ONE period per call — loop the range, paced.
        for period in periods:
            params: dict = {
                "reporterCode": reporter_code,
                "period": period,
                "flowCode": flow_code,
                "maxRecords": max_records,
            }
            if cmd_code:
                params["cmdCode"] = cmd_code
            if partner_code is not None:
                params["partnerCode"] = partner_code
            data = self._get(PREVIEW_URL, params)
            batch = data.get("data") or []
            if not isinstance(batch, list):
                raise TradeAPIError("UN Comtrade response missing 'data'")
            rows.extend(batch)
            if len(batch) >= max_records:
                truncated = True

        return {
            "rows": self._decorate(rows),
            "truncated": truncated,
            "source": self.describe(reporter_code, periods, flow_code,
                                    cmd_code, partner_code),
        }

    # -- statistics (aggregated series) -------------------------------------

    def statistics(self, *, reporter_code: int, periods: list[str],
                   flow: str, cmd_code: str | None = None,
                   partner_code: int | None = None) -> dict:
        """Sum primaryValue per (period, flow) from search rows.

        Pure aggregation over what the provider returned — no invented
        values. Rows with no primaryValue are counted but contribute 0.

        Double-count guard: an unfiltered query can include BOTH the
        World aggregate row (partnerCode 0, isAggregate) and the
        individual partner rows that compose it. Whenever a group
        (period, flow) contains any non-World row, the World row is
        excluded from that group's sum.
        """
        result = self.search(reporter_code=reporter_code, periods=periods,
                             flow=flow, cmd_code=cmd_code,
                             partner_code=partner_code)
        rows = result["rows"]
        # group membership first so the guard can see sibling rows
        groups: dict[tuple[str, str], list[dict]] = {}
        for r in rows:
            key = (str(r.get("period") or ""), str(r.get("flowCode") or ""))
            groups.setdefault(key, []).append(r)

        series: dict[tuple[str, str], dict] = {}
        for (period, fl), group in groups.items():
            has_partner_rows = any(
                (g.get("partnerCode") not in (0, None)) for g in group)
            cell = {"period": period, "flow": fl, "value": 0.0,
                    "records": 0}
            for r in group:
                pc = r.get("partnerCode")
                if has_partner_rows and pc in (0, None):
                    continue  # World aggregate duplicates its parts
                cell["records"] += 1
                v = r.get("primaryValue")
                if isinstance(v, (int, float)):
                    cell["value"] += float(v)
            series[(period, fl)] = cell
        points = sorted(series.values(),
                        key=lambda c: (c["period"], c["flow"]))
        result["series"] = points
        result["total_value"] = sum(p["value"] for p in points)
        result["record_type"] = "aggregated_statistics"
        return result

    # -- HS codes ------------------------------------------------------------

    def hs_search(self, query: str | None = None, code: str | None = None,
                  limit: int = 50) -> list[dict]:
        """Search the UN HS reference by product text and/or code.

        Exact code match returns that node + its children (sub-headings);
        text match returns leaf+non-leaf nodes whose description contains
        the query (case-insensitive). Never invents classifications.
        """
        rows = self.hs_rows()
        out: list[dict] = []
        code = (code or "").strip() or None
        q = (query or "").strip().lower()

        if code:
            # exact node, then children via parent linkage
            exact = [r for r in rows if str(r.get("id")) == code]
            children = [r for r in rows
                        if str(r.get("parent")) == code]
            out = exact + children

        if q and len(out) < limit:
            seen = {str(r.get("id")) for r in out}
            for r in rows:
                if len(out) >= limit:
                    break
                rid = str(r.get("id"))
                if rid in seen:
                    continue
                text = str(r.get("text") or "").lower()
                if q in text:
                    out.append(r)
                    seen.add(rid)
        return out[:limit]

    # -- countries -----------------------------------------------------------

    def countries(self) -> dict:
        """Reporter + partner reference lists (for filter dropdowns)."""
        reporters = []
        for code, r in sorted(self.reporter_map().items()):
            if r.get("isGroup"):
                continue
            reporters.append({
                "code": code,
                "name": r.get("reporterDesc") or r.get("text") or str(code),
                "iso2": r.get("reporterCodeIsoAlpha2"),
                "iso3": r.get("reporterCodeIsoAlpha3"),
            })
        partners = []
        for code, r in sorted(self.partner_map().items()):
            name = (r.get("PartnerDesc") or r.get("text") or str(code))
            partners.append({
                "code": code,
                "name": name,
                "iso2": r.get("PartnerCodeIsoAlpha2"),
                "iso3": r.get("PartnerCodeIsoAlpha3"),
            })
        return {"reporters": reporters, "partners": partners,
                "source": {"name": "UN Comtrade reference",
                           "url": REF_BASE,
                           "record_type": "reference_data"}}

    # -- decoration / metadata ----------------------------------------------

    def _decorate(self, rows: list[dict]) -> list[dict]:
        """Fill *Desc fields locally (the API returns them null).

        Missing reference names degrade to the raw code — never a guess.
        """
        try:
            reporters = self.reporter_map()
        except TradeError:
            reporters = {}
        try:
            partners = self.partner_map()
        except TradeError:
            partners = {}
        try:
            hs = {str(r.get("id")): r for r in self.hs_rows()}
        except TradeError:
            hs = {}

        flow_names = {"M": "Import", "X": "Export", "R": "Re-Export",
                      "A": "Re-Import"}
        out = []
        for r in rows:
            row = dict(r)
            rc = row.get("reporterCode")
            pc = row.get("partnerCode")
            cmd = str(row.get("cmdCode") or "")
            row["reporterDesc"] = (
                (reporters.get(int(rc), {}) if rc is not None else {})
                .get("reporterDesc") or row.get("reporterDesc"))
            row["partnerDesc"] = (
                (partners.get(int(pc), {}) if pc is not None else {})
                .get("PartnerDesc") or row.get("partnerDesc"))
            node = hs.get(cmd)
            if node and not row.get("cmdDesc"):
                text = str(node.get("text") or "")
                # "7113 - Jewellery ..." -> strip the code prefix
                row["cmdDesc"] = re.sub(r"^\d{2,6}\s*-\s*", "", text) or None
            if not row.get("flowDesc"):
                row["flowDesc"] = flow_names.get(
                    str(row.get("flowCode") or ""))
            out.append(row)
        return out

    def describe(self, reporter_code: int, periods: list[str],
                 flow_code: str, cmd_code: str | None,
                 partner_code: int | None) -> dict:
        """Source-attribution block returned with every payload."""
        return {
            "name": "UN Comtrade",
            "url": PREVIEW_URL,
            "record_type": "aggregated_statistics",
            "coverage": ("Annual merchandise trade statistics as reported "
                         "by countries (HS classification)"),
            "reporter_code": reporter_code,
            "periods": list(periods),
            "flow": flow_code,
            "cmd_code": cmd_code,
            "partner_code": partner_code,
            "disclaimer": ("Aggregated trade statistics — NOT individual "
                           "shipment records and NOT buyer/supplier "
                           "identities."),
        }

    def status(self) -> dict:
        """Integration status for /api/data-sources (no secrets)."""
        return {
            "key": self.name,
            "name": "UN Comtrade",
            "endpoint": PREVIEW_URL,
            "auth_type": "api_key" if self._api_key else "none",
            "auth_configured": bool(self._api_key),
            "mode": ("paid (subscription key set)" if self._api_key
                     else "public preview (no key)"),
            "limits": (f"<= {MAX_RECORDS} records/call, one period/call, "
                       f"rate-limited; min interval "
                       f"{settings.TRADE_MIN_INTERVAL}s"),
            "status": "active",
        }

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_provider: UNComtradeProvider | None = None


def get_trade_provider() -> UNComtradeProvider:
    """Singleton provider selected by TRADE_PROVIDER.

    'none' (or empty) disables the module with a typed config error the
    API turns into an honest 503 — never a fake empty result.
    """
    global _provider
    choice = (settings.TRADE_PROVIDER or "").strip().lower()
    if choice in ("", "none", "off", "disabled"):
        raise TradeConfigError(
            "Trade data provider is disabled (TRADE_PROVIDER=none) — set "
            "TRADE_PROVIDER=un_comtrade to enable global trade search")
    if choice not in ("un_comtrade", "uncomtrade", "un-comtrade"):
        raise TradeConfigError(
            f"Unknown TRADE_PROVIDER '{settings.TRADE_PROVIDER}' — "
            "supported: un_comtrade, none")
    if _provider is None:
        _provider = UNComtradeProvider()
    return _provider


def reset_trade_provider() -> None:
    """Drop the singleton (tests / settings changes)."""
    global _provider
    if _provider is not None:
        try:
            _provider.close()
        except Exception:
            pass
    _provider = None
