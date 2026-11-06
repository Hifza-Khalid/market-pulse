import os
import sqlite3
import joblib
import numpy as np
import pandas as pd
import yfinance as yf
from flask import Flask, jsonify, request, render_template
from dotenv import load_dotenv
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get(
    "MARKET_PULSE_DB", os.path.join(BASE_DIR, "data", "market_pulse.db")
)
MODEL_DIR = os.path.join(BASE_DIR, "models")

app = Flask(__name__)
analyzer = SentimentIntensityAnalyzer()


# ---------------- DB helpers ----------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    with open(os.path.join(BASE_DIR, "schema.sql")) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def _recompute_change_pct(conn, ticker=None):
    """
    Recompute daily_change_pct for a single ticker (or all tickers if None).
    Safe to run multiple times — idempotent.
    """
    if ticker:
        conn.execute(
            """
            UPDATE daily_prices
            SET daily_change_pct = (
                SELECT ((d1.close - d2.close) / d2.close) * 100
                FROM daily_prices d1
                JOIN daily_prices d2
                  ON d2.ticker = d1.ticker
                 AND d2.date = (
                     SELECT MAX(date) FROM daily_prices
                     WHERE ticker = d1.ticker AND date < d1.date
                 )
                WHERE d1.ticker = daily_prices.ticker
                  AND d1.date = daily_prices.date
            )
            WHERE ticker = ?
            """,
            (ticker,),
        )
    else:
        conn.execute(
            """
            UPDATE daily_prices
            SET daily_change_pct = (
                SELECT ((d1.close - d2.close) / d2.close) * 100
                FROM daily_prices d1
                JOIN daily_prices d2
                  ON d2.ticker = d1.ticker
                 AND d2.date = (
                     SELECT MAX(date) FROM daily_prices
                     WHERE ticker = d1.ticker AND date < d1.date
                 )
                WHERE d1.ticker = daily_prices.ticker
                  AND d1.date = daily_prices.date
            )
            """
        )
    conn.commit()


def _ensure_change_pct(conn):
    """
    Lazy self-heal: if the latest batch of rows has NULL daily_change_pct,
    recompute for all tickers. Called by /api/summary before querying.
    """
    row = conn.execute(
        """
        SELECT COUNT(*) AS missing
        FROM daily_prices
        WHERE date = (SELECT MAX(date) FROM daily_prices)
          AND daily_change_pct IS NULL
        """
    ).fetchone()
    if row and row["missing"] > 0:
        _recompute_change_pct(conn)


# ---------------- Pages ----------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------- Watchlist ----------------
@app.get("/api/watchlist")
def list_watchlist():
    conn = get_db()
    rows = conn.execute("SELECT ticker FROM watchlist ORDER BY added_at").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/watchlist")
def add_watchlist():
    data = request.get_json() or {}
    ticker = (data.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400

    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty:
            return jsonify({"error": "unknown ticker"}), 400
    except Exception as e:
        print(f"[yfinance] error for {ticker}: {e}")
        return jsonify({"error": "unknown ticker"}), 400

    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO watchlist (ticker) VALUES (?)", (ticker,))
        conn.commit()
    finally:
        conn.close()

    try:
        _bootstrap_ticker(ticker)
    except Exception as e:
        print(f"[bootstrap] error for {ticker}: {e}")

    return jsonify({"ticker": ticker, "status": "added"})


@app.delete("/api/watchlist/<ticker>")
def delete_watchlist(ticker):
    conn = get_db()
    conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker.upper(),))
    conn.execute("DELETE FROM alerts WHERE ticker = ?", (ticker.upper(),))
    conn.commit()
    conn.close()
    return jsonify({"ticker": ticker, "status": "removed"})


# ---------------- Prices & Summary ----------------
@app.get("/api/prices/<ticker>")
def get_prices(ticker):
    ticker = ticker.upper()
    conn = get_db()
    rows = conn.execute(
        "SELECT date, close FROM daily_prices WHERE ticker = ? ORDER BY date ASC",
        (ticker,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/summary")
def get_summary():
    conn = get_db()

    # Self-heal: recompute change % if the latest rows are NULL
    _ensure_change_pct(conn)

    latest_row = conn.execute("SELECT MAX(date) AS d FROM daily_prices").fetchone()
    latest_date = latest_row["d"] if latest_row else None

    if not latest_date:
        conn.close()
        return jsonify({
            "avg_change": 0, "best": "—", "last_updated": "—",
            "top_gainers": [], "top_losers": [], "range_52w": {},
        })

    rows = conn.execute(
        "SELECT ticker, close, daily_change_pct FROM daily_prices WHERE date = ?",
        (latest_date,),
    ).fetchall()

    changes = [r["daily_change_pct"] for r in rows if r["daily_change_pct"] is not None]
    avg = float(np.mean(changes)) if changes else 0.0

    sorted_rows = sorted(rows, key=lambda r: r["daily_change_pct"] or -999, reverse=True)

    top_gainers = [
        {
            "ticker": r["ticker"],
            "change_pct": round(r["daily_change_pct"] or 0, 2),
            "close": round(r["close"], 2),
        }
        for r in sorted_rows[:5]
        if (r["daily_change_pct"] or 0) > 0
    ]

    top_losers = [
        {
            "ticker": r["ticker"],
            "change_pct": round(r["daily_change_pct"] or 0, 2),
            "close": round(r["close"], 2),
        }
        for r in sorted_rows[-5:]
        if (r["daily_change_pct"] or 0) < 0
    ]
    top_losers.reverse()

    range_52w = {}
    for r in rows:
        t = r["ticker"]
        w = conn.execute(
            """
            SELECT MAX(close) AS hi, MIN(close) AS lo
            FROM daily_prices
            WHERE ticker = ? AND date >= date('now', '-365 days')
            """,
            (t,),
        ).fetchone()
        if w and w["hi"] is not None:
            range_52w[t] = {
                "high": round(w["hi"], 2),
                "low": round(w["lo"], 2),
                "current": round(r["close"], 2),
            }

    conn.close()

    return jsonify({
        "avg_change": round(avg, 2),
        "best": sorted_rows[0]["ticker"] if sorted_rows else "—",
        "last_updated": latest_date,
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "range_52w": range_52w,
    })


# ---------------- Portfolio Simulator ----------------
@app.get("/api/simulate")
def simulate_portfolio():
    ticker = (request.args.get("ticker") or "").upper()
    amount = request.args.get("amount", type=float)
    since = request.args.get("since", "")

    if not ticker or not amount or not since:
        return jsonify({"error": "ticker, amount, since are required"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be positive"}), 400

    conn = get_db()
    history = conn.execute(
        """
        SELECT date, close FROM daily_prices
        WHERE ticker = ? AND date >= ?
        ORDER BY date ASC
        """,
        (ticker, since),
    ).fetchall()

    latest = conn.execute(
        "SELECT date, close FROM daily_prices WHERE ticker = ? ORDER BY date DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    conn.close()

    if not history or not latest:
        return jsonify({"error": "no data for this ticker in the given period"}), 404

    entry_price = history[0]["close"]
    current_price = latest["close"]
    shares = amount / entry_price
    current_value = shares * current_price
    gain = current_value - amount
    gain_pct = (gain / amount) * 100

    trajectory = [
        {"date": h["date"], "value": round(shares * h["close"], 2)} for h in history
    ]

    return jsonify({
        "ticker": ticker,
        "amount": amount,
        "entry_date": history[0]["date"],
        "entry_price": round(entry_price, 2),
        "current_date": latest["date"],
        "current_price": round(current_price, 2),
        "shares": round(shares, 4),
        "current_value": round(current_value, 2),
        "gain": round(gain, 2),
        "gain_pct": round(gain_pct, 2),
        "trajectory": trajectory,
    })


# ---------------- Alerts ----------------
@app.get("/api/alerts")
def list_alerts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM alerts ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/alerts")
def add_alert():
    data = request.get_json() or {}
    ticker = (data.get("ticker") or "").strip().upper()
    condition_type = data.get("condition_type")
    threshold = data.get("threshold")

    valid_conditions = {"above", "below", "pct_drop", "pct_rise"}
    if not ticker or condition_type not in valid_conditions:
        return jsonify({"error": "invalid ticker or condition_type"}), 400
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return jsonify({"error": "threshold must be numeric"}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO alerts (ticker, condition_type, threshold) VALUES (?, ?, ?)",
        (ticker, condition_type, threshold),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "created"})


@app.delete("/api/alerts/<int:alert_id>")
def delete_alert(alert_id):
    conn = get_db()
    conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


# ---------------- News + Sentiment ----------------
def _extract_news_fields(item):
    if "content" in item and isinstance(item["content"], dict):
        content = item["content"]
        title = content.get("title", "")
        publisher = (content.get("provider") or {}).get("displayName", "Unknown")
        link = (content.get("canonicalUrl") or {}).get("url", "")
    else:
        title = item.get("title", "")
        publisher = item.get("publisher", "Unknown")
        link = item.get("link", "")
    return title, publisher, link


@app.get("/api/news/<ticker>")
def get_news(ticker):
    ticker = ticker.upper()
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception:
        return jsonify([])

    results = []
    for item in raw[:10]:
        title, publisher, link = _extract_news_fields(item)
        if not title:
            continue
        scores = analyzer.polarity_scores(title)
        compound = scores["compound"]
        if compound >= 0.05:
            sentiment = "Bullish"
        elif compound <= -0.05:
            sentiment = "Bearish"
        else:
            sentiment = "Neutral"
        results.append({
            "title": title,
            "publisher": publisher,
            "link": link,
            "sentiment": sentiment,
            "score": round(compound, 3),
        })

    return jsonify(results[:5])


# ---------------- Predictions ----------------
@app.get("/api/predictions")
def get_predictions():
    conn = get_db()
    tickers = [r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist").fetchall()]
    conn.close()

    output = []
    for t in tickers:
        try:
            pred = _predict_ticker(t)
            if pred:
                output.append(pred)
        except Exception:
            continue
    return jsonify(output)


# ---------------- Internal helpers ----------------
def _bootstrap_ticker(ticker, period="6mo"):
    df = yf.Ticker(ticker).history(period=period)
    if df.empty:
        return
    conn = get_db()
    for date, row in df.iterrows():
        date_str = date.strftime("%Y-%m-%d")
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_prices
            (ticker, date, open, high, low, close, volume, daily_change_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker, date_str,
                float(row["Open"]), float(row["High"]), float(row["Low"]),
                float(row["Close"]), int(row["Volume"]), None,
            ),
        )
    conn.commit()

    # Recompute change % for this ticker only
    _recompute_change_pct(conn, ticker)
    conn.close()


def _predict_ticker(ticker):
    model_path = os.path.join(MODEL_DIR, f"{ticker}_model.pkl")
    if not os.path.exists(model_path):
        return None

    conn = get_db()
    rows = conn.execute(
        "SELECT date, close, volume FROM daily_prices WHERE ticker = ? ORDER BY date ASC",
        (ticker,),
    ).fetchall()
    conn.close()

    if len(rows) < 30:
        return None

    df = pd.DataFrame(rows, columns=["date", "close", "volume"])
    df = _compute_features(df)

    features = ["lag_1", "lag_2", "lag_3", "sma_5", "sma_10", "sma_20", "rsi", "vol_change"]
    latest = df[features].iloc[[-1]]

    model = joblib.load(model_path)
    predicted = float(model.predict(latest)[0])

    residual_std = getattr(model, "residual_std_", predicted * 0.02)
    low = predicted - 1.96 * residual_std
    high = predicted + 1.96 * residual_std

    return {
        "ticker": ticker,
        "last_close": float(df["close"].iloc[-1]),
        "predicted_close": predicted,
        "confidence_low": max(low, 0),
        "confidence_high": high,
    }


def _compute_features(df):
    df = df.copy()
    df["close"] = df["close"].astype(float)
    df["lag_1"] = df["close"].shift(1)
    df["lag_2"] = df["close"].shift(2)
    df["lag_3"] = df["close"].shift(3)
    df["sma_5"] = df["close"].rolling(5).mean()
    df["sma_10"] = df["close"].rolling(10).mean()
    df["sma_20"] = df["close"].rolling(20).mean()
    df["vol_change"] = df["volume"].pct_change().replace([np.inf, -np.inf], 0)
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df.dropna()


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)