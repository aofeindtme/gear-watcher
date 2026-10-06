"""Online-Shops (Neuware), konfigurationsgetrieben.

Die meisten Händler laufen auf einem Standard-Shopsystem. Pro System gibt es
hier einen Parser für die Suchergebnisseite, pro Shop nur eine Zeile in SHOPS
(Basis-URL, Suchpfad, Parser). Shops mit Eigenbau bekommen einen eigenen Parser.

Aufgenommen wird ein Shop nur, wenn seine robots.txt die Such-URL erlaubt und die
Ergebnisse ohne JavaScript im HTML (oder über eine öffentliche JSON-API) stehen.
"""
import json
import re
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from . import Listing, SourceError, get, parse_price, to_float
from .shopware import parse_sw5_boxes, parse_sw6_boxes

EMPTY_MARKERS = ("keine artikel", "keine produkte", "keine treffer", "keine ergebnisse", "0 treffer",
                 "no result", "no products", "nichts gefunden", "leider keine", "ergab keine",
                 "nothing found", "0 artikel", "0 produkte", "nicht gefunden", "no matching", "0 results")


def _availability(value: str | None) -> bool | None:
    v = (value or "").lower().rsplit("/", 1)[-1]
    if any(s in v for s in ("outofstock", "soldout", "discontinued")):
        return False
    if any(s in v for s in ("instock", "limitedavailability", "preorder", "onlineonly")):
        return True
    return None


def _img(el, base: str) -> str:
    if not el:
        return ""
    src = el.get("data-src") or el.get("src") or (el.get("srcset") or el.get("data-srcset") or "").split(" ")[0]
    src = str(src or "")
    if not src or src.startswith("data:"):
        return ""
    return urljoin(base, src)


# ----------------------------------------------------------------------
# Parser je Shopsystem
# ----------------------------------------------------------------------

def parse_microdata_list(html: str, site: str, base: str) -> list[Listing]:
    """schema.org/Product-Microdata je Kachel (JTL-Shop 5 und viele andere)."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for scope in soup.select('[itemtype*="schema.org/Product"]'):
        if scope.find_parent(attrs={"itemtype": re.compile("schema.org/Product")}):
            continue  # verschachtelt
        price_el = scope.select_one('[itemprop="price"]')
        # itemprop="name" auch in verschachtelten Scopes (Marke) -> nur die der Produkt-Ebene
        name_el = next((n for n in scope.select('[itemprop="name"]')
                        if n.find_parent(attrs={"itemscope": True}) is scope), None)
        link = scope.select_one("a[href]")
        if not price_el or not name_el or not link:
            continue
        url = urljoin(base, str(link["href"]))
        avail = scope.select_one('[itemprop="availability"]')
        offers_text = scope.select_one('[itemprop="offers"]')
        results.append(Listing(
            site=site,
            ext_id=url,
            title=(name_el.get("content") or name_el.get_text(" ", strip=True)),
            url=url,
            price=to_float(price_el.get("content")) if price_el.get("content") else parse_price(price_el.get_text()),
            price_text=offers_text.get_text(" ", strip=True).replace("*", "").strip()[:40] if offers_text else "",
            image=_img(scope.select_one("img"), base),
            available=_availability(avail.get("href") or avail.get("content")) if avail else None,
        ))
    return results


def parse_woo_store_api(data: list, site: str) -> list[Listing]:
    """WooCommerce Store API (/wp-json/wc/store/v1/products) - öffentlich, JSON."""
    results = []
    for p in data:
        prices = p.get("prices") or {}
        minor = int(prices.get("currency_minor_unit") or 2)
        raw = to_float(prices.get("price"))
        price = raw / 10 ** minor if raw is not None else None
        regular = to_float(prices.get("regular_price"))
        regular = regular / 10 ** minor if regular is not None else None
        results.append(Listing(
            site=site,
            ext_id=str(p.get("id")),
            title=BeautifulSoup(p.get("name") or "", "html.parser").get_text(),
            url=p.get("permalink") or "",
            price=price,
            price_text=(f"{price:.2f} €".replace(".", ",") if price is not None else "")
            + (f" (statt {regular:.2f} €)".replace(".", ",") if regular and price and regular > price else ""),
            image=((p.get("images") or [{}])[0] or {}).get("src", ""),
            condition="Angebot" if p.get("on_sale") else "",
            available=bool(p.get("is_in_stock")) if p.get("is_in_stock") is not None else None,
        ))
    return results


def parse_grube(html: str, site: str, base: str) -> list[Listing]:
    """Grube (novomind iShop): Ergebnisse als JSON im App-State (elementsList)."""
    m = re.search(r"elementsList\s*:\s*\[", html)
    if not m:
        return []
    try:
        items, _ = json.JSONDecoder().raw_decode(html[m.end() - 1:])
    except ValueError as e:
        raise SourceError(f"Grube: App-State nicht lesbar ({e})") from e
    results = []
    for el in items:
        d = el.get("data") or {}
        if el.get("type") != "product" or not d.get("name"):
            continue
        price = d.get("price") or {}
        current = to_float(price.get("priceForSchemaOrgOffer")) or parse_price(f"{price.get('current', '')} €")
        old = parse_price(f"{price.get('old', '')} €")
        url = urljoin(base, str(d.get("url") or "").split("?")[0])
        img = ((d.get("images") or [{}])[0] or {}).get("src", "")
        results.append(Listing(
            site=site,
            ext_id=str(d.get("productId") or d.get("id")),
            title=d["name"],
            url=url,
            price=current,
            price_text=("ab " if price.get("isFromPrice") else "") + f"{price.get('current', '').strip()} €"
            + (f" (statt {price.get('old', '').strip()} €)" if old else ""),
            image=img,
            condition=", ".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in d.get("labels") or [])[:60],
        ))
    return results


def parse_askari(html: str, site: str, base: str) -> list[Listing]:
    """jagd.de (Askari, OXID-basiert)."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for art in soup.select(".articles article.product"):  # ohne Empfehlungen bei 0 Treffern
        box = art.select_one("[data-product-id]")
        link = art.select_one("a[href$='.html']")
        title_el = art.select_one(".item-title, .title, h2, h3")
        if not box or not link:
            continue
        price_el = art.select_one(".newprice") or art.select_one(".normalprice") or art.select_one(".price")
        price_text = price_el.get_text(" ", strip=True).replace("UVP", "").strip() if price_el else ""
        title = title_el.get_text(" ", strip=True) if title_el else str(link.get("title") or "")
        if not title:
            img = art.select_one("img")
            title = str(img.get("alt") or "").strip() if img else ""
        results.append(Listing(
            site=site,
            ext_id=str(box.get("data-product-id")),
            title=title,
            url=urljoin(base, str(link["href"])),
            price=parse_price(price_text.replace("€", "") + " €") if price_text else None,
            price_text=price_text,
            image=_img(art.select_one("img"), base),
        ))
    return results


def parse_revolutionrace(html: str, site: str, base: str) -> list[Listing]:
    """Revolution Race (Nuxt, serverseitig gerendert): <a data-testid="ProductCard-link">."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for card in soup.select('a[data-testid="ProductCard-link"]'):
        href = str(card.get("href") or "")
        label = str(card.get("aria-label") or "")
        title = re.split(r"\s+(?:Originalpreis|Preis|Rabattierter Preis):", label)[0]
        title = re.sub(r"\s+\d[\d.,]*,?\s*€.*$", "", title).strip()  # "Hiball Jacket Herren 129, €"
        selling = card.select_one(".campaign-price__selling-price") or card.select_one(".standard-price__price") \
            or card.select_one("[class*=price]")
        listed = card.select_one(".campaign-price__listed-price")
        price_text = selling.get_text(" ", strip=True) if selling else ""
        if not title or not href:
            continue
        results.append(Listing(
            site=site,
            ext_id=href,
            title=title,
            url=urljoin(base, href),
            price=parse_price(price_text),
            price_text=price_text + (f" (statt {listed.get_text(' ', strip=True)})"
                                     if listed and selling and listed is not selling else ""),
            image=_img(card.select_one("img"), base),
        ))
    return results


def parse_doublealpha(html: str, site: str, base: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for box in soup.select(".item-holder"):
        name = box.select_one(".name a")
        price_el = box.select_one('[itemprop="price"]')
        if not name or not price_el:
            continue
        url = urljoin(base, str(name["href"]))
        stock = box.select_one(".stock")
        stock_text = stock.get_text(" ", strip=True).lower() if stock else ""
        results.append(Listing(
            site=site,
            ext_id=url,
            title=name.get_text(" ", strip=True),
            url=url,
            price=to_float(price_el.get("content")),
            price_text=f"{price_el.get_text(strip=True)} €",
            image=_img(box.select_one("img"), base),
            available=False if "out of stock" in stock_text else (True if stock_text else None),
        ))
    return results


def parse_modified(html: str, site: str, base: str) -> list[Listing]:
    """modified eCommerce (Shooters First Choice): Tabellen-Layout .productPreview."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for box in soup.select(".productPreview"):
        link = box.select_one("a[href]")
        img = box.select_one("img")
        price_el = box.select_one(".price")
        if not link or not price_el:
            continue
        url = re.sub(r"[?&]MODsid=[^&]+", "", urljoin(base, str(link["href"])))
        title = str(img.get("alt") or "") if img else ""
        if not title:
            slug = url.rsplit("/", 1)[-1].split("::")[0]
            title = slug.replace("-", " ")
        price_text = price_el.get_text(" ", strip=True)
        results.append(Listing(
            site=site,
            ext_id=url,
            title=title,
            url=url,
            price=parse_price(price_text if "€" in price_text else price_text + " €"),
            price_text=price_text if "€" in price_text else price_text + " €",
            image=_img(img, base),
        ))
    return results


# ----------------------------------------------------------------------
# Shop-Liste
# ----------------------------------------------------------------------
# path: Such-URL relativ zur Basis, {q} = Suchbegriff (URL-kodiert)
# parser: Funktion(html, site, base) oder "woo" für die WooCommerce-Store-API
# empty_marker (optional): Text im HTML, an dem eine leere Ergebnisseite erkennbar ist
# Reihenfolge = Anzeige im Formular.

SHOPS = {
    "frankonia": None,  # eigener Adapter (sources/frankonia.py), hier nur für die Reihenfolge
    "pirschergear": {"label": "Pirscher Gear", "base": "https://www.pirschergear.com",
                     "path": "/search?search={q}", "parser": parse_sw6_boxes},
    "pirschershop": {"label": "Pirscher Shop", "base": "https://www.pirschershop.de",
                     "path": "/search?search={q}", "parser": parse_sw6_boxes},
    "hubertus": {"label": "Hubertus Fieldsports", "base": "https://www.hubertus-fieldsports.de",
                 "path": "/search?search={q}", "parser": parse_sw6_boxes},
    "grube": {"label": "Grube", "base": "https://www.grube.de",
              "path": "/search/?q={q}", "parser": parse_grube},
    "jagd_de": {"label": "jagd.de (Askari)", "base": "https://www.jagd.de",
                "path": "/index.php?cl=search&searchparam={q}&listorderby=Insert&listorder=desc",
                "parser": parse_askari},
    "jagdwelt24": {"label": "Jagdwelt24", "base": "https://www.jagdwelt24.de",
                   "path": "/search/?qs={q}", "parser": parse_microdata_list},
    "revolutionrace": {"label": "Revolution Race", "base": "https://www.revolutionrace.de",
                       "path": "/suche?q={q}", "parser": parse_revolutionrace},
    "triebel": {"label": "Sportwaffen Triebel", "base": "https://sportwaffen-triebel.de",
                "path": "/search?sSearch={q}", "parser": parse_sw5_boxes},
    "shootingequipment": {"label": "Shooting Equipment", "base": "https://shootingequipment.de",
                          "path": "/wp-json/wc/store/v1/products?search={q}&per_page=50&orderby=date&order=desc",
                          "parser": "woo"},
    "atlas": {"label": "Atlas Taktik", "base": "https://www.atlas-taktik.de",
              "path": "/search?sSearch={q}", "parser": parse_sw5_boxes},
    "shootingsolutions": {"label": "Shooting Solutions", "base": "https://shooting-solutions.de",
                          "path": "/search/?qs={q}", "parser": parse_microdata_list},
    "doublealpha": {"label": "Double Alpha", "base": "https://www.doublealpha.biz",
                    "path": "/catalog/all-products?keywords={q}", "parser": parse_doublealpha,
                    # leere Suche = leere Katalogseite ohne Hinweistext
                    "empty_marker": "p-catalog-all-products"},
    "sfc": {"label": "Shooters First Choice", "base": "https://www.shooters-first-choice.de",
            "path": "/advanced_search_result.php?keywords={q}", "parser": parse_modified},
}


def make_search(key: str):
    shop = SHOPS[key]

    def search(watch: dict, session) -> list[Listing]:
        url = shop["base"] + shop["path"].format(q=quote_plus(watch["query"]))
        r = get(session, url, ok_status=(200, 404, 410))  # manche Shops: "keine Treffer" = 404/410
        if shop["parser"] == "woo":
            if r.status_code != 200:
                raise SourceError(f"{shop['label']}: HTTP {r.status_code}")
            return parse_woo_store_api(r.json(), key)
        html = r.text
        items = shop["parser"](html, key, shop["base"])
        if items:
            return items
        lower = html.lower()
        if any(m in lower for m in EMPTY_MARKERS) or (shop.get("empty_marker") or "\0") in html:
            return []
        if r.status_code != 200:
            raise SourceError(f"{shop['label']}: HTTP {r.status_code}")
        raise SourceError(f"{shop['label']}: Ergebnisliste nicht gefunden (Seitenstruktur geändert?)")

    return search
