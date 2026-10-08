"""Beobachtung beliebiger Shop-URLs (Produkt- oder Kategorieseiten).

Für Shops, deren Suche per robots.txt gesperrt ist (Recon, Alljagd), oder für
ein ganz bestimmtes Produkt, dessen Preis/Verfügbarkeit man verfolgen will.

- Produktseite: Preis + Verfügbarkeit aus schema.org-Microdata/JSON-LD
  (fast alle Shopsysteme liefern das), Frankonia über sein Tracking-Attribut.
- Kategorie-/Listenseite: Shopware-5/6-, Frankonia- oder schema.org-Kacheln
  (JTL u.a.) -> jedes neue
  Produkt dort ist ein neuer Treffer.

Vor jedem Abruf wird die robots.txt des Shops geprüft.
"""
import json
import re
import time
import urllib.robotparser
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from . import USER_AGENT, Listing, SourceError, get, http_session, parse_price, to_float
from . import frankonia, shops, shopware

_robots_cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
ROBOTS_TTL = 24 * 3600

IN_STOCK = ("instock", "limitedavailability", "preorder", "presale", "onlineonly", "instoreonly")
OUT_OF_STOCK = ("outofstock", "soldout", "discontinued", "backorder")


def split_urls(text: str | None) -> list[str]:
    return [u.strip() for u in (text or "").splitlines() if u.strip().startswith(("http://", "https://"))]


def host_label(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def robots_allowed(url: str) -> bool:
    parts = urlparse(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    cached = _robots_cache.get(origin)
    if not cached or time.time() - cached[0] > ROBOTS_TTL:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = http_session().get(f"{origin}/robots.txt", timeout=15)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:
            rp = None  # robots.txt nicht erreichbar -> nicht blockieren
        cached = (time.time(), rp)
        _robots_cache[origin] = cached
    rp = cached[1]
    return rp is None or rp.can_fetch(USER_AGENT, url)


def _availability(value: str | None) -> bool | None:
    v = (value or "").lower().rsplit("/", 1)[-1]
    if any(s in v for s in OUT_OF_STOCK):
        return False
    if any(s in v for s in IN_STOCK):
        return True
    return None


def _pick_variant(group: dict, url: str) -> dict:
    """ProductGroup (MediaMarkt/Saturn): die Variante dieser Seite, Name/Bild notfalls von der Gruppe."""
    variants = [v for v in group.get("hasVariant") or [] if isinstance(v, dict)]
    path = urlparse(url).path
    variant = next((v for v in variants if isinstance(v.get("offers"), dict)
                    and urlparse(str(v["offers"].get("url") or "")).path == path), None) \
        or next((v for v in variants if v.get("sku") and path.endswith(f"-{v['sku']}.html")), None)
    if not variant:
        return group if group.get("offers") else (variants[0] if len(variants) == 1 else group)
    return {"name": group.get("name"), "image": group.get("image"), **variant}


def _jsonld_product(soup, url: str = "") -> dict | None:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            # MediaMarkt/Saturn verpacken das Produkt in eine BuyAction
            if isinstance(item, dict) and isinstance(item.get("object"), dict):
                item = item["object"]
            if isinstance(item, dict) and "ProductGroup" in str(item.get("@type")):
                return _pick_variant(item, url)
            if isinstance(item, dict) and "Product" in str(item.get("@type")):
                return item
    return None


def parse_product_page(html: str, url: str) -> Listing | None:
    """Generische Produktseite über schema.org (Microdata oder JSON-LD)."""
    soup = BeautifulSoup(html, "html.parser")
    title = price = availability = image = None

    price_el = soup.select_one('[itemprop="price"]')
    if price_el:
        price = to_float(price_el.get("content")) or parse_price(price_el.get_text(" ", strip=True))
        avail_el = soup.select_one('[itemprop="availability"]')
        if avail_el:
            availability = _availability(avail_el.get("href") or avail_el.get("content"))

    ld = _jsonld_product(soup, url)
    if ld:
        title = ld.get("name")
        offers = ld.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if price is None:
            price = to_float(offers.get("price") or offers.get("lowPrice"))
        if availability is None:
            availability = _availability(offers.get("availability"))
        img = ld.get("image")
        image = img[0] if isinstance(img, list) and img else img if isinstance(img, str) else None

    if price is None:
        og_price = soup.select_one('meta[property="product:price:amount"]')
        price = to_float(og_price.get("content")) if og_price else None
    if price is None:
        return None

    h1 = soup.select_one("h1")
    og_title = soup.select_one('meta[property="og:title"]')
    title = (h1.get_text(" ", strip=True) if h1 else None) or title or \
        (og_title.get("content") if og_title else None) or url
    og_img = soup.select_one('meta[property="og:image"]')
    image = image or (og_img.get("content") if og_img else "")

    return Listing(
        site="url",
        ext_id=url,
        title=str(title),
        url=url,
        price=price,
        image=str(image or ""),
        location=host_label(url),
        available=availability,
    )


def _title_matches(title: str, query: str | None) -> bool:
    words = [w.lower() for w in (query or "").split() if w]
    t = title.lower()
    return all(w in t for w in words)


def fetch_url(url: str, query: str | None, session) -> list[Listing]:
    if not robots_allowed(url):
        raise SourceError(f"{host_label(url)}: robots.txt verbietet den Abruf von {url}")
    html = get(session, url).text
    host = host_label(url)

    if host.endswith("frankonia.de"):
        item = frankonia.parse_product_page(html, url)
        if item:
            return [item]
        items = frankonia.parse_tiles(html)
    else:
        # og:type=product ist ein eindeutiges Produktseiten-Signal; Produktseiten haben oft
        # zusätzlich "Ähnliche Produkte"-Kacheln, deshalb dann Produkt zuerst.
        og_product = re.search(r'property="og:type"\s+content="product"', html) is not None
        if og_product:
            item = parse_product_page(html, url)
            if item:
                return [item]
        items = shopware.parse_sw6_boxes(html, "url", url) or shopware.parse_sw5_boxes(html, "url", url) \
            or shops.parse_microdata_list(html, "url", url)
        # Ab zwei Kacheln ist es eine Liste - auch wenn einzelne Preise als Microdata markiert sind (JTL)
        if len(items) < 2 and not og_product:
            item = parse_product_page(html, url)
            if item:
                return [item]
        if not items:
            raise SourceError(f"{host}: Seite nicht erkannt (weder Produktseite mit Preis noch bekannte Produktliste)")

    for i in items:
        i.site = "url"
        i.ext_id = i.url
        i.location = host
    # Auf Listenseiten wirkt der Suchbegriff immer als Titelfilter (alle Wörter müssen
    # vorkommen) - unabhängig vom Häkchen, sonst käme jedes Produkt der Kategorie
    return [i for i in items if _title_matches(i.title, query)]


def search(watch: dict, session) -> list[Listing]:
    urls = split_urls(watch.get("urls"))
    results, errors = [], []
    for n, url in enumerate(urls):
        if n:
            time.sleep(2)
        try:
            results.extend(fetch_url(url, watch.get("query"), session))
        except SourceError as e:
            errors.append(str(e))
    if errors and not results:
        raise SourceError("; ".join(errors))
    # Teilfehler: Treffer der übrigen URLs trotzdem verarbeiten, Fehler protokolliert der Watcher
    watch.setdefault("_warnings", []).extend(errors)
    return results
