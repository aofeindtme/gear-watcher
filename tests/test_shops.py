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
