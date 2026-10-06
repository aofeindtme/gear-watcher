"""Frankonia-Kleinanzeigen (frankonia-kleinanzeigen.de) - Privat-/Händlerangebote,
Festpreis und Auktion. robots.txt enthält keine Einschränkungen.

Suche: /suche?q=...&sort=startX (neu eingestellt zuerst), Preis über
priceFrom/priceTo, Umkreis über geo_plz + geo_dist + geo_country=DE (ohne
geo_country ignoriert die Seite den Umkreis).
"""
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import Listing, SourceError, get, parse_price, to_float

BASE = "https://www.frankonia-kleinanzeigen.de"
RADIUS_STEPS = [10, 25, 50, 100, 150, 200, 500]


def build_params(watch: dict) -> dict:
    params = {"q": watch["query"], "sort": "startX"}
    if watch.get("min_price") is not None:
        params["priceFrom"] = str(int(watch["min_price"]))
    if watch.get("max_price") is not None:
        params["priceTo"] = str(int(watch["max_price"]))
    if watch.get("zip_code") and watch.get("radius_km"):
        params["geo_plz"] = watch["zip_code"]
        params["geo_dist"] = next((r for r in RADIUS_STEPS if r >= watch["radius_km"]), RADIUS_STEPS[-1])
        params["geo_country"] = "DE"
    return params


def search(watch: dict, session) -> list[Listing]:
    r = get(session, f"{BASE}/suche", params=build_params(watch))
    return parse(r.text)


def _text(el, sel: str) -> str:
    found = el.select_one(sel)
    return found.get_text(" ", strip=True) if found else ""


def parse(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("li.ap-offer-list__row")
    if not rows and "ap-offer-list" not in html and "0 treffer" not in html.lower() \
            and "keine treffer" not in html.lower():
        raise SourceError("Frankonia-Kleinanzeigen: Ergebnisliste nicht gefunden (Seitenstruktur geändert?)")

    results = []
    for row in rows:
        link = row.select_one("a[href^='/angebot/']")
        if not link:
            continue
        # data-ga4-eec = "select_item|<id>|<voller Titel>|<preis 29,00>|<PRIVATE|BUSINESS>|<n>|<fixprice|auction>|..."
        ga = (row.get("data-ga4-eec") or "").split("|")
        ext_id = ga[1] if len(ga) > 1 and ga[1] else str(link["href"]).rsplit("/", 1)[-1]
        title = ga[2] if len(ga) > 2 and ga[2] else _text(row, ".ap-slider-tile-countdown__name")
        price_text = _text(row, ".ap-slider-tile-countdown__price")
        price = parse_price(price_text)
        if price is None and len(ga) > 3:
            price = to_float(ga[3].replace(".", "").replace(",", "."))
        offer_type = _text(row, ".ap-slider-tile-countdown__type")
        is_auction = (len(ga) > 6 and ga[6] == "auction") or "auktion" in offer_type.lower()
        label = _text(row, ".ap-slider-tile-countdown__text-right").rstrip(":")
        img = row.select_one("img")
        img_src = (img.get("data-src") or img.get("src") or "") if img else ""
        if img_src.startswith("//"):
            img_src = "https:" + img_src
        seller = _text(row, ".ap-slider-tile-countdown__status")

        results.append(Listing(
            site="frankonia_kleinanzeigen",
            ext_id=ext_id,
            title=title,
            url=urljoin(BASE, str(link["href"])),
            price=price,
            price_text=f"{price_text} ({label})" if label and price_text else price_text,
            image="" if "platzhalter" in img_src else str(img_src),
            location=_text(row, ".ap-slider-tile-countdown__place"),
            posted=_text(row, ".date"),
            condition=" · ".join(x for x in [_text(row, ".ap-slider-tile-countdown__condition"), seller] if x),
            is_auction=is_auction,
        ))
    return results
