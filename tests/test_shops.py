import json

from sources import shops


def test_microdata_list_uses_product_name_not_brand():
    html = """<div itemscope itemtype="https://schema.org/Product">
      <a href="/spypoint-flex-m2"><img src="/img/1.jpg"></a>
      <div itemprop="brand" itemscope itemtype="https://schema.org/Brand"><span itemprop="name">SpyPoint</span></div>
      <span itemprop="name">SPYPOINT Wildkamera Flex-M2</span>
      <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">129,90 € *
        <meta itemprop="price" content="129.90"><link itemprop="availability" href="https://schema.org/OutOfStock">
      </div></div>"""
    item = shops.parse_microdata_list(html, "jagdwelt24", "https://www.jagdwelt24.de")[0]
    assert (item.title, item.price, item.available, item.url) == \
        ("SPYPOINT Wildkamera Flex-M2", 129.9, False, "https://www.jagdwelt24.de/spypoint-flex-m2")


def test_woo_store_api_minor_units():
    data = [{"id": 7, "name": "Walther Magazin &#8211; GSP", "permalink": "https://x/p/7",
             "prices": {"price": "8500", "regular_price": "9900", "currency_minor_unit": 2},
             "images": [{"src": "https://x/i.jpg"}], "is_in_stock": False, "on_sale": True}]
    item = shops.parse_woo_store_api(data, "shootingequipment")[0]
    assert (item.title, item.price, item.available) == ("Walther Magazin – GSP", 85.0, False)
    assert "statt 99,00" in item.price_text


def test_grube_app_state():
    elements = [{"type": "product", "data": {"productId": "776633", "name": "Fjällräven Jacke",
                 "url": "/p/fjaellraeven-jacke/776633/?q=jacke#x", "images": [{"src": "https://cdn/x.jpg"}],
                 "price": {"current": "649,00 ", "old": "0,00 ", "priceForSchemaOrgOffer": "649.0"}}}]
    html = "<script>window.__initialAppState = { modules: { elementsList: " + json.dumps(elements) + ", x: 1 }}</script>"
    item = shops.parse_grube(html, "grube", "https://www.grube.de")[0]
    assert (item.ext_id, item.price, item.url) == ("776633", 649.0, "https://www.grube.de/p/fjaellraeven-jacke/776633/")


def test_revolutionrace_title_and_price():
    html = """<a data-testid="ProductCard-link" href="/herren/hiball?Color=1"
       aria-label="Hiball Softshell Jacket Herren 129, €"><span class="standard-price__price">129&nbsp;€</span></a>
       <a data-testid="ProductCard-link" href="/damen/cyclone"
       aria-label="Cyclone Jacket Damen Originalpreis: 199, €, Rabattierter Preis: 139, €">
       <span class="campaign-price__listed-price">199 €</span><span class="campaign-price__selling-price">139 €</span></a>"""
    a, b = shops.parse_revolutionrace(html, "revolutionrace", "https://www.revolutionrace.de")
    assert (a.title, a.price) == ("Hiball Softshell Jacket Herren", 129)
    assert (b.title, b.price) == ("Cyclone Jacket Damen", 139) and "statt 199" in b.price_text


def test_empty_marker(monkeypatch):
    class R:
        status_code = 200
        text = '<body class="p-catalog-all-products"></body>'
    monkeypatch.setattr(shops, "get", lambda *a, **k: R())
    assert shops.make_search("doublealpha")({"query": "x"}, None) == []


def test_cards_zooplus_preset_and_sponsored_skip():
    html = """<div data-zta="product-card" data-variant-id="19180">
      <a data-zta="product-info" href="/shop/hunde/retrieverleine/10917?activeVariant=19180">
        <span data-zta="product-link">HUNTER Retriever-Führleine</span><p data-zta="variant-desc">260 cm</p></a>
      <span data-zta="reducedPriceRefPriceAmount">45,99 €</span><span data-zta="reducedPriceAmount">23,99 €</span></div>
      <div data-zta="product-card" data-variant-id="1"><a data-zta="product-info" href="https://ads.example/r?x=1">
        <span data-zta="product-link">Gesponsert</span></a><span class="z-product-price__amount">1,00 €</span></div>"""
    items = shops.parse_cards(html, "zooplus", "https://www.zooplus.de", shops.CARD_PRESETS["zooplus"])
    assert len(items) == 1
    i = items[0]
    assert (i.ext_id, i.title, i.price) == ("19180", "HUNTER Retriever-Führleine 260 cm", 23.99)
    assert i.url == "https://www.zooplus.de/shop/hunde/retrieverleine/10917" and "statt 45,99" in i.price_text


def test_cards_stock_text():
    cfg = shops.SHOPS["mindfactory"]["cards"]
    html = """<div class="pcontent"><a class="p-complete-link" href="https://www.mindfactory.de/p/1"></a>
      <div class="pname">Samsung 990 PRO</div><div class="pshipping">Nicht lieferbar</div>
      <div class="pprice">€ 208,60*</div></div>"""
    i = shops.parse_cards(html, "mindfactory", "https://www.mindfactory.de", cfg)[0]
    assert (i.title, i.price, i.available) == ("Samsung 990 PRO", 208.6, False)


def test_shopify_suggest():
    data = {"resources": {"results": {"products": [
        {"id": 5, "title": "GHOST Hybrid Holster", "url": "/products/ghost-hybrid?_pos=1", "price": "46.99",
         "compare_at_price_max": "59.90", "available": False, "image": "https://cdn/x.jpg"}]}}}
    i = shops.parse_shopify_suggest(data, "dynamicshooting", "https://www.dynamic-shooting.at")[0]
    assert (i.ext_id, i.price, i.available) == ("5", 46.99, False)
    assert i.url == "https://www.dynamic-shooting.at/products/ghost-hybrid" and "statt 59,90" in i.price_text


def test_fuzzy_shop_requires_all_words(monkeypatch):
    class R:
        status_code = 200
        text = """<main class="product-list-container">
          <article><a href="/p/x1/1"><h3>Lenovo ThinkPad X1</h3><span class="font-semibold">564,51 €</span></a></article>
          <article><a href="/p/xbox/2"><h3>Microsoft Xbox</h3><span class="font-semibold">400 €</span></a></article></main>"""
    monkeypatch.setattr(shops, "get", lambda *a, **k: R())
    items = shops.make_search("refurbed")({"query": "thinkpad x1"}, None)
    assert [i.title for i in items] == ["Lenovo ThinkPad X1"]


def test_grube_parser_on_bergzeit_prices_with_euro_sign():
    elements = [{"type": "product", "data": {"productId": "5057367", "name": "Trail 30 Rucksack", "url": "/p/trail-30/5057367/",
                 "price": {"current": "119,20 €", "old": "154,95 €", "priceForSchemaOrgOffer": "119.2"}}}]
    html = "<script>x = { elementsList: " + json.dumps(elements) + " }</script>"
    i = shops.parse_grube(html, "bergzeit", "https://www.bergzeit.de")[0]
    assert (i.price, i.price_text) == (119.2, "119,20 € (statt 154,95 €)")


def test_mediamarkt_strike_price_and_marketplace():
    html = """<div data-test="mms-product-card">
      <a href="/de/product/_garmin-fenix-8-47-mm-2948141.html"><p data-test="product-title">GARMIN fenix 8 47 mm</p></a>
      <div data-test="mms-price"><span data-test="mms-strike-price-label">UVP</span>
        <span aria-hidden="true">799,99&nbsp;€</span><span>799,99€</span>
        <span aria-hidden="true">669,99&nbsp;€</span><span>669,99€</span></div>
      <div data-test="product-delivery">Lieferung nach Hause 09.10.2026</div></div>
    <div data-test="mms-product-card">
      <a href="/de/product/_garmin-fenix-8-gold-157846604.html"><p data-test="product-title">GARMIN fenix 8 Gold</p></a>
      <div data-test="mms-price"><span aria-hidden="true">919,13&nbsp;€</span></div>
      <a data-test="mms-third-party-provider-link" href="/de/marketplace/x">Händler</a></div>"""
    a, b = shops.parse_mediamarkt(html, "mediamarkt", "https://www.mediamarkt.de")
    assert (a.ext_id, a.price, a.available, a.condition) == ("2948141", 669.99, True, "")
    assert "statt 799,99" in a.price_text
    assert (b.ext_id, b.price, b.condition) == ("157846604", 919.13, "Marktplatz")
