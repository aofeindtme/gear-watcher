"""Hintergrund-Abfrage: arbeitet fällige Suchen ab und benachrichtigt per ntfy."""
import logging
import threading
import time

import db
import notify
from sources import SOURCES, http_session

log = logging.getLogger("gear-watcher")

TICK_SECONDS = 30
# Pause zwischen zwei Seitenabrufen - verteilt die Last, statt eine Website
# mit allen Suchen gleichzeitig abzufragen.
REQUEST_PAUSE_SECONDS = 3
MIN_INTERVAL_MIN = 15
# Mehr neue Treffer als das in einem Lauf -> eine Sammelnachricht statt Flut.
MAX_SINGLE_NOTIFICATIONS = 5

wake_event = threading.Event()
_run_lock = threading.Lock()


def split_words(text: str | None) -> list[str]:
    return [w.strip().lower() for w in (text or "").split(",") if w.strip()]


def apply_filters(watch: dict, listings: list, site_filters: set) -> list:
    """Generische Nachfilterung für alles, was die Quelle nicht selbst kann
    (und als Sicherheitsnetz für Preisfilter, die Seiten teils lax auslegen)."""
    excludes = split_words(watch.get("exclude_words"))
    min_p, max_p = watch.get("min_price"), watch.get("max_price")
    out = []
    for item in listings:
        title = item.title.lower()
        if any(w in title for w in excludes):
            continue
        # Auktionen: aktuelles Gebot ist kein Endpreis -> Preisfilter nur, wenn
        # die Seite ihn selbst anwendet (dort zählt dann ihre Logik).
        if item.price is not None and not (item.is_auction and "price" in site_filters):
            if min_p is not None and item.price < min_p:
                continue
            if max_p is not None and item.price > max_p:
                continue
        out.append(item)
    return out


def _fmt_price(p) -> str:
    if p is None:
        return "Preis ?"
    return f"{p:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".").replace(",00 €", " €")


def run_watch(watch, session=None) -> dict:
    """Führt eine Suche über alle ihre Quellen aus. Rückgabe: Zusammenfassung."""
    watch = dict(watch)
    session = session or http_session()
    user_settings = db.get_user_settings(watch["user_id"])
    states = db.get_site_states(watch["id"])
    started = db.now()
    summary = {"new": 0, "price_drop": 0, "errors": 0}
    events: list[tuple[str, object, dict | None]] = []

    for site in [s for s in (watch["sites"] or "").split(",") if s]:
        src = SOURCES.get(site)
        if not src:
            continue
        baseline = site not in states
        try:
            raw = src["search"](watch, session)
            items = apply_filters(watch, raw, src["filters"])
        except Exception as e:  # eine kaputte Quelle darf die anderen nicht blockieren
            summary["errors"] += 1
            db.save_site_state(watch["id"], site, None, str(e))
            db.log("ERROR", f"{src['label']}: {e}", watch["user_id"], watch["id"])
            continue
        finally:
            time.sleep(REQUEST_PAUSE_SECONDS)

        for item in items:
            event, old = db.upsert_listing(watch["id"], item, baseline)
            if event == "price_drop" and not watch["notify_price_drop"]:
                continue
            if event != "unchanged":
                summary[event] += 1
                events.append((event, item, old))
        db.save_site_state(watch["id"], site, len(items), None)
        if baseline:
            db.log("INFO", f"{src['label']}: Erstabruf, {len(items)} bestehende Treffer still übernommen",
                   watch["user_id"], watch["id"])

    db.mark_watch_run(watch["id"], started)
    _notify(watch, user_settings, events)
    if summary["new"] or summary["price_drop"]:
        db.log("INFO", f"{summary['new']} neu, {summary['price_drop']} günstiger", watch["user_id"], watch["id"])
    return summary


def _notify(watch: dict, settings: dict, events: list):
    if not events:
        return
    if not settings.get("ntfy_topic"):
        db.log("WARNING", "Treffer gefunden, aber kein ntfy-Topic eingestellt", watch["user_id"], watch["id"])
        return

    if len(events) > MAX_SINGLE_NOTIFICATIONS:
        lines = [f"• {_fmt_price(i.price)} – {i.title} ({SOURCES[i.site]['label']})" for _, i, _ in events[:15]]
        if len(events) > 15:
            lines.append(f"… und {len(events) - 15} weitere")
        ok, err = notify.send(settings, f"{watch['name']}: {len(events)} Treffer", "\n".join(lines))
    else:
        ok, err = True, None
        for event, item, old in events:
            label = SOURCES[item.site]["label"]
            if event == "price_drop":
                title = f"↓ {_fmt_price(item.price)} – {item.title}"
                msg = f"Vorher {_fmt_price(old['price'])} · {label} · Suche „{watch['name']}“"
                tags = "chart_with_downwards_trend"
            else:
                title = f"{_fmt_price(item.price)} – {item.title}"
                bits = [label, item.location, item.condition, "Auktion" if item.is_auction else ""]
                msg = " · ".join(b for b in bits if b) + f"\nSuche „{watch['name']}“"
                tags = "mag"
            ok_one, err_one = notify.send(settings, title, msg, click=item.url, image=item.image, tags=tags)
            ok, err = ok and ok_one, err or err_one
    if not ok:
        db.log("ERROR", f"ntfy-Versand fehlgeschlagen: {err}", watch["user_id"], watch["id"])


def tick():
    with _run_lock:
        session = http_session()
        for watch in db.due_watches():
            try:
                run_watch(watch, session)
            except Exception as e:  # ein kaputter Lauf darf den Thread nicht beenden
                log.exception("Suche %s fehlgeschlagen", watch["id"])
                db.log("ERROR", f"Unerwarteter Fehler: {e}", watch["user_id"], watch["id"])


def loop():
    while True:
        try:
            tick()
        except Exception:
            log.exception("Watcher-Tick fehlgeschlagen")
        wake_event.wait(TICK_SECONDS)
        wake_event.clear()


def start():
    threading.Thread(target=loop, name="watcher", daemon=True).start()


def run_now(watch_id: int, user_id: int) -> dict | None:
    watch = db.get_watch(watch_id, user_id)
    if not watch:
        return None
    with _run_lock:
        return run_watch(watch)
