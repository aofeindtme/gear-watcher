from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sources import mail, mydealz

FEED = """<rss xmlns:pepper="http://www.pepper.com/rss" xmlns:media="http://search.yahoo.com/mrss/" version="2.0"><channel>
<item><category><![CDATA[Elektronik]]></category><pepper:merchant name="Amazon" price="199,90€"/>
<media:thumbnail url="https://static/x.jpg"/><title><![CDATA[Samsung 990 PRO 2TB SSD]]></title>
<link>https://www.mydealz.de/deals/samsung-990-pro-1</link><pubDate>Wed, 07 Oct 2026 08:16:57 +0200</pubDate>
<guid>https://www.mydealz.de/deals/samsung-990-pro-1</guid></item>
<item><title><![CDATA[Payback Coupons]]></title><link>https://www.mydealz.de/deals/payback-2</link></item>
</channel></rss>"""


def test_mydealz_feed_and_word_filter(monkeypatch):
    items = mydealz.parse_feed(FEED)
    assert (items[0].title, items[0].price, items[0].location, items[0].posted) == \
        ("Samsung 990 PRO 2TB SSD", 199.9, "Amazon", "07.10. 08:16")
    assert items[1].price is None

    class R:
        text = FEED
    monkeypatch.setattr(mydealz, "get", lambda *a, **k: R())
    found = mydealz.search({"query": "990 pro"}, None)
    assert [i.title for i in found] == ["Samsung 990 PRO 2TB SSD"]   # zwei Feeds, Duplikat entfernt


def test_mail_price_alert():
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "=?utf-8?q?Preiswecker=3A_Swarovski_Z8i_2-16x50_f=C3=BCr_1.899_=E2=82=AC?="
    msg["From"] = '"idealo Preiswecker" <preiswecker@idealo.de>'
    msg["Message-ID"] = "<abc@idealo>"
    msg["Date"] = "Wed, 07 Oct 2026 09:00:00 +0200"
    msg.attach(MIMEText("Ihr Wunschpreis wurde erreicht.", "plain", "utf-8"))
    msg.attach(MIMEText('<p>Jetzt <a href="https://www.idealo.de/preisvergleich/OffersOfProduct/123.html">ansehen</a></p>',
                        "html", "utf-8"))
    item = mail.parse_message(msg.as_bytes())
    assert item.title == "Preiswecker: Swarovski Z8i 2-16x50 für 1.899 €"
    assert (item.price, item.ext_id, item.location) == (1899.0, "<abc@idealo>", "idealo Preiswecker")
    assert item.url == "https://www.idealo.de/preisvergleich/OffersOfProduct/123.html"
    assert "Wunschpreis" in item.extra["text"]
