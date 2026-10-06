"""ntfy-Benachrichtigungen (JSON-Publish, damit Umlaute in Titeln sicher ankommen)."""
import requests


def send(settings: dict, title: str, message: str, click: str = "", image: str = "",
         tags: str = "mag", priority: int = 3) -> tuple[bool, str | None]:
    """Rückgabe: (erfolgreich, Fehlerdetail-oder-None). Ohne Topic: (False, None)."""
    server = (settings.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
    topic = settings.get("ntfy_topic") or ""
    if not topic:
        return False, None

    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "tags": [t for t in tags.split(",") if t],
        "priority": priority,
    }
    if click:
        payload["click"] = click
        payload["actions"] = [{"action": "view", "label": "Öffnen", "url": click}]
    if image:
        payload["attach"] = image

    headers = {}
    auth = None
    if settings.get("ntfy_token"):
        headers["Authorization"] = f"Bearer {settings['ntfy_token']}"
    elif settings.get("ntfy_user"):
        auth = (settings["ntfy_user"], settings.get("ntfy_password") or "")

    try:
        resp = requests.post(server, json=payload, headers=headers, auth=auth, timeout=10)
        resp.raise_for_status()
        return True, None
    except requests.HTTPError as e:
        return False, f"HTTP {e.response.status_code}: {e.response.text[:200]}"
    except requests.RequestException as e:
        return False, str(e)
