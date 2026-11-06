import os
import importlib
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("MARKET_PULSE_DB", str(db_file))
    import app as app_module
    importlib.reload(app_module)
    app_module.init_db()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200


def test_watchlist_empty(client):
    r = client.get("/api/watchlist")
    assert r.status_code == 200
    assert r.get_json() == []


def test_summary_empty(client):
    r = client.get("/api/summary")
    assert r.status_code == 200
    data = r.get_json()
    assert data["avg_change"] == 0
    assert data["top_gainers"] == []
    assert data["top_losers"] == []
    assert data["range_52w"] == {}


def test_simulate_missing_params(client):
    r = client.get("/api/simulate")
    assert r.status_code == 400


def test_simulate_bad_amount(client):
    r = client.get("/api/simulate?ticker=AAPL&amount=-5&since=2025-01-01")
    assert r.status_code == 400


def test_add_alert_invalid(client):
    r = client.post("/api/alerts", json={"ticker": "", "condition_type": "above", "threshold": 100})
    assert r.status_code == 400


def test_add_alert_valid(client):
    r = client.post("/api/alerts", json={"ticker": "AAPL", "condition_type": "above", "threshold": 250})
    assert r.status_code == 200
    alerts = client.get("/api/alerts").get_json()
    assert len(alerts) == 1
    assert alerts[0]["ticker"] == "AAPL"


def test_delete_alert(client):
    client.post("/api/alerts", json={"ticker": "TSLA", "condition_type": "below", "threshold": 100})
    alerts = client.get("/api/alerts").get_json()
    aid = alerts[0]["id"]
    r = client.delete(f"/api/alerts/{aid}")
    assert r.status_code == 200
    assert client.get("/api/alerts").get_json() == []


def test_news_empty_for_unknown(client):
    r = client.get("/api/news/NOTAREALTICKER")
    assert r.status_code == 200
    assert r.get_json() == []