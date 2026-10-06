"""Kleinanzeigen (kleinanzeigen.de).

Such-URLs sind pfadbasiert:
    /s-<plz>/preis:<min>:<max>/sortierung:neueste/<suchbegriff>/k0l<ortsId>r<radius>
Die Orts-ID zur PLZ kommt aus /s-ort-empfehlungen.json (dieselbe Abfrage,
die die Website beim Tippen ins Ortsfeld macht) und wird gecacht.
"""
import re
from functools import lru_cache
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from . import Listing, SourceError, get, http_session, parse_price

BASE = "https://www.kleinanzeigen.de"
RADIUS_STEPS = [5, 10, 20, 30, 50, 100, 150, 200]


@lru_cache(maxsize=64)
def location_id(zip_code: str) -> str | None:
    r = get(http_session(), f"{BASE}/s-ort-empfehlungen.json", params={"query": zip_code})
    for key, label in r.json().items():
        if key != "_0" and label.startswith(zip_code):
            return key.lstrip("_")
    return None


def build_url(watch: dict) -> str:
    segments = []
    loc_suffix = ""
    if watch.get("zip_code") and watch.get("radius_km"):
        loc = location_id(watch["zip_code"])
        if not loc:
            raise SourceError(f"Kleinanzeigen: PLZ {watch['zip_code']} unbekannt")
        radius = next((r for r in RADIUS_STEPS if r >= watch["radius_km"]), RADIUS_STEPS[-1])
        segments.append(f"s-{watch['zip_code']}")
        loc_suffix = f"l{loc}r{radius}"
    min_p, max_p = watch.get("min_price"), watch.get("max_price")
    if min_p is not None or max_p is not None:
        segments.append(f"preis:{_int(min_p)}:{_int(max_p)}")
    segments.append("sortierung:neueste")
    # erstes Segment braucht das "s-"-Präfix
    if not segments[0].startswith("s-"):
        segments[0] = "s-" + segments[0]
    query = quote(watch["query"].strip().replace(" ", "-").lower())
    return f"{BASE}/{'/'.join(segments)}/{query}/k0{loc_suffix}"


def _int(v) -> str:
    return "" if v is None else str(int(v))


def search(watch: dict, session) -> list[Listing]:
    r = get(session, build_url(watch))
    return parse(r.text)


_DATE_RE = re.compile(r"^(Heute|Gestern|\d{2}\.\d{2}\.\d{4})")
_PRICE_HINT_RE = re.compile(r"€|VB|verschenken", re.I)


def parse(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    articles = soup.select("article[data-adid]")
    if not articles and "data-adid" not in html and "keine ergebnisse" not in html.lower():
        raise SourceError("Kleinanzeigen: Ergebnisliste nicht gefunden (Seitenstruktur geändert?)")

    results = []
    for art in articles:
        href = str(art.get("data-href") or "")
        title_el = art.select_one("h2, h3")
        if not href or not title_el:
            continue

        # Ort + Datum stehen als <span> in der Kopfzeile mit dem Standort-Icon
        location = posted = ""
        icon = art.select_one('svg[data-title="locationOutline"]')
        if icon and icon.parent and icon.parent.parent:
            spans = [s.get_text(" ", strip=True) for s in icon.parent.parent.find_all("span")]
            spans = [s for s in spans if s]
            for s in spans:
                if _DATE_RE.match(s):
                    posted = s
                elif not location:
                    location = s

        price_text = ""
        for p in art.find_all("p"):
            t = p.get_text(" ", strip=True)
            if t and len(t) < 40 and _PRICE_HINT_RE.search(t):
                price_text = t
                break

        img = art.select_one("img")
        results.append(Listing(
            site="kleinanzeigen",
            ext_id=str(art["data-adid"]),
            title=title_el.get_text(" ", strip=True),
            url=urljoin(BASE, href),
            price=parse_price(price_text),
            price_text=price_text,
            image=str(img["src"]) if img and img.get("src") else "",
            location=location,
            posted=posted,
        ))
    return results
