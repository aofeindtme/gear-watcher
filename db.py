"""SQLite-Persistenz.

Single-User-Betrieb, aber mehrbenutzerfähig angelegt: alles Nutzerbezogene
(Suchen, ntfy-Einstellungen, Protokoll) hängt an einer user_id. Für weitere
Nutzer fehlt später nur eine Verwaltungsseite (create_user existiert schon).
"""
import os
import secrets
import sqlite3
import datetime as dt
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "/data/gear_watcher.db")

USER_SETTING_DEFAULTS = {
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": "",
    "ntfy_token": "",
    "ntfy_user": "",
    "ntfy_password": "",
    "ebay_client_id": "",
    "ebay_client_secret": "",
}

WATCH_FIELDS = [
    "name", "query", "sites", "min_price", "max_price", "zip_code", "radius_km",
    "condition", "exclude_words", "search_description", "interval_min",
    "notify_price_drop", "active", "urls", "require_all_words",
]

# Spalten, die nach der ersten Version dazukamen: (Tabelle, Spalte, Definition)
MIGRATIONS = [
    ("watches", "urls", "TEXT"),
    ("listings", "available", "INTEGER"),
    ("watches", "require_all_words", "INTEGER NOT NULL DEFAULT 1"),
]


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with conn() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            key TEXT NOT NULL, value TEXT,
            PRIMARY KEY (user_id, key)
        );
        CREATE TABLE IF NOT EXISTS watches (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            query TEXT NOT NULL,
            sites TEXT NOT NULL,              -- kommagetrennt, Schlüssel aus sources.SOURCES
            min_price REAL,
            max_price REAL,
            zip_code TEXT,
            radius_km INTEGER,
            condition TEXT,                   -- '', 'new', 'used'
            exclude_words TEXT,               -- kommagetrennt
            search_description INTEGER NOT NULL DEFAULT 0,
            interval_min INTEGER NOT NULL DEFAULT 60,
            notify_price_drop INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_run_at TEXT
        );
        -- Stand je Suche+Quelle. Fehlt die Zeile, ist der nächste Lauf ein
        -- "Baseline"-Lauf: Treffer werden still als gesehen gespeichert statt
        -- 25 Push-Nachrichten auf einmal zu verschicken.
        CREATE TABLE IF NOT EXISTS watch_site_state (
            watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
            site TEXT NOT NULL,
            last_run_at TEXT,
            last_count INTEGER,
            last_error TEXT,
            PRIMARY KEY (watch_id, site)
        );
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY,
            watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
            site TEXT NOT NULL,
            ext_id TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            image TEXT,
            price REAL,
            price_text TEXT,
            lowest_price REAL,
            location TEXT,
            posted TEXT,
            condition TEXT,
            is_auction INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new',   -- new | seen | star | hidden
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            UNIQUE (watch_id, site, ext_id)
        );
        CREATE INDEX IF NOT EXISTS idx_listings_watch ON listings(watch_id, first_seen);
        CREATE TABLE IF NOT EXISTS price_history (
            listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
            ts TEXT NOT NULL,
            price REAL
        );
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY,
            ts TEXT NOT NULL,
            user_id INTEGER,
            watch_id INTEGER,
            level TEXT NOT NULL,
            message TEXT NOT NULL
        );
        """)
        for table, column, definition in MIGRATIONS:
            existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        if not c.execute("SELECT 1 FROM app_settings WHERE key='secret_key'").fetchone():
            c.execute("INSERT INTO app_settings VALUES ('secret_key', ?)", (secrets.token_hex(32),))


def get_app_setting(key: str) -> str | None:
    with conn() as c:
        row = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


# ----------------------------------------------------------------------
# Nutzer
# ----------------------------------------------------------------------

def count_users() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(username: str, password_hash: str, is_admin: bool = False) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?,?,?,?)",
            (username, password_hash, int(is_admin), now()),
        )
        return int(cur.lastrowid or 0)


def get_user_by_name(username: str):
    with conn() as c:
        return c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def get_user_by_id(user_id: int):
    with conn() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def set_password(user_id: int, password_hash: str):
    with conn() as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))


def get_user_settings(user_id: int) -> dict:
    with conn() as c:
        rows = c.execute("SELECT key, value FROM user_settings WHERE user_id=?", (user_id,)).fetchall()
    return {**USER_SETTING_DEFAULTS, **{r["key"]: r["value"] for r in rows}}


def save_user_settings(user_id: int, values: dict):
    with conn() as c:
        for k, v in values.items():
            if k in USER_SETTING_DEFAULTS:
                c.execute(
                    "INSERT INTO user_settings VALUES (?,?,?) "
                    "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
                    (user_id, k, v),
                )


# ----------------------------------------------------------------------
# Suchen (Watches)
# ----------------------------------------------------------------------

def list_watches(user_id: int):
    with conn() as c:
        return c.execute("""
            SELECT w.*,
                   (SELECT COUNT(*) FROM listings l WHERE l.watch_id=w.id AND l.status='new') AS new_count,
                   (SELECT COUNT(*) FROM listings l WHERE l.watch_id=w.id AND l.status!='hidden') AS total_count,
                   (SELECT MIN(l.price) FROM listings l WHERE l.watch_id=w.id AND l.status!='hidden'
                        AND l.price > 0 AND l.is_auction = 0
                        AND l.last_seen >= w.last_run_at) AS cheapest_now
            FROM watches w WHERE w.user_id=? ORDER BY w.name COLLATE NOCASE
        """, (user_id,)).fetchall()


def get_watch(watch_id: int, user_id: int | None = None):
    with conn() as c:
        if user_id is None:
            return c.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
        return c.execute("SELECT * FROM watches WHERE id=? AND user_id=?", (watch_id, user_id)).fetchone()


def save_watch(user_id: int, values: dict, watch_id: int | None = None) -> int:
    cols = [k for k in WATCH_FIELDS if k in values]
    with conn() as c:
        if watch_id is None:
            cur = c.execute(
                f"INSERT INTO watches (user_id, created_at, {', '.join(cols)}) "
                f"VALUES (?, ?, {', '.join('?' for _ in cols)})",
                (user_id, now(), *[values[k] for k in cols]),
            )
            return int(cur.lastrowid or 0)
        c.execute(
            f"UPDATE watches SET {', '.join(f'{k}=?' for k in cols)} WHERE id=? AND user_id=?",
            (*[values[k] for k in cols], watch_id, user_id),
        )
        # Geänderte Filter liefern andere Treffer -> neu "baselinen", sonst
        # käme für jede jetzt zusätzlich passende Altanzeige eine Push-Nachricht.
        c.execute("DELETE FROM watch_site_state WHERE watch_id=?", (watch_id,))
        c.execute("UPDATE watches SET last_run_at=NULL WHERE id=?", (watch_id,))
        return watch_id


def delete_watch(watch_id: int, user_id: int):
    with conn() as c:
        c.execute("DELETE FROM watches WHERE id=? AND user_id=?", (watch_id, user_id))


def set_watch_active(watch_id: int, user_id: int, active: bool):
    with conn() as c:
        c.execute("UPDATE watches SET active=? WHERE id=? AND user_id=?", (int(active), watch_id, user_id))


def due_watches():
    """Aktive Suchen, deren Intervall abgelaufen ist (oder die nie liefen)."""
    with conn() as c:
        rows = c.execute("SELECT * FROM watches WHERE active=1").fetchall()
    t = dt.datetime.now(dt.timezone.utc)
    due = []
    for w in rows:
        if not w["last_run_at"]:
            due.append(w)
            continue
        last = dt.datetime.fromisoformat(w["last_run_at"])
        if t - last >= dt.timedelta(minutes=w["interval_min"]):
            due.append(w)
    return due


def mark_watch_run(watch_id: int, started_at: str):
    # Startzeit statt Endzeit: list_watches() erkennt "aktuell noch online"
    # an last_seen >= last_run_at.
    with conn() as c:
        c.execute("UPDATE watches SET last_run_at=? WHERE id=?", (started_at, watch_id))


def get_site_states(watch_id: int) -> dict:
    with conn() as c:
        rows = c.execute("SELECT * FROM watch_site_state WHERE watch_id=?", (watch_id,)).fetchall()
    return {r["site"]: r for r in rows}


def save_site_state(watch_id: int, site: str, count: int | None, error: str | None):
    with conn() as c:
        c.execute(
            "INSERT INTO watch_site_state VALUES (?,?,?,?,?) ON CONFLICT(watch_id, site) DO UPDATE SET "
            "last_run_at=excluded.last_run_at, last_count=excluded.last_count, last_error=excluded.last_error",
            (watch_id, site, now(), count, error),
        )


# ----------------------------------------------------------------------
# Treffer (Listings)
# ----------------------------------------------------------------------

def upsert_listing(watch_id: int, listing, baseline: bool) -> tuple[str, dict | None]:
    """Speichert einen Treffer. Rückgabe: (ereignis, alte_zeile)
    ereignis = 'new' | 'price_drop' | 'back_in_stock' | 'unchanged'."""
    available = None if listing.available is None else int(listing.available)
    ts = now()
    with conn() as c:
        old = c.execute(
            "SELECT * FROM listings WHERE watch_id=? AND site=? AND ext_id=?",
            (watch_id, listing.site, listing.ext_id),
        ).fetchone()
        if old is None:
            cur = c.execute("""
                INSERT INTO listings (watch_id, site, ext_id, title, url, image, price, price_text,
                    lowest_price, location, posted, condition, is_auction, available, status, first_seen, last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (watch_id, listing.site, listing.ext_id, listing.title, listing.url, listing.image,
                  listing.price, listing.price_text, listing.price, listing.location, listing.posted,
                  listing.condition, int(listing.is_auction), available, "seen" if baseline else "new", ts, ts))
            c.execute("INSERT INTO price_history VALUES (?,?,?)", (cur.lastrowid, ts, listing.price))
            return ("unchanged" if baseline else "new"), None

        event = "unchanged"
        lowest = old["lowest_price"]
        if listing.price is not None and old["price"] is not None and listing.price != old["price"]:
            c.execute("INSERT INTO price_history VALUES (?,?,?)", (old["id"], ts, listing.price))
            # Bei Auktionen steigt der Preis mit jedem Gebot - nur echte
            # Preissenkungen (Sofortkauf/Festpreis) zählen.
            if listing.price < old["price"] and not listing.is_auction:
                event = "price_drop"
        if available == 1 and old["available"] == 0:
            event = "back_in_stock"
        if listing.price is not None and (lowest is None or listing.price < lowest):
            lowest = listing.price
        c.execute("""
            UPDATE listings SET title=?, url=?, image=?, price=?, price_text=?, lowest_price=?,
                location=?, posted=?, condition=?, is_auction=?, available=?, last_seen=?
            WHERE id=?
        """, (listing.title, listing.url, listing.image, listing.price, listing.price_text, lowest,
              listing.location or old["location"], listing.posted or old["posted"], listing.condition,
              int(listing.is_auction), available if available is not None else old["available"], ts, old["id"]))
        if event in ("price_drop", "back_in_stock") and old["status"] == "seen":
            c.execute("UPDATE listings SET status='new' WHERE id=?", (old["id"],))
        return event, dict(old)


# Sortierungen der Trefferseite: Schlüssel -> (Label, ORDER BY). Preise ohne Wert immer ans Ende.
LISTING_SORTS = {
    "newest": ("Neueste zuerst", "l.first_seen DESC, l.id DESC"),
    # Auktionsgebote sind keine Endpreise -> nach den Festpreisen
    "price_asc": ("Preis aufsteigend", "l.price IS NULL, l.is_auction, l.price ASC, l.first_seen DESC"),
    "price_desc": ("Preis absteigend", "l.price IS NULL, l.is_auction, l.price DESC, l.first_seen DESC"),
    "drop": ("Größte Preissenkung", "(l.lowest_price IS NULL OR first_price IS NULL), "
                                    "(first_price - l.price) DESC, l.first_seen DESC"),
    "title": ("Titel A–Z", "l.title COLLATE NOCASE ASC"),
    "site": ("Quelle", "l.site ASC, l.first_seen DESC"),
    "updated": ("Zuletzt gesehen", "l.last_seen DESC, l.id DESC"),
}


def list_listings(user_id: int, watch_id: int | None = None, status: str | None = None,
                  site: str | None = None, limit: int = 300, sort: str = "newest"):
    sql = """
        SELECT l.*, w.name AS watch_name,
               (SELECT h.price FROM price_history h WHERE h.listing_id = l.id ORDER BY h.ts LIMIT 1) AS first_price
        FROM listings l
        JOIN watches w ON w.id = l.watch_id
        WHERE w.user_id = ?
    """
    args: list = [user_id]
    if watch_id:
        sql += " AND l.watch_id = ?"
        args.append(watch_id)
    if status == "active":
        sql += " AND l.status != 'hidden'"
    elif status:
        sql += " AND l.status = ?"
        args.append(status)
    if site:
        sql += " AND l.site = ?"
        args.append(site)
    sql += f" ORDER BY {LISTING_SORTS.get(sort, LISTING_SORTS['newest'])[1]} LIMIT ?"
    args.append(limit)
    with conn() as c:
        return c.execute(sql, args).fetchall()


def listings_for_watch(watch_id: int):
    with conn() as c:
        return c.execute("SELECT * FROM listings WHERE watch_id=?", (watch_id,)).fetchall()


def delete_listings(ids: list[int]):
    if not ids:
        return
    with conn() as c:
        c.executemany("DELETE FROM listings WHERE id=?", [(i,) for i in ids])


def set_listing_status(listing_id: int, user_id: int, status: str):
    with conn() as c:
        c.execute("""
            UPDATE listings SET status=? WHERE id=?
            AND watch_id IN (SELECT id FROM watches WHERE user_id=?)
        """, (status, listing_id, user_id))


def mark_all_seen(user_id: int, watch_id: int | None = None):
    sql = "UPDATE listings SET status='seen' WHERE status='new' AND watch_id IN (SELECT id FROM watches WHERE user_id=?"
    args: list = [user_id]
    if watch_id:
        sql += " AND id=?"
        args.append(watch_id)
    with conn() as c:
        c.execute(sql + ")", args)


def price_history(listing_id: int):
    with conn() as c:
        return c.execute("SELECT ts, price FROM price_history WHERE listing_id=? ORDER BY ts",
                         (listing_id,)).fetchall()


# ----------------------------------------------------------------------
# Protokoll
# ----------------------------------------------------------------------

def log(level: str, message: str, user_id: int | None = None, watch_id: int | None = None):
    with conn() as c:
        c.execute("INSERT INTO log (ts, user_id, watch_id, level, message) VALUES (?,?,?,?,?)",
                  (now(), user_id, watch_id, level, message))
        # Protokoll begrenzen
        c.execute("DELETE FROM log WHERE id <= (SELECT MAX(id) - 5000 FROM log)")


def list_log(user_id: int, limit: int = 300):
    with conn() as c:
        return c.execute("""
            SELECT g.*, w.name AS watch_name FROM log g LEFT JOIN watches w ON w.id = g.watch_id
            WHERE g.user_id = ? OR g.user_id IS NULL ORDER BY g.id DESC LIMIT ?
        """, (user_id, limit)).fetchall()
