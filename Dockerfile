FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py db.py notify.py watcher.py ./
COPY sources ./sources
COPY templates ./templates
COPY static ./static

RUN mkdir -p /data
VOLUME ["/data"]

# Läuft als root: /data wird ggf. per Bind-Mount vom Host überlagert, dessen
# Berechtigungen wir nicht kontrollieren (siehe ipsc_matcher).
EXPOSE 5000

# WICHTIG: nur 1 Worker! Der Watcher-Thread startet beim Modul-Import - mit
# mehreren Workern liefe er mehrfach und schickte doppelte Benachrichtigungen.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "180", "--bind", "0.0.0.0:5000", "app:app"]
