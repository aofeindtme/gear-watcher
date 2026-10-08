from sources import Listing, parse_price
from sources import egun, kleinanzeigen
from watcher import apply_filters


def L(title, price, auction=False):
    return Listing(site="egun", ext_id=title, title=title, url="u", price=price, is_auction=auction)


def test_parse_price():
    assert parse_price("1.250 € VB") == 1250
    assert parse_price("35,72 €") == 35.72
    assert parse_price("1.300,00 € (Startpreis)") == 1300
    assert parse_price("Zu verschenken") == 0
    assert parse_price("VB") is None
    assert parse_price("") is None


def test_exclude_words_case_insensitive():
    items = [L("Leica Amplus 6", 900), L("SUCHE Leica Amplus", 0), L("Nerf Zielfernrohr", 5)]
    out = apply_filters({"exclude_words": "suche, nerf"}, items, set())
    assert [i.title for i in out] == ["Leica Amplus 6"]


def test_price_filter_and_auctions():
    items = [L("billig", 50), L("passt", 500), L("teuer", 5000), L("auktion", 1, auction=True), L("ohne preis", None)]
    w = {"min_price": 100, "max_price": 1000}
    # Seite filtert Preis selbst -> Auktionsgebote nicht nachfiltern
    assert [i.title for i in apply_filters(w, items, {"price"})] == ["passt", "auktion", "ohne preis"]
    # Seite ohne Preisfilter -> alles mit Preis nachfiltern
    assert [i.title for i in apply_filters(w, items, set())] == ["passt", "ohne preis"]


def test_egun_params():
    p = egun.build_params({"query": "zf", "min_price": 100, "max_price": 99.5, "zip_code": "24558",
                           "radius_km": 30, "condition": "used"})
    assert p == {"query": "zf", "order": "starts", "asdes": "desc", "minprice": "100", "maxprice": "99,50",
                 "zip": "24558", "radius": 50, "cond": "used"}


def test_kleinanzeigen_url(monkeypatch):
    monkeypatch.setattr(kleinanzeigen, "location_id", lambda z: "13734")
    assert kleinanzeigen.build_url({"query": "Leica Amplus"}) == \
        "https://www.kleinanzeigen.de/s-sortierung:neueste/leica-amplus/k0"
    assert kleinanzeigen.build_url({"query": "zf", "max_price": 1200, "zip_code": "24558", "radius_km": 40}) == \
        "https://www.kleinanzeigen.de/s-24558/preis::1200/sortierung:neueste/zf/k0l13734r50"


def test_parse_price_formats():
    assert parse_price("29,- €") == 29
    assert parse_price("1.050,- € VB") == 1050
    assert parse_price("€129.00") == 129
    assert parse_price("€1,299.00") == 1299
    assert parse_price("589,00 € *") == 589


def test_soft_hyphen_removed_from_titles():
    assert Listing(site="x", ext_id="1", title="Fernglasgurt Neo\xadpren  Outline", url="u").title == \
        "Fernglasgurt Neopren Outline"


def test_active_sites():
    from watcher import active_sites
    assert active_sites({"sites": "egun,url,nope", "query": "zf", "urls": ""}) == ["egun"]
    assert active_sites({"sites": "egun", "query": "zf", "urls": "https://a.de/x"}) == ["egun", "url"]
    assert active_sites({"sites": "egun", "query": "", "urls": "https://a.de/x"}) == ["url"]


def test_ebay_filter():
    from sources import ebay
    assert ebay.build_filter({"min_price": 100, "max_price": None, "condition": "used"}) == \
        "deliveryCountry:DE,price:[100..],priceCurrency:EUR,conditions:{USED}"


def test_url_product_page_microdata():
    from sources import url
    html = """<html><head><meta property="og:type" content="product"></head><body>
      <h1>Helikon Wallet</h1><meta itemprop="price" content="37.95">
      <link itemprop="availability" href="https://schema.org/OutOfStock"></body></html>"""
    item = url.parse_product_page(html, "https://shop.de/p/1")
    assert (item.title, item.price, item.available, item.location) == ("Helikon Wallet", 37.95, False, "shop.de")


def test_url_product_page_jsonld():
    from sources import url
    html = """<script type="application/ld+json">{"@type":"Product","name":"ZF","image":["https://i/x.jpg"],
      "offers":{"price":"499.00","availability":"https://schema.org/InStock"}}</script>"""
    item = url.parse_product_page(html, "https://www.shop.de/p/2")
    assert (item.title, item.price, item.available, item.image) == ("ZF", 499.0, True, "https://i/x.jpg")


def test_require_all_words_any_order():
    items = [L("Leica Amplus 6 2,5-15x56", 900), L("Amplus Leica Zielfernrohr", 800),
             L("Leica Fernglas", 500), L("Zeiss Conquest", 700)]
    w = {"query": "amplus leica", "require_all_words": 1}
    assert [i.title for i in apply_filters(w, items, set())] == ["Leica Amplus 6 2,5-15x56", "Amplus Leica Zielfernrohr"]
    w["require_all_words"] = 0
    assert len(apply_filters(w, items, set())) == 4


def test_all_words_not_applied_to_explicit_urls():
    item = Listing(site="url", ext_id="u", title="Helikon Wallet", url="u", price=10)
    assert apply_filters({"query": "swarovski z8", "require_all_words": 1}, [item], set()) == [item]


def test_egun_title_without_new_badge():
    html = """<li data-auction-id="7"><a class="list-item__link" href="/item/7/x">
      <span class="list-item__title-text"><span class="badge badge--new">neu</span> Zielfernrohr Z8i</span>
      <span class="list-item__price">100,00 €</span></a></li>"""
    assert egun.parse(html)[0].title == "Zielfernrohr Z8i"


def test_product_page_productgroup_picks_page_variant():
    import json as _json
    from sources import url
    ld = {"@type": "BuyAction", "object": {"@type": "ProductGroup", "name": "fenix 8", "hasVariant": [
        {"@type": "Product", "sku": "2948139", "name": "fenix 8 43 mm",
         "offers": {"url": "/de/product/_fenix-8-43-2948139.html", "price": 669.99,
                    "availability": "https://schema.org/InStock"}},
        {"@type": "Product", "sku": "2948141", "name": "fenix 8 47 mm",
         "offers": {"url": "/de/product/_fenix-8-47-2948141.html", "price": 699.0,
                    "availability": "https://schema.org/OutOfStock"}}]}}
    html = f'<script type="application/ld+json">{_json.dumps(ld)}</script>'
    item = url.parse_product_page(html, "https://www.saturn.de/de/product/_fenix-8-47-2948141.html")
    assert (item.title, item.price, item.available) == ("fenix 8 47 mm", 699.0, False)
