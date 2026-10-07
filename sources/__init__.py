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
    available: bool | None = None  # nur Shops: lieferbar? None = unbekannt
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        # Weiche Trennstriche (&shy;) würden Ausschlusswörter/Titelfilter aushebeln
        self.title = " ".join(self.title.replace("\xad", "").split())


class SourceError(Exception):
    """Abruf/Parsen einer Quelle fehlgeschlagen (wird geloggt, nicht geworfen)."""


def http_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "de-DE,de;q=0.9",
    })
    return s


def get(session: requests.Session, url: str, ok_status: tuple = (200,), **kwargs) -> requests.Response:
    try:
        r = session.get(url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as e:
        raise SourceError(f"{url}: {e}") from e
    if r.status_code not in ok_status:
        raise SourceError(f"{url}: HTTP {r.status_code}")
    # Ohne charset im Content-Type rät requests ISO-8859-1 -> "â‚¬" statt "€"
    if "charset" not in r.headers.get("Content-Type", "").lower():
        r.encoding = "utf-8"
    return r


_PRICE_RE = re.compile(r"€\s*(\d[\d.,]*(?:-)?)|(\d[\d.,]*(?:-)?)\s*(?:€|EUR\b)")


def _to_number(raw: str) -> float | None:
    """Deutsches ("1.250,00", "29,-") und englisches ("1,250.00") Format."""
    raw = raw.rstrip("-").rstrip(",.")
    if not raw:
        return None
    if "," in raw and "." in raw:
        dec = "," if raw.rfind(",") > raw.rfind(".") else "."
    elif "," in raw:
        dec = "," if len(raw) - raw.rfind(",") - 1 in (1, 2) else None
    elif "." in raw:
        dec = "." if len(raw) - raw.rfind(".") - 1 in (1, 2) else None
    else:
        dec = None
    if dec:
        whole, frac = raw.rsplit(dec, 1)
        whole = whole.replace(".", "").replace(",", "")
        return float(f"{whole or 0}.{frac}")
    return float(raw.replace(".", "").replace(",", ""))


def parse_price(text: str) -> float | None:
    """'1.250 € VB' -> 1250.0, '35,72 €' -> 35.72, '29,- €' -> 29.0, '€129.00' -> 129.0,
    'Zu verschenken' -> 0.0, 'VB' ohne Zahl -> None."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if m:
        return _to_number(m.group(1) or m.group(2))
    if "verschenken" in text.lower():
        return 0.0
    return None


def to_float(value) -> float | None:
    """Maschinenlesbare Preise ('37.95', '1099.0') aus Attributen/Microdata."""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


# Themen: steuern die Gruppen im Formular und welche Quellen eine Themenwahl vorbelegt.
TOPICS = {
    "jagd": "Jagd",
    "schiessen": "Schießsport & IPSC",
    "outdoor": "Outdoor",
    "bijou": "Bijou (Hund)",
    "bienen": "Bienen",
    "it": "IT",
    "gesundheit": "Gesundheit",
}
ALL_TOPICS = list(TOPICS)

# Registry: Schlüssel = wie in der DB gespeichert, Reihenfolge = Anzeige im UI.
# kind:    classifieds = Kleinanzeigen/Auktionen, deals = Deal-/Preisalarm-Quellen, shop = Neuware
# topics:  Themen der Quelle (das erste = Gruppe im Formular), ALL_TOPICS = themenübergreifend
# filters: was die Seite selbst filtert (Rest filtert watcher.apply_filters)
# needs:   Einstellungen, ohne die die Quelle nicht nutzbar ist
from . import (ebay, egun, frankonia, frankonia_kleinanzeigen, kleinanzeigen, mail, mydealz,  # noqa: E402
               shops, url)

SOURCES = {
    "kleinanzeigen": {"label": "Kleinanzeigen", "kind": "classifieds", "search": kleinanzeigen.search,
                      "filters": {"price", "zip"}, "topics": ALL_TOPICS},
    "ebay": {"label": "eBay", "kind": "classifieds", "search": ebay.search, "topics": ALL_TOPICS,
             "filters": {"price", "condition"}, "needs": ("ebay_client_id", "ebay_client_secret")},
    "mydealz": {"label": "mydealz", "kind": "deals", "search": mydealz.search, "filters": set(),
                "topics": ALL_TOPICS},
    "mail": {"label": "Preisalarm-Mails (idealo, Geizhals)", "kind": "deals", "search": mail.search,
             "filters": set(), "topics": ALL_TOPICS, "needs": ("imap_host", "imap_user", "imap_password")},
    "egun": {"label": "eGun", "kind": "classifieds", "search": egun.search,
             "filters": {"price", "zip", "condition"}, "topics": ["jagd", "schiessen"]},
    "frankonia_kleinanzeigen": {"label": "Frankonia-Kleinanzeigen", "kind": "classifieds",
                                "search": frankonia_kleinanzeigen.search, "filters": {"price", "zip"},
                                "topics": ["jagd", "schiessen"]},
    "frankonia": {"label": "Frankonia", "kind": "shop", "search": frankonia.search, "filters": set(),
                  "topics": ["jagd", "schiessen", "outdoor"]},
    # alle weiteren Shops aus sources/shops.py (konfigurationsgetrieben)
    **{key: {"label": shop["label"], "kind": "shop", "search": shops.make_search(key), "filters": set(),
             "topics": shop["topics"]}
       for key, shop in shops.SHOPS.items() if shop},
    # Kein Häkchen im Formular: läuft automatisch, sobald eine Suche URLs enthält.
    "url": {"label": "Shop-URL", "kind": "shop", "search": url.search, "filters": set(), "hidden": True,
            "topics": []},
}
