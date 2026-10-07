"""eBay.de über die offizielle Browse API (Scraping verbietet eBay ausdrücklich).

Benötigt eine kostenlose eBay-Developer-App (https://developer.ebay.com):
Client-ID ("App ID") und Client-Secret ("Cert ID") der Production-Umgebung
unter Einstellungen eintragen. Die App holt sich damit per Client-Credentials-
Flow ein Token (2 h gültig, wird gecacht).

Waffen sind auf eBay.de nicht erlaubt - sinnvoll für Optik, Bekleidung,
Zubehör, Messer usw.
"""
import base64
import time

import requests

from . import Listing, SourceError, to_float

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"

_token_cache: dict[str, tuple[float, str]] = {}

CONDITIONS = {"new": "NEW", "used": "USED"}


def _token(client_id: str, client_secret: str) -> str:
    cached = _token_cache.get(client_id)
    if cached and cached[0] > time.time() + 60:
        return cached[1]
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        r = requests.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": SCOPE},
            timeout=20,
        )
    except requests.RequestException as e:
        raise SourceError(f"eBay-Token: {e}") from e
    if r.status_code != 200:
        raise SourceError(f"eBay-Token: HTTP {r.status_code} {r.text[:200]}")
    data = r.json()
    _token_cache[client_id] = (time.time() + int(data.get("expires_in", 7200)), data["access_token"])
    return data["access_token"]


def build_filter(watch: dict) -> str:
    parts = ["deliveryCountry:DE"]
    lo, hi = watch.get("min_price"), watch.get("max_price")
    if lo is not None or hi is not None:
        parts.append(f"price:[{'' if lo is None else lo}..{'' if hi is None else hi}]")
        parts.append("priceCurrency:EUR")
    cond = CONDITIONS.get(watch.get("condition") or "")
    if cond:
        parts.append(f"conditions:{{{cond}}}")
    return ",".join(parts)


def search(watch: dict, session) -> list[Listing]:
    creds = watch.get("_settings") or {}  # vom Watcher übergebene Nutzereinstellungen
    if not creds.get("ebay_client_id") or not creds.get("ebay_client_secret"):
        raise SourceError("eBay: keine API-Zugangsdaten unter Einstellungen hinterlegt")
    token = _token(creds["ebay_client_id"], creds["ebay_client_secret"])
    try:
        r = requests.get(
            SEARCH_URL,
            headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_DE",
                     "Accept-Language": "de-DE"},
            params={"q": watch["query"], "filter": build_filter(watch), "sort": "newlyListed", "limit": 50},
            timeout=20,
        )
    except requests.RequestException as e:
        raise SourceError(f"eBay: {e}") from e
    if r.status_code != 200:
        raise SourceError(f"eBay: HTTP {r.status_code} {r.text[:200]}")
    return parse(r.json())


def _price_text(price: float | None, currency: str) -> str:
    """'1234.5', 'EUR' -> '1.234,50 €' (andere Währungen bleiben als Code stehen)."""
    if price is None:
        return ""
    text = f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} {'€' if currency in ('EUR', '') else currency}"


def parse(data: dict) -> list[Listing]:
    results = []
    for it in data.get("itemSummaries", []):
        options = it.get("buyingOptions") or []
        is_auction = "AUCTION" in options
        price_obj = (it.get("currentBidPrice") if is_auction else None) or it.get("price") or {}
        price = to_float(price_obj.get("value"))
        loc = it.get("itemLocation") or {}
        results.append(Listing(
            site="ebay",
            ext_id=str(it.get("itemId") or it.get("legacyItemId")),
            title=it.get("title", ""),
            url=it.get("itemWebUrl", ""),
            price=price,
            price_text=_price_text(price, price_obj.get("currency", "")) + (" (Gebot)" if is_auction else ""),
            image=(it.get("image") or {}).get("imageUrl", ""),
            location=" ".join(x for x in [loc.get("postalCode", ""), loc.get("city", "")] if x),
            condition=it.get("condition", ""),
            is_auction=is_auction,
        ))
    return results
