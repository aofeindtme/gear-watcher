"""Preisalarme per E-Mail (idealo-Preiswecker, Geizhals-Preisalarm, ...).

idealo und Geizhals sperren automatische Abrufe, schicken aber selbst E-Mails, wenn
ein Wunschpreis erreicht ist. Diese Quelle liest ein eigens dafür angelegtes Postfach
per IMAP (nur lesend, markiert nichts) und macht aus jeder Mail einen Treffer.
Ob eine Mail zur Suche gehört, entscheiden die Suchwörter (Betreff + Text).
"""
import datetime as dt
import email
import imaplib
import re
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from . import Listing, SourceError, parse_price

DAYS_BACK = 30
MAX_MESSAGES = 200
LINK_HINTS = ("idealo", "geizhals", "mydealz")


def _header(msg, name: str) -> str:
    try:
        return str(make_header(decode_header(msg.get(name, ""))))
    except (UnicodeDecodeError, LookupError):
        return msg.get(name, "")


def _body(msg) -> tuple[str, str]:
    """(Text, erster passender Link) aus Text- oder HTML-Teil."""
    text, html = "", ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, AttributeError):
            continue
        if ctype == "text/plain" and not text:
            text = content
        elif ctype == "text/html" and not html:
            html = content
    link = ""
    if html:
        soup = BeautifulSoup(html, "html.parser")
        link = next((str(a["href"]) for a in soup.find_all("a", href=True)
                     if any(h in str(a["href"]).lower() for h in LINK_HINTS)), "")
        text = text or soup.get_text(" ", strip=True)
    if not link:
        m = re.search(r"https?://\S*(?:" + "|".join(LINK_HINTS) + r")\S*", text, re.I)
        link = m.group(0).rstrip(">)]\"'") if m else ""
    return " ".join(text.split()), link


def parse_message(raw: bytes) -> Listing | None:
    msg = email.message_from_bytes(raw)
    subject = " ".join(_header(msg, "Subject").split())
    if not subject:
        return None
    text, link = _body(msg)
    try:
        posted = parsedate_to_datetime(msg.get("Date", "")).strftime("%d.%m. %H:%M")
    except (TypeError, ValueError):
        posted = ""
    sender = _header(msg, "From")
    price = parse_price(subject) or parse_price(text[:2000])
    return Listing(
        site="mail",
        ext_id=(msg.get("Message-ID") or f"{subject}|{msg.get('Date', '')}").strip(),
        title=subject,
        url=link,
        price=price,
        price_text=f"{price:.2f} €".replace(".", ",") if price is not None else "",
        location=re.sub(r"\s*<[^>]+>", "", sender).strip('" ') or sender,
        posted=posted,
        extra={"text": text[:4000]},
    )


def search(watch: dict, session) -> list[Listing]:
    s = watch.get("_settings") or {}
    words = [w.lower() for w in (watch.get("query") or "").split()]
    try:
        box = imaplib.IMAP4_SSL(s["imap_host"], int(s.get("imap_port") or 993), timeout=30)
    except (OSError, ValueError) as e:
        raise SourceError(f"IMAP-Verbindung zu {s.get('imap_host')}: {e}") from e
    try:
        box.login(s["imap_user"], s["imap_password"])
        box.select(s.get("imap_folder") or "INBOX", readonly=True)
        since = (dt.date.today() - dt.timedelta(days=DAYS_BACK)).strftime("%d-%b-%Y")
        typ, data = box.search(None, "SINCE", since)
        ids = (data[0] or b"").split()[-MAX_MESSAGES:] if typ == "OK" else []
        results = []
        for mid in ids:
            typ, parts = box.fetch(mid, "(BODY.PEEK[])")  # PEEK: nicht als gelesen markieren
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None)
            item = parse_message(raw) if raw else None
            if item and all(w in f"{item.title} {item.extra.get('text', '')}".lower() for w in words):
                results.append(item)
        return results
    except imaplib.IMAP4.error as e:
        raise SourceError(f"IMAP: {e}") from e
    finally:
        try:
            box.logout()
        except Exception:
            pass
