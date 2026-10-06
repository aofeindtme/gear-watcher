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
- **Preissenkung** eines bekannten Angebots (nicht bei Auktionsgeboten) löst ebenfalls
  eine Nachricht aus; Tiefstpreis und Preisverlauf werden gespeichert.
- Unter **Treffer** alle Funde als Karten, filterbar nach Suche/Status/Quelle, mit
  Merken/Gesehen/Ausblenden.

## Quellen

| Quelle | Art | Filter direkt auf der Seite |
|---|---|---|
| eGun | Auktionen/Sofortkauf | Preis, PLZ/Umkreis, Zustand, Beschreibung |
| Kleinanzeigen | Kleinanzeigen | Preis, PLZ/Umkreis |

Ausschlusswörter und Preisgrenzen werden zusätzlich generisch nachgefiltert.

Geplant (je ein Adapter in `sources/`): eBay (offizielle Browse-API), Frankonia-Kleinanzeigen,
Frankonia, Brownells, Recon Company, Pirscher Gear, Sportwaffen Triebel, Alljagd,
Waffen Schumacher. Amazon nur über Keepa (kostenpflichtige API) - Amazon blockt Scraper.

Neue Quelle hinzufügen: Modul in `sources/` mit `search(watch, session) -> list[Listing]`
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
