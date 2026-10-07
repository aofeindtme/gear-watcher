"""mydealz (Deals aus allen Bereichen) über die öffentlichen RSS-Feeds.

Eine Suche über mydealz gibt es nur per JavaScript, die Feeds sind aber offen:
/rss/alles = alle neuen Deals (~30 je gut eine Stunde), /rss/deals = heiße Deals
(~30 über mehrere Stunden). Beide zusammen decken ein Intervall von 60 Minuten ab.
Gefiltert wird hier lokal: alle Suchwörter müssen im Deal-Titel stehen.
"""
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from . import Listing, SourceError, get, parse_price

FEEDS = ("https://www.mydealz.de/rss/alles", "https://www.mydealz.de/rss/deals")
NS = {"pepper": "http://www.pepper.com/rss", "media": "http://search.yahoo.com/mrss/"}


def parse_feed(xml_text: str) -> list[Listing]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise SourceError(f"mydealz: Feed nicht lesbar ({e})") from e
    results = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        title = (it.findtext("title") or "").strip()
        if not link or not title:
            continue
        merchant = it.find("pepper:merchant", NS)
        price_text = merchant.get("price", "") if merchant is not None else ""
        thumb = it.find("media:thumbnail", NS)
        try:
            posted = parsedate_to_datetime(it.findtext("pubDate") or "").strftime("%d.%m. %H:%M")
        except (TypeError, ValueError):
            posted = ""
        results.append(Listing(
            site="mydealz",
            ext_id=(it.findtext("guid") or link).strip(),
            title=title,
            url=link,
            price=parse_price(price_text),
            price_text=price_text,
            image=thumb.get("url", "") if thumb is not None else "",
            location=merchant.get("name", "") if merchant is not None else "",
            posted=posted,
            condition=it.findtext("category") or "",
        ))
    return results


def search(watch: dict, session) -> list[Listing]:
    words = [w.lower() for w in watch["query"].split()]
    seen, results = set(), []
    for url in FEEDS:
        for item in parse_feed(get(session, url).text):
            if item.ext_id not in seen and all(w in item.title.lower() for w in words):
                seen.add(item.ext_id)
                results.append(item)
    return results
