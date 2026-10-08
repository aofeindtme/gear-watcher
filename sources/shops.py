"""Online-Shops (Neuware), konfigurationsgetrieben.

Die meisten Händler laufen auf einem Standard-Shopsystem. Pro System gibt es
hier einen Parser für die Suchergebnisseite, pro Shop nur eine Zeile in SHOPS
(Basis-URL, Suchpfad, Parser). Shops mit Eigenbau bekommen einen eigenen Parser.

Aufgenommen wird ein Shop nur, wenn seine robots.txt die Such-URL erlaubt und die
Ergebnisse ohne JavaScript im HTML (oder über eine öffentliche JSON-API) stehen.
"""
import json
import re
from urllib.parse import quote_plus, urljoin, urlsplit

from bs4 import BeautifulSoup

from . import Listing, SourceError, get, parse_price, to_float
from .shopware import UNAVAILABLE_HINTS, parse_sw5_boxes, parse_sw6_boxes

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


def _euro(value) -> str:
    v = str(value or "").strip()
    return v if not v or "€" in v else f"{v} €"


def parse_grube(html: str, site: str, base: str) -> list[Listing]:
    """novomind iShop (Grube, Bergzeit): Ergebnisse als JSON im App-State (elementsList)."""
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
        cur_text = _euro(price.get("current"))
        old_text = _euro(price.get("old"))
        current = to_float(price.get("priceForSchemaOrgOffer")) or parse_price(cur_text)
        old = parse_price(old_text)
        url = urljoin(base, str(d.get("url") or "").split("?")[0])
        img = ((d.get("images") or [{}])[0] or {}).get("src", "")
        results.append(Listing(
            site=site,
            ext_id=str(d.get("productId") or d.get("id")),
            title=d["name"],
            url=url,
            price=current,
            price_text=("ab " if price.get("isFromPrice") else "") + cur_text
            + (f" (statt {old_text})" if old else ""),
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


def parse_mediamarkt(html: str, site: str, base: str) -> list[Listing]:
    """MediaMarkt (React, serverseitig gerendert). CSS-Klassen sind Hashes, deshalb nur data-test.
    Preis-Block: optional Streichpreis (nach "UVP"/"statt"-Label), dann aktueller Preis."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for card in soup.select("[data-test=mms-product-card]"):
        title_el = card.select_one("[data-test=product-title]")
        link = card.select_one("a[href*='/product/']")
        price_box = card.select_one("[data-test=mms-price]")
        if not title_el or not link or not price_box:
            continue
        prices = [s.get_text(" ", strip=True) for s in price_box.select("span[aria-hidden=true]")
                  if "€" in s.get_text()]
        if not prices:
            continue
        has_strike = price_box.select_one("[data-test=mms-strike-price-label]") is not None and len(prices) > 1
        current = prices[-1] if has_strike else prices[0]
        url = urljoin(base, str(link["href"])).split("?")[0]
        m = re.search(r"-(\d+)\.html$", url)
        delivery = card.select_one("[data-test=product-delivery]")
        delivery_text = delivery.get_text(" ", strip=True).lower() if delivery else ""
        results.append(Listing(
            site=site,
            ext_id=m.group(1) if m else url,
            title=title_el.get_text(" ", strip=True),
            url=url,
            price=parse_price(current),
            price_text=current + (f" (statt {prices[0]})" if has_strike else ""),
            image=_img(card.select_one("[data-test=product-image] img, img"), base),
            # Fremdhändler über den MediaMarkt-Marktplatz
            condition="Marktplatz" if card.select_one("[data-test=mms-third-party-provider-link]") else "",
            available=(not any(h in delivery_text for h in UNAVAILABLE_HINTS)) if delivery_text else None,
        ))
    return results


def parse_cards(html: str, site: str, base: str, cfg: dict) -> list[Listing]:
    """Produktkacheln per CSS-Selektoren aus der Shop-Konfiguration.

    cfg: card (Kachel), link (darin; fehlt = Kachel ist selbst der Link), title (Liste von
    Selektoren, Texte werden verbunden; fehlt = title-Attribut/Text des Links), price,
    old (Streichpreis, optional), stock (Lieferstatus-Text, optional), id_attr (optional)."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for card in soup.select(cfg["card"]):
        link = card if not cfg.get("link") else card.select_one(cfg["link"])
        price_el = card.select_one(cfg["price"])
        if not link or not link.get("href") or not price_el:
            continue
        if cfg.get("title"):
            parts = [el.get_text(" ", strip=True) for sel in cfg["title"] for el in card.select(sel)[:1]]
            title = " ".join(p for p in parts if p)
        else:
            title = str(link.get("title") or link.get_text(" ", strip=True))
        url = urljoin(base, str(link["href"])).split("#")[0].split("?")[0]
        if urlsplit(url).netloc != urlsplit(base).netloc:
            continue  # gesponserte Kachel mit Tracking-Link
        price_text = price_el.get_text(" ", strip=True)
        old_el = card.select_one(cfg["old"]) if cfg.get("old") else None
        stock_el = card.select_one(cfg["stock"]) if cfg.get("stock") else None
        stock = stock_el.get_text(" ", strip=True).lower() if stock_el else ""
        if not title:
            continue
        results.append(Listing(
            site=site,
            ext_id=str(card.get(cfg["id_attr"]) or url) if cfg.get("id_attr") else url,
            title=title,
            url=url,
            price=parse_price(price_text),
            price_text=price_text + (f" (statt {old_el.get_text(' ', strip=True)})" if old_el else ""),
            image=_img(card.select_one("img"), base),
            available=(not any(h in stock for h in UNAVAILABLE_HINTS)) if stock else None,
        ))
    return results


def parse_shopify_suggest(data: dict, site: str, base: str) -> list[Listing]:
    """Shopify Predictive Search (/search/suggest.json) - öffentlich, max. 10 Produkte."""
    results = []
    for p in ((data.get("resources") or {}).get("results") or {}).get("products") or []:
        price = to_float(p.get("price"))
        old = to_float(p.get("compare_at_price_max"))
        url = urljoin(base, str(p.get("url") or "").split("?")[0])
        results.append(Listing(
            site=site,
            ext_id=str(p.get("id") or url),
            title=p.get("title") or "",
            url=url,
            price=price,
            price_text=(f"{price:.2f} €".replace(".", ",") if price is not None else "")
            + (f" (statt {old:.2f} €)".replace(".", ",") if old and price and old > price else ""),
            image=p.get("image") or p.get("featured_image", {}).get("url", "") or "",
            available=p.get("available"),
        ))
    return results


# ----------------------------------------------------------------------
# Shop-Liste
# ----------------------------------------------------------------------
# path: Such-URL relativ zur Basis, {q} = Suchbegriff (URL-kodiert)
# parser: Funktion(html, site, base), "woo" (WooCommerce-Store-API), "shopify"
#         (Predictive-Search-JSON) oder "cards" (CSS-Selektoren unter "cards")
# topics: Themen (sources.TOPICS), das erste bestimmt die Gruppe im Formular
# empty_marker (optional): Text im HTML, an dem eine leere Ergebnisseite erkennbar ist
# fuzzy (optional): Shop zeigt ohne Treffer Ersatzprodukte/Bestseller - Ergebnisse
#        werden deshalb immer auf "alle Suchwörter im Titel" gefiltert.
# Reihenfolge = Anzeige im Formular.

SHOPIFY_PATH = "/search/suggest.json?q={q}&resources[type]=product&resources[limit]=10"
WOO_PATH = "/wp-json/wc/store/v1/products?search={q}&per_page=50&orderby=date&order=desc"

SHOPS = {
    # --- Jagd ---
    "frankonia": None,  # eigener Adapter (sources/frankonia.py), hier nur für die Reihenfolge
    "pirschergear": {"label": "Pirscher Gear", "base": "https://www.pirschergear.com", "topics": ["jagd", "outdoor"],
                     "path": "/search?search={q}", "parser": parse_sw6_boxes},
    "pirschershop": {"label": "Pirscher Shop", "base": "https://www.pirschershop.de", "topics": ["jagd"],
                     "path": "/search?search={q}", "parser": parse_sw6_boxes},
    "hubertus": {"label": "Hubertus Fieldsports", "base": "https://www.hubertus-fieldsports.de",
                 "topics": ["jagd", "outdoor"], "path": "/search?search={q}", "parser": parse_sw6_boxes},
    "jagd_de": {"label": "jagd.de (Askari)", "base": "https://www.jagd.de", "topics": ["jagd"],
                "path": "/index.php?cl=search&searchparam={q}&listorderby=Insert&listorder=desc",
                "parser": parse_askari},
    "jagdwelt24": {"label": "Jagdwelt24", "base": "https://www.jagdwelt24.de", "topics": ["jagd"],
                   "path": "/search/?qs={q}", "parser": parse_microdata_list},
    # nur österreichischer Store (Preise inkl. AT-MwSt.)
    "kettner": {"label": "Kettner (AT)", "base": "https://www.kettner.com", "topics": ["jagd", "outdoor"],
                "path": "/at_de/catalogsearch/result/?q={q}", "parser": "cards",
                "cards": {"card": ".product-item-info", "link": "a.product-item-link",
                          "title": ["a.product-item-link"],
                          "price": "[data-price-type=finalPrice] .price, .price-final_price .price",
                          "old": "[data-price-type=oldPrice] .price"}},
    # --- Schießsport & IPSC ---
    "triebel": {"label": "Sportwaffen Triebel", "base": "https://sportwaffen-triebel.de", "topics": ["schiessen", "jagd"],
                "path": "/search?sSearch={q}", "parser": parse_sw5_boxes},
    "shootingequipment": {"label": "Shooting Equipment", "base": "https://shootingequipment.de",
                          "topics": ["schiessen"], "path": WOO_PATH, "parser": "woo"},
    "atlas": {"label": "Atlas Taktik", "base": "https://www.atlas-taktik.de", "topics": ["schiessen"],
              "path": "/search?sSearch={q}", "parser": parse_sw5_boxes},
    "shootingsolutions": {"label": "Shooting Solutions", "base": "https://shooting-solutions.de",
                          "topics": ["schiessen"], "path": "/search/?qs={q}", "parser": parse_microdata_list},
    "doublealpha": {"label": "Double Alpha", "base": "https://www.doublealpha.biz", "topics": ["schiessen"],
                    "path": "/catalog/all-products?keywords={q}", "parser": parse_doublealpha,
                    # leere Suche = leere Katalogseite ohne Hinweistext
                    "empty_marker": "p-catalog-all-products"},
    "sfc": {"label": "Shooters First Choice", "base": "https://www.shooters-first-choice.de", "topics": ["schiessen"],
            "path": "/advanced_search_result.php?keywords={q}", "parser": parse_modified},
    "dynamicshooting": {"label": "Dynamic Shooting (AT)", "base": "https://www.dynamic-shooting.at",
                        "topics": ["schiessen"], "path": SHOPIFY_PATH, "parser": "shopify"},
    # --- Outdoor ---
    "grube": {"label": "Grube", "base": "https://www.grube.de", "topics": ["outdoor", "jagd"],
              "path": "/search/?q={q}", "parser": parse_grube},
    "bergzeit": {"label": "Bergzeit", "base": "https://www.bergzeit.de", "topics": ["outdoor"],
                 "path": "/search/?q={q}", "parser": parse_grube},
    "revolutionrace": {"label": "Revolution Race", "base": "https://www.revolutionrace.de", "topics": ["outdoor"],
                       "path": "/suche?q={q}", "parser": parse_revolutionrace},
    # --- Bijou (Hund) ---
    "zooplus": {"label": "Zooplus", "base": "https://www.zooplus.de", "topics": ["bijou"],
                "path": "/search/results?q={q}", "parser": "cards", "cards": "zooplus"},
    "bitiba": {"label": "Bitiba", "base": "https://www.bitiba.de", "topics": ["bijou"],
               "path": "/search/results?q={q}", "parser": "cards", "cards": "zooplus"},
    "fressnapf": {"label": "Fressnapf", "base": "https://www.fressnapf.de", "topics": ["bijou"],
                  "path": "/search/?text={q}", "parser": "cards",
                  "cards": {"card": ".product-teaser", "link": "a.pt-header",
                            "title": [".pt-subhead", ".pt-head"], "price": ".pt-price"}},
    "ruffwear": {"label": "Ruffwear", "base": "https://ruffwear.eu", "topics": ["bijou", "outdoor"],
                 "path": SHOPIFY_PATH, "parser": "shopify"},
    # --- Bienen ---
    "bienenruck": {"label": "Bienen Ruck", "base": "https://www.bienen-ruck.de", "topics": ["bienen"],
                   "path": "/search?search={q}", "parser": parse_sw6_boxes},
    "graze": {"label": "Graze", "base": "https://www.graze.eu", "topics": ["bienen"],
              "path": "/search?q={q}", "parser": parse_microdata_list},
    "kellmann": {"label": "Kellmann", "base": "https://kellmann.de", "topics": ["bienen"],
                 "path": WOO_PATH, "parser": "woo"},
    # --- IT ---
    "mindfactory": {"label": "Mindfactory", "base": "https://www.mindfactory.de", "topics": ["it"],
                    "path": "/search_result.php?search_query={q}", "parser": "cards",
                    "cards": {"card": ".pcontent", "link": "a.p-complete-link", "title": [".pname"],
                              "price": ".pprice", "stock": ".pshipping"}},
    "alternate": {"label": "Alternate", "base": "https://www.alternate.de", "topics": ["it"], "fuzzy": True,
                  "path": "/listing.xhtml?q={q}", "parser": "cards",
                  "cards": {"card": "a.productBox", "title": [".product-name", ".product-name-sub"],
                            "price": ".price", "stock": ".delivery-info"}},
    "refurbed": {"label": "Refurbed", "base": "https://www.refurbed.de", "topics": ["it"], "fuzzy": True,
                 "path": "/search/?query={q}", "parser": "cards",
                 "cards": {"card": "main.product-list-container article", "link": "a", "title": ["h3"],
                           "price": "span.font-semibold", "old": "del"}},
    # sucht unscharf (zeigt z. B. fenix 9 zu "fenix 8"). Saturn (gleiche Plattform) und Coolblue
    # sperren ihre Suche per robots.txt -> dort nur Produkt-URLs beobachten
    "mediamarkt": {"label": "MediaMarkt", "base": "https://www.mediamarkt.de", "topics": ["it"], "fuzzy": True,
                   "path": "/de/search.html?query={q}", "parser": parse_mediamarkt},
    "afb": {"label": "AfB (refurbished)", "base": "https://www.afbshop.de", "topics": ["it"],
            "path": "/search?search={q}", "parser": parse_sw6_boxes},
    # --- Gesundheit ---
    "shopapotheke": {"label": "Shop Apotheke", "base": "https://www.shop-apotheke.com", "topics": ["gesundheit"],
                     "fuzzy": True,
                     "path": "/search.htm?q={q}", "parser": "cards",
                     "cards": {"card": "[data-qa-id=result-list-entry]", "link": "a.link_overlay",
                               "title": ["[data-qa-id=serp-result-item-title]"],
                               "price": "[data-qa-id=entry-price]", "stock": "[data-qa-id=product-status-qa-id]"}},
}

# Mehrfach genutzte Kachel-Konfigurationen
CARD_PRESETS = {
    "zooplus": {"card": "[data-zta=product-card]", "link": "a[data-zta=product-info]", "id_attr": "data-variant-id",
                "title": ["[data-zta=product-link]", "[data-zta=variant-desc]"],
                "price": "[data-zta=reducedPriceAmount], .z-product-price__amount",
                "old": "[data-zta=reducedPriceRefPriceAmount]"},
}


def make_search(key: str):
    shop = SHOPS[key]

    def search(watch: dict, session) -> list[Listing]:
        url = shop["base"] + shop["path"].format(q=quote_plus(watch["query"]))
        r = get(session, url, ok_status=(200, 404, 410))  # manche Shops: "keine Treffer" = 404/410
        if shop["parser"] in ("woo", "shopify"):
            if r.status_code != 200:
                raise SourceError(f"{shop['label']}: HTTP {r.status_code}")
            if shop["parser"] == "woo":
                return parse_woo_store_api(r.json(), key)
            return parse_shopify_suggest(r.json(), key, shop["base"])
        if shop.get("fuzzy"):
            words = [w.lower() for w in watch["query"].split()]
            return [i for i in _parse(r.text) if all(w in i.title.lower() for w in words)]
        return _check_empty(r, _parse(r.text))

    def _parse(html: str) -> list[Listing]:
        if shop["parser"] == "cards":
            cfg = shop["cards"]
            return parse_cards(html, key, shop["base"], CARD_PRESETS[cfg] if isinstance(cfg, str) else cfg)
        return shop["parser"](html, key, shop["base"])

    def _check_empty(r, items: list[Listing]) -> list[Listing]:
        html = r.text
        if items:
            return items
        lower = html.lower()
        if any(m in lower for m in EMPTY_MARKERS) or (shop.get("empty_marker") or "\0") in html:
            return []
        if r.status_code != 200:
            raise SourceError(f"{shop['label']}: HTTP {r.status_code}")
        raise SourceError(f"{shop['label']}: Ergebnisliste nicht gefunden (Seitenstruktur geändert?)")

    return search
