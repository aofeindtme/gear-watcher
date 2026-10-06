# Gear Watcher

Beobachtet Kleinanzeigen-, Auktions- und Shop-Seiten nach Ausrüstung (Schießsport,
Jagd, ...), die man gerade sucht, und meldet **neue Angebote** und **Preissenkungen**
per [ntfy](https://ntfy.sh) aufs Handy. Ziel: Dinge möglichst günstig bekommen.

## Funktionsweise

- Unter **Suchen** legt man beliebig viele Suchen an: Suchbegriff, Quellen, Preis von/bis,
  PLZ + Umkreis, Zustand, Ausschlusswörter (z. B. `suche, defekt, airsoft`), Intervall.
- Ein Hintergrund-Thread arbeitet fällige Suchen ab (Standard: stündlich, Minimum
  15 Minuten), mit Pausen zwischen den Seitenabrufen.
- **Erster Abruf** einer Suche (und nach jeder Änderung ihrer Filter) übernimmt alle
  vorhandenen Treffer still als "gesehen" - Push-Nachrichten gibt es danach nur für
  wirklich Neues. Mehr als 5 neue Treffer auf einmal kommen als eine Sammelnachricht.
- **Preissenkung** eines bekannten Angebots (nicht bei Auktionsgeboten) und **wieder
  lieferbar** (Shops) lösen ebenfalls eine Nachricht aus; Tiefstpreis und Preisverlauf
  werden gespeichert.
- Unter **Treffer** alle Funde als Karten, filterbar nach Suche/Status/Quelle, mit
  Merken/Gesehen/Ausblenden.

## Quellen

| Quelle | Art | Filter direkt auf der Seite | Hinweis |
|---|---|---|---|
| eGun | Auktionen/Sofortkauf | Preis, PLZ/Umkreis, Zustand, Beschreibung | |
| Kleinanzeigen | Kleinanzeigen | Preis, PLZ/Umkreis | |
| Frankonia-Kleinanzeigen | Kleinanzeigen/Auktionen | Preis, PLZ/Umkreis | |
| eBay | Auktionen/Sofortkauf | Preis, Zustand | offizielle Browse-API, Zugangsdaten unter Einstellungen |
| Frankonia | Shop | – | |
| Pirscher Gear, Pirscher Shop, Hubertus Fieldsports | Shop (Shopware 6) | – | Pirscher Gear = pirschergear**.com** |
| Sportwaffen Triebel, Atlas Taktik | Shop (Shopware 5) | – | |
| Jagdwelt24, Shooting Solutions | Shop (JTL, schema.org) | – | |
| Grube | Shop (novomind, JSON im Seitenzustand) | – | |
| jagd.de (Askari) | Shop (OXID) | – | sortiert nach "neu" |
| Revolution Race | Shop (Nuxt) | – | |
| Shooting Equipment | Shop (WooCommerce Store-API, JSON) | – | |
| Double Alpha | Shop | – | |
| Shooters First Choice | Shop (modified eCommerce) | – | |
| **Shop-URLs** | Produkt- oder Kategorieseiten | – | siehe unten |

Ausschlusswörter und Preisgrenzen werden zusätzlich generisch nachgefiltert.

**Shop-URLs beobachten:** In einer Suche können zusätzlich (oder ausschließlich) URLs
eingetragen werden:
- **Produktseite** (beliebiger Shop mit schema.org-Daten, also fast alle): meldet
  Preissenkungen und "wieder lieferbar".
- **Kategorieseite** (Shopware-5/6-Shops, Frankonia): meldet neue Produkte; ein
  Suchbegriff wirkt dort als Titelfilter.
- Vor jedem Abruf wird die `robots.txt` geprüft. Einziger Weg für **Recon Company**,
  **Alljagd**, **Living Active** und **Volber** (Suche per robots.txt gesperrt) sowie
  **TACWRK** (Suche läuft nur per JavaScript über einen Drittanbieter).

**Bewusst nicht dabei:**
- **Brownells** - brownells.de ist eine geparkte Domain, der echte Shop
  (brownells-deutschland.de) steht hinter einer Cloudflare-Bot-Sperre. Die wird nicht umgangen.
- **Amazon** - blockt Scraper, die offizielle API setzt ein Partnerkonto mit Umsätzen
  voraus. Option für später: Keepa-API (kostenpflichtig).
- **Waffen Schumacher** - waffenschumacher.com hat keinen Online-Shop (WordPress-Seite).
- **IPSC Store** (ipscstore.com) - Cloudflare-Bot-Sperre.
- **Sportshooter** (sportshooter.de) - Produkte werden nur per JavaScript geladen, keine
  Sitemap; weder Suche noch Kategorie-/Produktseiten sind ohne Browser auslesbar.

Neuen Shop hinzufügen: läuft er auf einem schon unterstützten System (Shopware 5/6, JTL,
WooCommerce, ...), reicht ein Eintrag in `SHOPS` in `sources/shops.py`. Sonst Parser dort
ergänzen oder eigenes Modul in `sources/` mit `search(watch, session) -> list[Listing]`
anlegen und in `sources/__init__.py` in `SOURCES` eintragen. Vorher `robots.txt` der Seite
prüfen und nur erlaubte Pfade abfragen.

## Faire Nutzung

Die Abfragen sind bewusst sparsam: ein Seitenabruf pro Suche und Quelle pro Intervall,
nur Seite 1 nach "neueste zuerst" sortiert, Pausen zwischen Abrufen, nur von der
jeweiligen `robots.txt` erlaubte Pfade. Bitte Intervalle nicht unnötig kurz stellen.

## Setup (Portainer-Stack auf der NAS)

1. Portainer → Stacks → Add stack → **Repository**, dieses Repo, Compose-Pfad
   `docker-compose.yml`.
2. Unter "Environment variables" mindestens `ADMIN_PASSWORD` setzen (siehe `.env.example`),
   für Snapshots/Backups `DATA_PATH=/volume2/docker/gear-watcher/data` (Ordner vorher per
   SSH anlegen: `mkdir -p /volume2/docker/gear-watcher/data`).
3. `http://<NAS-IP>:5030` öffnen, mit `admin` + Passwort anmelden.
4. **Einstellungen** → ntfy-Server und Topic eintragen. Verlangt der ntfy-Server
   Anmeldung (eigener Server mit `auth-default-access: deny-all`), einen Access-Token
   anlegen (`ntfy token add <user>`) und eintragen. "Testnachricht senden" prüft die
   Verbindung.
5. **Suchen** → "Neue Suche".

Das Image wird per GitHub Actions bei jedem Push auf `main` nach
`ghcr.io/aofeindtme/gear-watcher` gebaut.

## Lokal entwickeln

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q tests
DB_PATH=./data/dev.db ADMIN_PASSWORD=devdevdev .venv/bin/flask --app app run --port 5099
```

`WATCHER_DISABLED=true` schaltet den Hintergrund-Abruf ab (z. B. zum UI-Testen);
"Jetzt abrufen" funktioniert trotzdem.

## Mehrbenutzer

Aktuell ein Account. Das Datenmodell ist schon mehrbenutzerfähig (Suchen,
ntfy-Einstellungen und Protokoll hängen an `user_id`) - für weitere Nutzer fehlt nur
eine Verwaltungsseite.
