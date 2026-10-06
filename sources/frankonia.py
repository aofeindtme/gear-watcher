"""Frankonia Online-Shop (frankonia.de) - Neuware.

Suche: /search.html?query=... (robots.txt erlaubt das). Artikel-Nr., Titel,
Preis und Marke stehen maschinenlesbar im Tracking-Attribut jeder Kachel
(data-ga-parameter), ebenso auf Produktseiten (data-ga-eec des Warenkorb-Buttons).
"""
import html as htmllib
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import Listing, SourceError, get, parse_price, to_float

BASE = "https://www.frankonia.de"


def search(watch: dict, session) -> list[Listing]:
    # Frankonia antwortet auf "keine Treffer" mit HTTP 404 samt normaler Seite
    r = get(session, f"{BASE}/search.html", ok_status=(200, 404), params={"query": watch["query"]})
    if r.status_code == 404:
        if "keine produkttreffer" in r.text.lower():
            return []
        raise SourceError("Frankonia: HTTP 404")
    return parse_tiles(r.text)


def parse_tiles(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    tiles = soup.select("[data-product-tracking-position]")
    if not tiles and "fr-article-tile" not in html and "keine produkttreffer" not in html.lower():
        raise SourceError("Frankonia: Ergebnisliste nicht gefunden (Seitenstruktur geändert?)")

    results = []
    for tile in tiles:
        # "click|EEC-productClick|<Titel>|<ArtNr>|<Preis 1099.0>|<Marke>|..."
        ga = htmllib.unescape(tile.get("data-ga-parameter") or "").split("|")
        link = tile.select_one("a[href^='/p/']")
        if not link or len(ga) < 5:
            continue
        price_el = tile.select_one(".fr-price-new__current-price")
        price_text = price_el.get_text(" ", strip=True) if price_el else ""
        img = tile.select_one("img")
        img_src = str(img.get("src") or "") if img else ""
        if img_src.startswith("//"):
            img_src = "https:" + img_src
        brand = ga[5] if len(ga) > 5 else ""
        title = ga[2]
        results.append(Listing(
            site="frankonia",
            ext_id=ga[3] or str(tile.get("data-external-key")),
            title=f"{brand} {title}".strip() if brand and brand.lower() not in title.lower() else title,
            url=urljoin(BASE, str(link["href"]).split("?")[0]),
            price=to_float(ga[4]) if to_float(ga[4]) is not None else parse_price(price_text),
            price_text=price_text,
            image=img_src,
        ))
    return results


def parse_product_page(html: str, url: str) -> Listing | None:
    soup = BeautifulSoup(html, "html.parser")
    btn = soup.select_one("[data-ga-eec^='add|']")
    if not btn:
        return None
    ga = htmllib.unescape(btn.get("data-ga-eec") or "").split("|")
    if len(ga) < 5:
        return None
    og_img = soup.select_one('meta[property="og:image"]')
    text = soup.get_text(" ", strip=True).lower()
    available = None
    if "nicht lieferbar" in text or "ausverkauft" in text:
        available = False
    elif "in den warenkorb" in text:
        available = True
    return Listing(
        site="url",
        ext_id=url,
        title=ga[2],
        url=url,
        price=to_float(ga[4]),
        price_text="",
        image=str(og_img.get("content") or "") if og_img else "",
        location="frankonia.de",
        available=available,
    )
