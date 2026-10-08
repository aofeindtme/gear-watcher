import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("ADMIN_PASSWORD", "test")
os.environ["WATCHER_DISABLED"] = "true"
import app  # noqa: E402


def test_price_alert_links_encode_query():
    links = dict(app.price_alert_links(" Garmin fēnix 8 "))
    assert links["idealo"].endswith("?q=Garmin+f%C4%93nix+8")
    assert links["Geizhals"] == "https://geizhals.de/?fs=Garmin+f%C4%93nix+8"
    assert app.price_alert_links("") == []
