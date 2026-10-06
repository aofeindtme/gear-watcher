import os
import logging
import datetime as dt
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, request, redirect, url_for, render_template, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash

import db
import notify
import watcher
from sources import SOURCES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = Flask(__name__)

db.init_db()
app.secret_key = db.get_app_setting("secret_key")

# Standard ist reines HTTP im LAN. Hinter einem HTTPS-Reverse-Proxy
# SECURE_COOKIES=true setzen, dann geht das Session-Cookie nur über HTTPS.
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SECURE_COOKIES", "false").lower() == "true"
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 30


def _bootstrap_admin():
    """Legt beim allerersten Start den ersten (Admin-)Account aus der Umgebung an."""
    if db.count_users():
        return
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        raise RuntimeError("Erster Start: ADMIN_PASSWORD muss gesetzt sein (siehe .env.example)")
    db.create_user(username, generate_password_hash(password), is_admin=True)
    db.log("INFO", f"Erster Account '{username}' angelegt")


_bootstrap_admin()

if os.environ.get("WATCHER_DISABLED", "false").lower() != "true":
    watcher.start()


# ----------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------

def current_user():
    user_id = session.get("user_id")
    return db.get_user_by_id(user_id) if user_id else None


@app.context_processor
def inject_globals():
    return {"user": current_user(), "SOURCES": SOURCES, "fmt_price": watcher._fmt_price}


LOCAL_TZ = ZoneInfo(os.environ.get("TZ") or "Europe/Berlin")


@app.template_filter("localtime")
def localtime(ts: str | None) -> str:
    """DB speichert UTC-ISO-Zeitstempel - Anzeige in lokaler Zeit."""
    if not ts:
        return ""
    return dt.datetime.fromisoformat(ts).astimezone(LOCAL_TZ).strftime("%d.%m.%Y %H:%M")


def requires_login(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user = db.get_user_by_name(request.form.get("username", "").strip())
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            nxt = request.args.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
        error = "Benutzername oder Passwort falsch."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ----------------------------------------------------------------------
# Treffer
# ----------------------------------------------------------------------

@app.route("/")
@requires_login
def dashboard():
    user = current_user()
    watch_id = request.args.get("watch", type=int)
    status = request.args.get("status", "active")
    site = request.args.get("site") or None
    listings = db.list_listings(user["id"], watch_id, status if status != "all" else None, site)
    return render_template("dashboard.html", listings=listings, watches=db.list_watches(user["id"]),
                           f_watch=watch_id, f_status=status, f_site=site)


@app.route("/listing/<int:listing_id>/status", methods=["POST"])
@requires_login
def listing_status(listing_id):
    status = request.form.get("status")
    if status not in ("new", "seen", "star", "hidden"):
        abort(400)
    db.set_listing_status(listing_id, current_user()["id"], status)
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/listings/mark-seen", methods=["POST"])
@requires_login
def listings_mark_seen():
    db.mark_all_seen(current_user()["id"], request.form.get("watch", type=int))
    return redirect(request.referrer or url_for("dashboard"))


# ----------------------------------------------------------------------
# Suchen
# ----------------------------------------------------------------------

def _float_or_none(v):
    v = (v or "").strip().replace(",", ".")
    return float(v) if v else None


def _watch_from_form(form) -> tuple[dict, list[str]]:
    errors = []
    sites = [s for s in form.getlist("sites") if s in SOURCES]
    values = {
        "name": form.get("name", "").strip(),
        "query": form.get("query", "").strip(),
        "sites": ",".join(sites),
        "zip_code": form.get("zip_code", "").strip() or None,
        "radius_km": int(form["radius_km"]) if form.get("radius_km", "").strip().isdigit() else None,
        "condition": form.get("condition", ""),
        "exclude_words": form.get("exclude_words", "").strip(),
        "search_description": int(bool(form.get("search_description"))),
        "notify_price_drop": int(bool(form.get("notify_price_drop"))),
        "active": int(bool(form.get("active"))),
    }
    try:
        values["min_price"] = _float_or_none(form.get("min_price"))
        values["max_price"] = _float_or_none(form.get("max_price"))
    except ValueError:
        errors.append("Preis bitte als Zahl angeben.")
    try:
        values["interval_min"] = max(watcher.MIN_INTERVAL_MIN, int(form.get("interval_min") or 60))
    except ValueError:
        errors.append("Intervall bitte als ganze Zahl (Minuten) angeben.")
    if not values["query"]:
        errors.append("Suchbegriff fehlt.")
    if not values["name"]:
        values["name"] = values["query"]
    if not sites:
        errors.append("Mindestens eine Quelle auswählen.")
    if values["zip_code"] and not values["zip_code"].isdigit():
        errors.append("PLZ bitte nur als Ziffern.")
    if values["zip_code"] and not values["radius_km"]:
        errors.append("Zur PLZ bitte auch einen Umkreis angeben.")
    return values, errors


@app.route("/watches")
@requires_login
def watches_page():
    user = current_user()
    watches = db.list_watches(user["id"])
    states = {w["id"]: db.get_site_states(w["id"]) for w in watches}
    return render_template("watches.html", watches=watches, states=states)


@app.route("/watches/new", methods=["GET", "POST"])
@app.route("/watches/<int:watch_id>/edit", methods=["GET", "POST"])
@requires_login
def watch_form(watch_id=None):
    user = current_user()
    watch = db.get_watch(watch_id, user["id"]) if watch_id else None
    if watch_id and not watch:
        abort(404)
    errors = []
    if request.method == "POST":
        values, errors = _watch_from_form(request.form)
        if not errors:
            new_id = db.save_watch(user["id"], values, watch_id)
            flash("Suche gespeichert. Der erste Abruf übernimmt vorhandene Treffer still, "
                  "danach gibt es Push-Nachrichten nur für Neues.")
            watcher.wake_event.set()
            return redirect(url_for("watches_page") + f"#w{new_id}")
        watch = values
    return render_template("watch_form.html", watch=watch, watch_id=watch_id, errors=errors)


@app.route("/watches/<int:watch_id>/delete", methods=["POST"])
@requires_login
def watch_delete(watch_id):
    db.delete_watch(watch_id, current_user()["id"])
    flash("Suche gelöscht.")
    return redirect(url_for("watches_page"))


@app.route("/watches/<int:watch_id>/toggle", methods=["POST"])
@requires_login
def watch_toggle(watch_id):
    user = current_user()
    watch = db.get_watch(watch_id, user["id"])
    if watch:
        db.set_watch_active(watch_id, user["id"], not watch["active"])
    return redirect(url_for("watches_page") + f"#w{watch_id}")


@app.route("/watches/<int:watch_id>/run", methods=["POST"])
@requires_login
def watch_run(watch_id):
    summary = watcher.run_now(watch_id, current_user()["id"])
    if summary is None:
        abort(404)
    flash(f"Abgerufen: {summary['new']} neu, {summary['price_drop']} günstiger"
          + (f", {summary['errors']} Quelle(n) mit Fehler (siehe Protokoll)" if summary["errors"] else "") + ".")
    return redirect(url_for("watches_page") + f"#w{watch_id}")


# ----------------------------------------------------------------------
# Einstellungen / Protokoll
# ----------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
@requires_login
def settings_page():
    user = current_user()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "ntfy":
            db.save_user_settings(user["id"], {k: request.form.get(k, "").strip() for k in db.USER_SETTING_DEFAULTS})
            flash("ntfy-Einstellungen gespeichert.")
        elif action == "ntfy_test":
            ok, err = notify.send(db.get_user_settings(user["id"]), "Gear Watcher",
                                  "Testnachricht - ntfy funktioniert.", tags="white_check_mark")
            flash("Testnachricht verschickt." if ok else f"Testnachricht fehlgeschlagen: {err or 'kein Topic gesetzt'}")
        elif action == "password":
            if not check_password_hash(user["password_hash"], request.form.get("current", "")):
                flash("Aktuelles Passwort falsch.")
            elif len(request.form.get("new", "")) < 8:
                flash("Neues Passwort bitte mindestens 8 Zeichen.")
            else:
                db.set_password(user["id"], generate_password_hash(request.form["new"]))
                flash("Passwort geändert.")
        return redirect(url_for("settings_page"))
    return render_template("settings.html", settings=db.get_user_settings(user["id"]))


@app.route("/log")
@requires_login
def log_page():
    return render_template("log.html", entries=db.list_log(current_user()["id"]))
