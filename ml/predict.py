"""Standalone prediction CLI — useful for debugging outside Flask."""
import os
import sys
import sqlite3
import joblib
import pandas as pd
from ml.features import compute_features, FEATURES

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get(
    "MARKET_PULSE_DB", os.path.join(BASE_DIR, "data", "market_pulse.db")
)
MODEL_DIR = os.path.join(BASE_DIR, "models")


def predict(ticker: str):
    model_path = os.path.join(MODEL_DIR, f"{ticker}_model.pkl")
    if not os.path.exists(model_path):
        print(f"No model for {ticker}. Train first.")
        return None

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT date, close, volume FROM daily_prices WHERE ticker = ? ORDER BY date ASC",
        conn, params=(ticker,),
    )
    conn.close()

    if len(df) < 30:
        print(f"Not enough data for {ticker}.")
        return None

    df = compute_features(df)
    latest = df[FEATURES].iloc[[-1]]
    model = joblib.load(model_path)
    pred = float(model.predict(latest)[0])
    residual_std = getattr(model, "residual_std_", pred * 0.02)

    result = {
        "ticker": ticker,
        "last_close": float(df["close"].iloc[-1]),
        "predicted_close": pred,
        "low": pred - 1.96 * residual_std,
        "high": pred + 1.96 * residual_std,
    }
    print(result)
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m ml.predict TICKER")
        sys.exit(1)
    predict(sys.argv[1].upper())