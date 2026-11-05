"""Product relevance engine + evidence-based business classification.

Deterministic and explainable (no LLM): every point and every label comes
with a reason string that can be shown to the user and stored as evidence.

Relevance (0-100):
 80-100 Highly Relevant | 60-79 Relevant | 40-59 Potential Match | 0-39 Low
Compares the user's product keywords against: business name, business
category (Google Place Details or OSM tags — including the curated
keyword->OSM-category map), and website text (title/description/products
pages).

Business type (evidence only — never assumed):
  Manufacturer | Importer | Wholesaler | Retailer | Distributor | Exporter
  | Service Provider | Unknown
Unknown is the honest answer when the site shows no evidence.
"""
import math
import re

# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------

_STOP = {"the", "and", "for", "with", "from", "that", "this", "are", "our",
         "you", "your", "all", "any", "not", "but", "has", "have", "its",
         "into", "over", "under", "near", "via", "per", "inc", "ltd", "llc",
         "co", "company", "business"}

# Agent-noun normalization so 'wholesalers' and 'wholesale' compare equal.
_AGENT = {"wholesaler": "wholesale", "importer": "import",
          "exporter": "export", "distributor": "distribut",
          "retailer": "retail", "manufacturer": "manufactur",
          "supplier": "supply", "dealer": "deal"}


def _norm_ws(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _stem(tok: str) -> str:
    """Very light plural/gerund stemming (bottles->bottle, making->make)."""
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us")):
        tok = tok[:-1]
    if len(tok) > 5 and tok.endswith("ing"):
        tok = tok[:-3]
    return _AGENT.get(tok, tok)


def _tokens(s: str | None) -> set[str]:
    raw = re.findall(r"[a-z0-9]+", (s or "").lower())
    return {t for t in (_stem(x) for x in raw) if t and t not in _STOP}


def _phrase_present(phrase: str, text_norm: str) -> bool:
    p = _norm_ws(phrase)
    return bool(p) and p in text_norm


def _majority(n: int) -> int:
    """ceil(n/2) — half-or-more of the keyword terms (repo bans `//`)."""
    return math.ceil(n / 2)


# ---------------------------------------------------------------------------
# relevance scoring
# ---------------------------------------------------------------------------

LABELS = ((80, "Highly Relevant"), (60, "Relevant"), (40, "Potential Match"),
          (0, "Low Relevance"))


def label_for(score: int) -> str:
    for threshold, label in LABELS:
        if score >= threshold:
            return label
    return "Low Relevance"


def score_relevance(keywords: list[str],
                    name: str | None,
                    category: str | None,
                    evidence_texts: list[tuple[str, str]],
                    tag_hits: list[tuple[str, str]] | None = None) -> tuple[int, str, list[str]]:
    """Score 0-100 with reasons.

    evidence_texts: [(text, source)] — source is a URL or 'Google listing'.
    tag_hits: [(keyword, osm_category)] — curated OSM category-tag matches
    (see osm_places.category_tag_hits); each adds the same weight as a
    literal category-text match, with a reason naming the exact tag.
    Returns (score, label, reasons).
    """
    name_n = _norm_ws(name)
    cat_n = _norm_ws(category)
    name_toks = _tokens(name)
    cat_toks = _tokens(category)
    # keyword -> curated OSM category value (normalized keyword form)
    tag_map: dict[str, str] = {}
    for k, v in (tag_hits or []):
        if k and v:
            tag_map.setdefault(_norm_ws(k), str(v))
    # per-segment normalized text for source-citing reasons
    segs = [(_norm_ws(t), src) for t, src in evidence_texts if t]
    site_n = " ".join(t for t, _ in segs)
    site_toks: set[str] = set()
    for t, _ in segs:
        site_toks |= _tokens(t)

    reasons: list[str] = []
    hits: list[tuple[str, int, list[str]]] = []

    for kw in (keywords or []):
        kw = (kw or "").strip()
        if not kw:
            continue
        kw_n = _norm_ws(kw)
        kw_toks = _tokens(kw)
        if not kw_toks:
            continue
        strength = 0
        why: list[str] = []
        sources_matched = 0

        # business name (strongest signal)
        if _phrase_present(kw_n, name_n):
            strength += 45
            sources_matched += 1
            why.append(f"Keyword '{kw}' appears in the business name")
        elif kw_toks <= name_toks:
            strength += 40
            sources_matched += 1
            why.append(f"All keyword terms of '{kw}' appear in the business name")
        elif name_toks and len(kw_toks & name_toks) >= max(1, _majority(len(kw_toks))):
            strength += 15
            sources_matched += 1
            why.append(f"{len(kw_toks & name_toks)}/{len(kw_toks)} terms of "
                       f"'{kw}' appear in the business name")

        # business category (from Google Place Details or OSM tags)
        cat_text_hit = False
        if _phrase_present(kw_n, cat_n) or kw_toks <= cat_toks:
            cat_text_hit = True
            strength += 20
            sources_matched += 1
            why.append(f"Keyword '{kw}' matches the business category "
                       f"'{category or ''}'".rstrip())
        elif cat_toks and len(kw_toks & cat_toks) >= max(1, _majority(len(kw_toks))):
            cat_text_hit = True
            strength += 8
            sources_matched += 1
            why.append(f"Part of '{kw}' matches the business category "
                       f"'{category or ''}'".rstrip())
        elif kw_n in tag_map:
            # curated OSM category tag (e.g. cookware -> shop=houseware):
            # same fact as a category-text match, so same weight — never
            # double-counted with the branch above.
            strength += 20
            sources_matched += 1
            why.append(f"Keyword '{kw}' maps to the OpenStreetMap "
                       f"category '{tag_map[kw_n]}'")

        # website pages (title/description/products text)
        site_full = _phrase_present(kw_n, site_n) or kw_toks <= site_toks
        site_part = (not site_full and site_toks
                     and len(kw_toks & site_toks) >= max(1, _majority(len(kw_toks))))
        if site_full or site_part:
            found_src = None
            for t, src in segs:
                if _phrase_present(kw_n, t) or kw_toks <= _tokens(t) or (
                        site_part and len(kw_toks & _tokens(t)) >= max(1, _majority(len(kw_toks)))):
                    found_src = src
                    break
            strength += 15 if site_full else 8
            sources_matched += 1
            why.append(f"Keyword '{kw}' found on the website"
                       + (f" ({found_src})" if found_src else ""))

        # corroboration: the same keyword evidenced in several places
        if sources_matched >= 3:
            strength += 15
            why.append("Keyword corroborated across name, category and website")
        elif sources_matched == 2:
            strength += 10
            why.append("Keyword corroborated in two sources (name/category/website)")

        if strength:
            hits.append((kw, strength, why))

    hits.sort(key=lambda h: -h[1])
    score = 0
    for i, (_kw, strength, why) in enumerate(hits):
        # the best-matching keyword counts fully; extra keywords add context
        score += strength if i == 0 else min(strength, 15)
        for r in why:
            if r not in reasons:
                reasons.append(r)
    score = max(0, min(100, score))
    if not reasons:
        reasons.append("No keyword evidence found in name, category or website")
    return score, label_for(score), reasons


# ---------------------------------------------------------------------------
# business classification (evidence-based; Unknown when no evidence)
# ---------------------------------------------------------------------------

# Ordered: ties go to the earlier type (a factory that also exports is a
# Manufacturer first).
_TYPE_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
    ("Manufacturer", ("manufacturer", "manufacturing", "manufacture",
                      "factory", "oem", "produced by", "own production",
                      "we make", "made in our")),
    ("Exporter", ("exporter", "exporting", "export to", "exports to",
                  "worldwide shipping", "international shipping")),
    ("Importer", ("importer", "importing", "we import")),
    ("Wholesaler", ("wholesaler", "wholesale", "bulk price", "bulk order",
                    "trade price", "reseller")),
    ("Distributor", ("distributor", "distribution", "dealer", "stockist",
                     "authorized dealer")),
    ("Retailer", ("retailer", "retail", "showroom", "online store",
                  "shop online")),
    ("Service Provider", ("service provider", "our services",
                          "services include", "consulting")),
]


def classify_business_type(
        evidence_texts: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """Pick the type with the most distinct on-page signals.

    evidence_texts: [(text, source)]. Returns (type, evidence strings).
    No signals anywhere => ('Unknown', []).
    """
    normed = [(_norm_ws(t), src) for t, src in evidence_texts if t]
    best_type = "Unknown"
    best_signals: list[str] = []
    for btype, signals in _TYPE_SIGNALS:
        found: list[str] = []
        for sig in signals:
            for text, src in normed:
                if sig in text:
                    found.append(f"'{sig}' found on {src}")
                    break
        # earlier type wins ties (loop order), so strictly-greater keeps it
        if len(found) > len(best_signals):
            best_type, best_signals = btype, found
    return best_type, best_signals[:6]


# ---------------------------------------------------------------------------
# dedup (spec section 6)
# ---------------------------------------------------------------------------

def normalize_domain(url: str | None) -> str | None:
    """'https://www.Example.com/shop/' -> 'example.com' (None if no URL)."""
    if not url:
        return None
    u = url.strip().lower()
    u = re.sub(r"^[a-z][a-z0-9+.-]*://", "", u)
    u = u.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    u = u.split("@")[-1]
    if ":" in u:
        u = u.split(":", 1)[0]
    if u.startswith("www."):
        u = u[4:]
    return u or None


def dedup_key_name_city(name: str | None, city: str | None,
                        country: str | None) -> str | None:
    """Fallback identity when place_id/domain are missing.

    Includes location on purpose — two 'ABC Traders' in different cities
    are different businesses (spec: never merge on generic name alone).
    """
    n = _norm_ws(name)
    if not n:
        return None
    return "|".join([n, _norm_ws(city) or "", _norm_ws(country) or ""])
