import importlib

import pytest

from sources import Listing


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    import db
    importlib.reload(db)
    import watcher
    importlib.reload(watcher)
    monkeypatch.setattr(watcher, "REQUEST_PAUSE_SECONDS", 0)
    db.init_db()
    uid = db.create_user("me", "x")
    db.save_user_settings(uid, {"ntfy_topic": "t"})
    sent = []
    monkeypatch.setattr(watcher.notify, "send", lambda s, title, msg, **kw: sent.append(title) or (True, None))
    results = {"items": []}
    monkeypatch.setitem(watcher.SOURCES, "fake", {"label": "Fake", "kind": "classifieds", "filters": set(),
                                                  "search": lambda w, s: list(results["items"])})
    wid = db.save_watch(uid, {"name": "ZF", "query": "zf", "sites": "fake", "interval_min": 60,
                              "notify_price_drop": 1, "active": 1})
    return db, watcher, uid, wid, results, sent


def item(i, price):
    return Listing(site="fake", ext_id=str(i), title=f"Item {i}", url=f"u{i}", price=price)


def test_baseline_then_new_then_price_drop(env):
    db, watcher, uid, wid, results, sent = env
    results["items"] = [item(1, 100), item(2, 200)]
    s = watcher.run_watch(db.get_watch(wid))
    assert s["new"] == 0 and sent == []                      # Baseline: still
    assert {l["status"] for l in db.list_listings(uid)} == {"seen"}

    results["items"] = [item(3, 300), item(1, 100), item(2, 200)]
    s = watcher.run_watch(db.get_watch(wid))
    assert s["new"] == 1 and len(sent) == 1 and "Item 3" in sent[0]

    results["items"] = [item(3, 300), item(1, 80), item(2, 250)]
    s = watcher.run_watch(db.get_watch(wid))
    assert s["price_drop"] == 1 and "Item 1" in sent[-1]
    row = next(l for l in db.list_listings(uid) if l["ext_id"] == "1")
    assert row["status"] == "new" and row["lowest_price"] == 80
    assert next(l for l in db.list_listings(uid) if l["ext_id"] == "2")["lowest_price"] == 200


def test_many_new_items_are_bundled(env):
    db, watcher, uid, wid, results, sent = env
    watcher.run_watch(db.get_watch(wid))                     # Baseline mit 0 Treffern
    results["items"] = [item(i, 10) for i in range(8)]
    watcher.run_watch(db.get_watch(wid))
    assert sent == ["ZF: 8 Treffer"]


def test_editing_watch_rebaselines(env):
    db, watcher, uid, wid, results, sent = env
    watcher.run_watch(db.get_watch(wid))
    db.save_watch(uid, {"max_price": 50}, wid)
    results["items"] = [item(9, 10)]
    watcher.run_watch(db.get_watch(wid))
    assert sent == []


def test_broken_source_does_not_block_others(env):
    db, watcher, uid, wid, results, sent = env
    def boom(w, s):
        raise RuntimeError("kaputt")
    watcher.SOURCES["broken"] = {"label": "Broken", "kind": "classifieds", "filters": set(), "search": boom}
    db.save_watch(uid, {"sites": "broken,fake"}, wid)
    results["items"] = [item(1, 1)]
    s = watcher.run_watch(db.get_watch(wid))
    assert s["errors"] == 1
    assert db.get_site_states(wid)["fake"]["last_count"] == 1
    assert "kaputt" in db.get_site_states(wid)["broken"]["last_error"]


def test_back_in_stock(env):
    db, watcher, uid, wid, results, sent = env
    def shop(avail, price=100):
        return Listing(site="fake", ext_id="p", title="Jacke", url="u", price=price, available=avail)
    results["items"] = [shop(False)]
    watcher.run_watch(db.get_watch(wid))
    results["items"] = [shop(None)]            # unbekannt überschreibt den Status nicht
    watcher.run_watch(db.get_watch(wid))
    results["items"] = [shop(True)]
    s = watcher.run_watch(db.get_watch(wid))
    assert s["back_in_stock"] == 1 and sent == ["Wieder lieferbar: Jacke"]


def test_missing_credentials_skip_source(env):
    db, watcher, uid, wid, results, sent = env
    watcher.SOURCES["fake"]["needs"] = ("ebay_client_id",)
    s = watcher.run_watch(db.get_watch(wid))
    assert s["errors"] == 0
    assert "Zugangsdaten" in db.get_site_states(wid)["fake"]["last_error"]
