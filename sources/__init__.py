"""Quellen-Adapter: je Website ein Modul mit einer search()-Funktion.

Jeder Adapter bekommt eine Suche ("Watch", dict aus der DB) und liefert eine
Liste von Listing-Objekten zurück. Filter, die die Website selbst kann (Preis,
PLZ/Umkreis, Zustand), gibt der Adapter direkt in die Such-URL - alles
andere (Ausschlusswörter, Preis bei Seiten ohne Preisfilter) filtert
watcher.apply_filters() danach generisch.
"""
import re
from dataclasses import dataclass, field

import requests

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
TIMEOUT = 20


@dataclass
class Listing:
    site: str
    ext_id: str          # ID auf der Quell-Website, eindeutig je site
    title: str
    url: str
    price: float | None = None
    price_text: str = ""
    image: str = ""
    location: str = ""
    posted: str = ""     # Freitext wie die Seite ihn anzeigt ("Heute, 08:31")
    condition: str = ""  # Freitext ("Gebrauchsspuren", "neu", ...)
    is_auction: bool = False
    extra: dict = field(default_factory=dict)


class SourceError(Exception):
    """Abruf/Parsen einer Quelle fehlgeschlagen (wird geloggt, nicht geworfen)."""


def http_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "de-DE,de;q=0.9",
    })
    return s


def get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    try:
        r = session.get(url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as e:
        raise SourceError(f"{url}: {e}") from e
    if r.status_code != 200:
        raise SourceError(f"{url}: HTTP {r.status_code}")
    # Ohne charset im Content-Type rät requests ISO-8859-1 -> "â‚¬" statt "€"
    if "charset" not in r.headers.get("Content-Type", "").lower():
        r.encoding = "utf-8"
    return r


_PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?\s*€")


def parse_price(text: str) -> float | None:
    """'1.250 € VB' -> 1250.0, '35,72 €' -> 35.72, 'Zu verschenken' -> 0.0,
    'VB' ohne Zahl -> None."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if m:
        euros = int(m.group(1).replace(".", ""))
        cents = int((m.group(2) or "0").ljust(2, "0"))
        return euros + cents / 100
    if "verschenken" in text.lower():
        return 0.0
    return None


# Registry: Schlüssel = wie in der DB gespeichert, Reihenfolge = Anzeige im UI.
# "kind": classifieds -> Benachrichtigung bei neuen Anzeigen;
#         shop        -> zusätzlich bei Preissenkung.
from . import egun, kleinanzeigen  # noqa: E402

SOURCES = {
    "egun": {"label": "eGun", "kind": "classifieds", "search": egun.search,
             "filters": {"price", "zip", "condition"}},
    "kleinanzeigen": {"label": "Kleinanzeigen", "kind": "classifieds", "search": kleinanzeigen.search,
                      "filters": {"price", "zip"}},
}
