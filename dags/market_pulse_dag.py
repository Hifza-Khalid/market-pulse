from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.email import EmailOperator
from airflow.utils.dates import days_ago
from datetime import datetime, timedelta
import os
import sys
import sqlite3
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ml.train import train_ticker

DB_PATH = os.environ.get("MARKET_PULSE_DB", "/opt/airflow/data/market_pulse.db")

default_args = {
    "owner": "market-pulse",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
}


def fetch_daily_prices(**context):
    conn = sqlite3.connect(DB_PATH)
    tickers = [r[0] for r in conn.execute("SELECT ticker FROM watchlist").fetchall()]
    if not tickers:
        conn.close()
        return {"count": 0}

    for t in tickers:
        hist = yf.Ticker(t).history(period="5d")
        if hist.empty:
            continue
        latest = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else latest
        change = ((latest["Close"] - prev["Close"]) / prev["Close"]) * 100

        conn.execute(
            """
            INSERT OR REPLACE INTO daily_prices
            (ticker, date, open, high, low, close, volume, daily_change_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t, datetime.utcnow().strftime("%Y-%m-%d"),
                float(latest["Open"]), float(latest["High"]), float(latest["Low"]),
                float(latest["Close"]), int(latest["Volume"]), float(change),
            ),
        )
    conn.commit()
    conn.close()
    return {"count": len(tickers)}


def check_alerts(**context):
    """Evaluate all active alerts against the latest prices."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    active = conn.execute("SELECT * FROM alerts WHERE active = 1").fetchall()
    triggered = []

    for alert in active:
        row = conn.execute(
            "SELECT close, daily_change_pct FROM daily_prices WHERE ticker = ? ORDER BY date DESC LIMIT 1",
            (alert["ticker"],),
        ).fetchone()
        if not row:
            continue

        close = row["close"]
        pct = row["daily_change_pct"] or 0
        condition = alert["condition_type"]
        threshold = alert["threshold"]

        met = (
            (condition == "above" and close > threshold) or
            (condition == "below" and close < threshold) or
            (condition == "pct_rise" and pct >= abs(threshold)) or
            (condition == "pct_drop" and pct <= -abs(threshold))
        )

        if met:
            triggered.append({
                "ticker": alert["ticker"],
                "condition": condition,
                "threshold": threshold,
                "close": close,
                "change_pct": pct,
            })
            conn.execute(
                "UPDATE alerts SET last_triggered_at = CURRENT_TIMESTAMP WHERE id = ?",
                (alert["id"],),
            )

    conn.commit()
    conn.close()

    context["ti"].xcom_push(key="triggered_alerts", value=triggered)
    return {"triggered": len(triggered)}


def retrain_models(**context):
    conn = sqlite3.connect(DB_PATH)
    tickers = [r[0] for r in conn.execute("SELECT ticker FROM watchlist").fetchall()]
    conn.close()
    trained = []
    for t in tickers:
        try:
            train_ticker(t)
            trained.append(t)
        except Exception as e:
            print(f"[error] {t}: {e}")
    return {"trained": trained}


def build_email_body(**context):
    ti = context["ti"]
    triggered = ti.xcom_pull(task_ids="check_alerts", key="triggered_alerts") or []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT ticker, close, daily_change_pct
        FROM daily_prices
        WHERE date = (SELECT MAX(date) FROM daily_prices)
        ORDER BY daily_change_pct DESC
        """
    ).fetchall()
    conn.close()

    rows_html = "".join(
        f"""
        <tr>
          <td style="padding:10px;border-bottom:1px solid #eee;font-weight:600;">{r['ticker']}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;">{r['close']:.2f}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;color:{'#059669' if (r['daily_change_pct'] or 0) >= 0 else '#dc2626'};">
            {(r['daily_change_pct'] or 0):.2f}%
          </td>
        </tr>
        """
        for r in rows
    )

    alerts_html = ""
    if triggered:
        alert_rows = "".join(
            f"""
            <tr>
              <td style="padding:8px;border-bottom:1px solid #fecaca;font-weight:600;">{a['ticker']}</td>
              <td style="padding:8px;border-bottom:1px solid #fecaca;">{a['condition']} {a['threshold']}</td>
              <td style="padding:8px;border-bottom:1px solid #fecaca;">Close: {a['close']:.2f} ({a['change_pct']:+.2f}%)</td>
            </tr>
            """
            for a in triggered
        )
        alerts_html = f"""
        <div style="background:#fef2f2;border-left:4px solid #dc2626;padding:16px;margin:20px 0;">
          <h3 style="color:#dc2626;margin:0 0 12px 0;">Price Alerts Triggered</h3>
          <table style="width:100%;border-collapse:collapse;">{alert_rows}</table>
        </div>
        """

    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:600px;margin:auto;color:#334155;">
      <h2 style="color:#0f172a;">Market Pulse Daily Digest</h2>
      <p>Your watchlist performance for <strong>{datetime.utcnow().strftime('%B %d, %Y')}</strong>:</p>
      {alerts_html}
      <table style="width:100%;border-collapse:collapse;margin-top:20px;">
        <thead>
          <tr style="background:#f8fafc;text-align:left;">
            <th style="padding:10px;">Ticker</th>
            <th style="padding:10px;">Close</th>
            <th style="padding:10px;">Change</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
      <p style="margin-top:24px;color:#64748b;font-size:13px;">
        Log in to the Market Pulse dashboard for interactive charts, simulator, and ML forecasts.
      </p>
    </div>
    """

    ti.xcom_push(key="email_html", value=html)
    return "built"


with DAG(
    dag_id="market_pulse_daily",
    default_args=default_args,
    schedule="@daily",
    start_date=days_ago(1),
    catchup=False,
    max_active_runs=1,
    tags=["finance", "market-pulse"],
) as dag:

    t1 = PythonOperator(task_id="fetch_daily_prices", python_callable=fetch_daily_prices)
    t2 = PythonOperator(task_id="check_alerts", python_callable=check_alerts)
    t3 = PythonOperator(task_id="retrain_models", python_callable=retrain_models)
    t4 = PythonOperator(task_id="build_email_body", python_callable=build_email_body)
    t5 = EmailOperator(
        task_id="send_daily_digest",
        to=os.environ.get("DIGEST_EMAIL", "you@example.com"),
        subject="Market Pulse Digest - {{ ds }}",
        html_content="{{ ti.xcom_pull(task_ids='build_email_body', key='email_html') }}",
    )

    t1 >> t2 >> t3 >> t4 >> t5