"""eGun (egun.de) - Auktionen und Sofortkauf für Waffen, Optik, Zubehör.

robots.txt erlaubt /search (gesperrt sind nur Konto-, Gebots- und
Kauf-Seiten). Suche sortiert nach Einstelldatum absteigend, damit neue
Angebote auf Seite 1 landen.
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import Listing, SourceError, get, parse_price

BASE = "https://www.egun.de"

# eGun-Werte für den Parameter "cond"
CONDITION_MAP = {"new": "new", "used": "used"}
RADIUS_STEPS = [10, 25, 50, 100, 250]


def build_params(watch: dict) -> dict:
    params = {
        "query": watch["query"],
        "order": "starts",
        "asdes": "desc",
        "plusdescr": "1" if watch.get("search_description") else "",
    }
    if watch.get("min_price") is not None:
        params["minprice"] = _fmt(watch["min_price"])
    if watch.get("max_price") is not None:
        params["maxprice"] = _fmt(watch["max_price"])
    if watch.get("zip_code") and watch.get("radius_km"):
        params["zip"] = watch["zip_code"]
        # eGun kennt nur feste Stufen - nächstgrößere nehmen
        params["radius"] = next((r for r in RADIUS_STEPS if r >= watch["radius_km"]), RADIUS_STEPS[-1])
    cond = CONDITION_MAP.get(watch.get("condition") or "")
    if cond:
        params["cond"] = cond
    return {k: v for k, v in params.items() if v != ""}


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.2f}".replace(".", ",")


def search(watch: dict, session) -> list[Listing]:
    r = get(session, f"{BASE}/search", params=build_params(watch))
    return parse(r.text)


def parse(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("li[data-auction-id]")
    if not items and "list-item" not in html and "keine" not in html.lower():
        raise SourceError("eGun: Ergebnisliste nicht gefunden (Seitenstruktur geändert?)")

    results = []
    for li in items:
        link = li.select_one("a.list-item__link")
        title = li.select_one(".list-item__title-text")
        if not link or not title:
            continue
        price_el = li.select_one(".list-item__price")
        price_text = price_el.get_text(" ", strip=True) if price_el else ""
        label_el = li.select_one(".list-item__price-label")
        label = label_el.get_text(" ", strip=True) if label_el else ""
        img = li.select_one("img")
        cond_el = li.select_one(".badge--condition")
        ends_el = li.select_one(".list-item__ends")
        is_auction = bool(re.search(r"gebot", label, re.I)) or bool(li.select_one(".list-item__bidstate--has"))

        results.append(Listing(
            site="egun",
            ext_id=str(li["data-auction-id"]),
            title=title.get_text(" ", strip=True),
            url=urljoin(BASE, str(link["href"])),
            price=parse_price(price_text),
            price_text=f"{price_text} ({label})" if label else price_text,
            image=urljoin(BASE, str(img["src"])) if img and img.get("src") else "",
            condition=cond_el.get_text(strip=True) if cond_el else "",
            is_auction=is_auction,
            extra={"ends": ends_el.get_text(strip=True)} if ends_el else {},
        ))
    return results
