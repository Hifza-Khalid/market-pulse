import os
import sqlite3
import joblib
import numpy as np
import pandas as pd
from datetime import datetime
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from ml.features import compute_features, FEATURES

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get(
    "MARKET_PULSE_DB", os.path.join(BASE_DIR, "data", "market_pulse.db")
)
MODEL_DIR = os.path.join(BASE_DIR, "models")
os.makedirs(MODEL_DIR, exist_ok=True)


def _ensure_schema():
    """Create tables if they don't exist yet. Safe to call every run."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    schema_path = os.path.join(BASE_DIR, "schema.sql")
    if not os.path.exists(schema_path):
        print(f"[warn] schema.sql not found at {schema_path}")
        return
    conn = sqlite3.connect(DB_PATH)
    with open(schema_path) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def train_ticker(ticker: str):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT date, close, volume FROM daily_prices WHERE ticker = ? ORDER BY date ASC",
        conn,
        params=(ticker,),
    )
    conn.close()

    if len(df) < 60:
        print(f"[skip] {ticker}: only {len(df)} rows (need at least 60)")
        return None

    df = compute_features(df)
    X, y = df[FEATURES], df["target"]

    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    model = RandomForestRegressor(
        n_estimators=200,
        max_depth=10,
        min_samples_leaf=3,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)
    preds = model.predict(X_test)

    mae = mean_absolute_error(y_test, preds)
    rmse = float(np.sqrt(mean_squared_error(y_test, preds)))
    r2 = r2_score(y_test, preds)
    model.residual_std_ = float((y_test - preds).std())

    joblib.dump(model, os.path.join(MODEL_DIR, f"{ticker}_model.pkl"))
    print(f"[trained] {ticker} | MAE={mae:.2f} RMSE={rmse:.2f} R2={r2:.3f}")
    return {"ticker": ticker, "mae": mae, "rmse": rmse, "r2": r2}


def train_all():
    _ensure_schema()

    conn = sqlite3.connect(DB_PATH)
    tickers = [
        r[0] for r in conn.execute("SELECT DISTINCT ticker FROM watchlist").fetchall()
    ]
    conn.close()

    if not tickers:
        print("[info] No tickers in watchlist. Add one from the dashboard first.")
        return []

    results = []
    for t in tickers:
        try:
            r = train_ticker(t)
            if r:
                results.append(r)
        except Exception as e:
            print(f"[error] {t}: {e}")
    return results


if __name__ == "__main__":
    results = train_all()
    print(f"\nTrained {len(results)} model(s) at {datetime.now().isoformat()}")