"""Selector-based profile extraction (old-pipeline parity, BeautifulSoup port).

The old engine queried the live DOM (h1.company-name, .description,
hall/booth selectors, product cards). Same selectors, same priority,
applied to fetched/rendered HTML — no browser API needed.
"""
import json
import re

INDUSTRY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Technology", ["software", "cloud", "saas", "tech", "digital"]),
    ("Manufacturing", ["manufacturer", "manufacturing", "industrial", "factory"]),
    ("Food & Beverage", ["food", "beverage", "restaurant", "catering", "organic"]),
    ("Textiles", ["textile", "fabric", "apparel", "clothing", "garment"]),
    ("Furniture", ["furniture", "sofa", "mattress", "home decor", "decor"]),
    ("Home & Giftware", ["gift", "gifts", "giftware", "homeware", "handicraft"]),
    ("Cosmetics", ["cosmetic", "beauty", "skincare", "fragrance"]),
    ("Toys", ["toy", "toys", "games", "play"]),
    ("Lighting", ["lighting", "lamp", "lamps", "chandelier"]),
    ("Jewelry", ["jewelry", "jewellery", "watch", "gem"]),
]


def infer_industry(text: str | None) -> str | None:
    if not text:
        return None
    low = text.lower()
    for industry, keywords in INDUSTRY_KEYWORDS:
        if any(kw in low for kw in keywords):
            return industry
    return None


def _first_text(soup, selectors: list[str], min_len: int = 0,
                max_len: int = 2000) -> str | None:
    for sel in selectors:
        try:
            el = soup.select_one(sel)
        except Exception:
            continue
        if el:
            text = el.get_text(" ", strip=True)
            if min_len <= len(text) <= max_len:
                return text
    return None


def extract_profile(soup, url: str) -> dict:
    """Structured exhibitor facts from a parsed profile page."""
    name = _first_text(soup, ["h1.company-name", "h1.exhibitor-name",
                              ".company-name", ".exhibitor-name",
                              "[itemprop='name']", "h1"],
                       min_len=2, max_len=200)
    description = _first_text(soup, [".description", ".about",
                                     ".company-description",
                                     ".exhibitor-description",
                                     "[itemprop='description']", ".overview",
                                     ".about-us", "p"],
                              min_len=20, max_len=2000)
    location = _first_text(soup, [".location", ".address", ".country",
                                  ".city", "[itemprop='address']",
                                  ".company-location"],
                           min_len=2, max_len=200)
    country = city = None
    if location:
        parts = [p.strip() for p in location.split(",")]
        if len(parts) >= 2:
            country, city = parts[-1], parts[0]
        else:
            country = location
    hall = _first_text(soup, [".hall", "[class*='hall']"],
                       min_len=1, max_len=100)
    booth = _first_text(soup, [".booth", ".stand", "[class*='booth']",
                                "[class*='stand']"],
                        min_len=1, max_len=100)
    products: list[dict] = []
    for sel in [".product", ".product-item", ".exhibitor-product",
                ".offering", "[class*='product-card']"]:
        try:
            els = soup.select(sel)
        except Exception:
            continue
        for el in els[:20]:
            text = el.get_text(" ", strip=True)
            if text and len(text) > 3:
                products.append({"name": text[:200], "category": ""})
        if products:
            break
    return {
        "profile_name": name,
        "profile_description": description,
        "industry": infer_industry(description),
        "country": country,
        "city": city,
        "address": location,
        "hall": hall,
        "booth": booth,
        "products": products,
        "products_json": json.dumps(products[:20]) if products else None,
    }
