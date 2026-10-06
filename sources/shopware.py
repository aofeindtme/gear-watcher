"""Shopware-Shops. Viele Jagd-/Outdoor-Händler laufen auf Shopware, daher
gemeinsame Parser für die Produktkacheln beider Generationen:

- Shopware 6: <div class="product-box"> (Pirscher Gear, Recon, Alljagd)
- Shopware 5: <div class="product--box"> (Sportwaffen Triebel)

Suche nur bei Shops, deren robots.txt /search erlaubt. Recon und Alljagd
sperren /search und alle URLs mit "?" -> dort nur Kategorie-/Produktseiten über
die URL-Beobachtung (sources/url.py).
"""
import json
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import Listing, SourceError, get, parse_price

UNAVAILABLE_HINTS = ("ausverkauft", "nicht verfügbar", "nicht lieferbar", "sold out", "out of stock",
                     "nicht mehr verfügbar", "derzeit nicht")


def _availability(text: str) -> bool | None:
    t = text.lower()
    if not t:
        return None
    return not any(h in t for h in UNAVAILABLE_HINTS)


def parse_sw6_boxes(html: str, site: str, base: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for box in soup.select(".product-box"):
        name = box.select_one("a.product-name")
        if not name:
            continue
        try:
            info = json.loads(box.get("data-product-information") or "{}")
        except ValueError:
            info = {}
        url = urljoin(base, str(name.get("href") or ""))
        price_el = box.select_one(".product-price")
        # Streichpreis/Rabatt stecken mit im Element - nur der erste Text ist der aktuelle Preis
        price_text = next(price_el.stripped_strings, "") if price_el else ""
        img = box.select_one("img.product-image")
        delivery = box.select_one(".delivery-information, .product-delivery-information")
        buy_btn = box.select_one(".btn-buy")
        available = _availability(delivery.get_text(" ", strip=True)) if delivery else None
        if available is None and buy_btn is not None:
            available = not buy_btn.has_attr("disabled")
        results.append(Listing(
            site=site,
            ext_id=str(info.get("id") or url),
            title=name.get_text(" ", strip=True),
            url=url,
            price=parse_price(price_text),
            price_text=price_text.split("*")[0].strip(),
            image=str(img.get("src") or "") if img else "",
            condition=" · ".join(b.get_text(" ", strip=True) for b in box.select(".product-badges .badge")),
            available=available,
        ))
    return results


def parse_sw5_boxes(html: str, site: str, base: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for box in soup.select(".product--box"):
        title = box.select_one("a.product--title")
        if not title:
            continue
        url = urljoin(base, str(title.get("href") or ""))
        price_el = box.select_one(".product--price .price--default") or box.select_one(".product--price")
        price_text = price_el.get_text(" ", strip=True) if price_el else ""
        img = box.select_one(".product--image img")
        img_src = ""
        if img:
            img_src = str(img.get("src") or (img.get("srcset") or "").split(",")[0].split(" ")[0])
        badges = [b.get_text(" ", strip=True) for b in box.select(".product--badge")]
        delivery = box.select_one(".product--delivery, .delivery--information")
        results.append(Listing(
            site=site,
            ext_id=str(box.get("data-ordernumber") or url),
            title=title.get_text(" ", strip=True),
            url=url,
            price=parse_price(price_text),
            price_text=price_text.replace("*", "").strip(),
            image=img_src,
            condition=" · ".join(badges),
            available=_availability(delivery.get_text(" ", strip=True)) if delivery else None,
        ))
    return results


def _check(results: list, html: str, label: str, marker: str) -> list:
    lower = html.lower()
    if not results and marker not in html and not any(m in lower for m in ("keine artikel", "keine produkte",
                                                                            "no result", "no products")):
        raise SourceError(f"{label}: Ergebnisliste nicht gefunden (Seitenstruktur geändert?)")
    return results


# ----------------------------------------------------------------------
# Konkrete Shops mit erlaubter Suche
# ----------------------------------------------------------------------

PIRSCHER_BASE = "https://www.pirschergear.com"
TRIEBEL_BASE = "https://sportwaffen-triebel.de"


def search_pirschergear(watch: dict, session) -> list[Listing]:
    r = get(session, f"{PIRSCHER_BASE}/search", params={"search": watch["query"]})
    return _check(parse_sw6_boxes(r.text, "pirschergear", PIRSCHER_BASE), r.text, "Pirscher Gear", "product-box")


def search_triebel(watch: dict, session) -> list[Listing]:
    r = get(session, f"{TRIEBEL_BASE}/search", params={"sSearch": watch["query"]})
    return _check(parse_sw5_boxes(r.text, "triebel", TRIEBEL_BASE), r.text, "Sportwaffen Triebel", "product--box")
