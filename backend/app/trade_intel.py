"""Phase 4 trade-intelligence helpers — pure functions (no DB, no network).

Honesty contract (master prompt):
- Competitor rankings are AGGREGATED country-level statistics from UN
  Comtrade (who ships what to a market). Trade statistics do NOT expose
  company identities — every payload built on these helpers must say so.
- The World aggregate row (partnerCode 0) duplicates its parts whenever
  individual partner rows are present — it is dropped from rankings in
  that case (same double-count guard as provider.statistics). An explicit
  World-only query keeps it.
- HS hierarchy nodes come from the official UN reference file only;
  missing nodes are reported as missing, never invented.
"""
from __future__ import annotations

import re

_HS_PREFIX_RE = re.compile(r"^\d{2,6}\s*-\s*")


def strip_hs_desc(text: str | None) -> str:
    """UN reference text '7113 - Articles of jewellery ...' -> description."""
    return _HS_PREFIX_RE.sub("", str(text or "")).strip()


def rank_suppliers(rows: list[dict], top_n: int = 15) -> dict:
    """Rank partner countries by primaryValue over import rows.

    Returns:
      suppliers      top-N [{rank, partner_code, partner_desc, value,
                     share_pct, records}] sorted by value desc
      total_value    sum over ALL kept partners (not just top-N)
      partners_count number of distinct partners kept
      periods        periods actually present in the rows (transparency)
      flows          flows present in the rows
      truncated      True when partners were cut by top_n
      dropped_world  True when the World aggregate row was excluded
                     because its parts were present (double-count guard)

    Rows with no primaryValue contribute 0 but still count as records —
    values are never invented.
    """
    # group membership first so the World guard can see sibling rows
    has_parts = any(r.get("partnerCode") not in (0, None) for r in rows)
    periods = sorted({str(r.get("period") or "") for r in rows if r.get("period")})
    flows = sorted({str(r.get("flowCode") or "") for r in rows if r.get("flowCode")})

    agg: dict[object, dict] = {}
    for r in rows:
        pc = r.get("partnerCode")
        if has_parts and pc in (0, None):
            continue  # World aggregate duplicates its parts
        cell = agg.setdefault(pc, {"partner_code": pc,
                                   "partner_desc": r.get("partnerDesc"),
                                   "value": 0.0, "records": 0})
        cell["records"] += 1
        v = r.get("primaryValue")
        if isinstance(v, (int, float)):
            cell["value"] += float(v)
        if not cell["partner_desc"] and r.get("partnerDesc"):
            cell["partner_desc"] = r.get("partnerDesc")

    ranked = sorted(agg.values(),
                    key=lambda c: (-c["value"],
                                   str(c["partner_desc"] or ""),
                                   str(c["partner_code"])))
    total = sum(c["value"] for c in ranked)
    out = []
    for i, c in enumerate(ranked[: max(1, top_n)], start=1):
        share = round(100.0 * c["value"] / total, 2) if total > 0 else 0.0
        out.append({"rank": i, "partner_code": c["partner_code"],
                    "partner_desc": c["partner_desc"],
                    "value": round(c["value"], 2),
                    "share_pct": share, "records": c["records"]})
    return {
        "suppliers": out,
        "total_value": round(total, 2),
        "partners_count": len(ranked),
        "periods": periods,
        "flows": flows,
        "truncated": len(ranked) > len(out),
        "dropped_world": has_parts and any(
            r.get("partnerCode") in (0, None) for r in rows),
    }


def hs_hierarchy(nodes: list[dict], code: str) -> dict | None:
    """Build the classification hierarchy for an HS node.

    `nodes` is a slice of the official UN HS reference (rows carry
    id/parent/text/aggrLevel/isLeaf). Returns None when the code is not
    present — the caller turns that into an honest 404.

    Returns {code, description, aggr_level, is_leaf, chapter, ancestors,
             children} where ancestors run chapter-first (2-digit) and
    children are the direct sub-headings.
    """
    code = str(code)
    by_id = {str(r.get("id")): r for r in nodes}
    node = by_id.get(code)
    if node is None:
        return None

    ancestors: list[dict] = []
    cur = node
    for _ in range(8):  # HS depth is at most ~3 levels above a leaf
        parent = str(cur.get("parent") or "")
        if not parent or parent == "#":
            break
        pnode = by_id.get(parent)
        if pnode is None:
            break  # partial reference data — stop, never invent a parent
        if str(pnode.get("id")).upper() == "TOTAL":
            break  # UN reference's aggregate root — not part of the
                   # classification chain (and its parent is "#")
        ancestors.append({
            "code": str(pnode.get("id")),
            "description": strip_hs_desc(pnode.get("text")),
            "aggr_level": pnode.get("aggrLevel"),
        })
        cur = pnode
    ancestors.reverse()  # chapter-first

    children = [{
        "code": str(r.get("id")),
        "description": strip_hs_desc(r.get("text")),
        "aggr_level": r.get("aggrLevel"),
        "is_leaf": str(r.get("isLeaf") or "0") == "1",
    } for r in nodes if str(r.get("parent")) == code
        and str(r.get("id")) != code]

    chapter = next((a for a in ancestors if len(a["code"]) == 2), None)
    if chapter is None and len(code) == 2:
        chapter = {"code": code,
                   "description": strip_hs_desc(node.get("text")),
                   "aggr_level": node.get("aggrLevel")}

    return {
        "code": code,
        "description": strip_hs_desc(node.get("text")),
        "aggr_level": node.get("aggrLevel"),
        "is_leaf": str(node.get("isLeaf") or "0") == "1",
        "chapter": chapter,
        "ancestors": ancestors,
        "children": children,
    }


def latest_cell(series: list[dict]) -> tuple[str, str] | None:
    """(period, flow) of the most recent point in a statistics series."""
    points = [(str(s.get("period") or ""), str(s.get("flow") or ""))
              for s in series if s.get("period")]
    if not points:
        return None
    return max(points, key=lambda pf: (pf[0], pf[1]))
