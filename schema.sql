CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL UNIQUE,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    daily_change_pct REAL,
    UNIQUE (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_ticker_date ON daily_prices(ticker, date);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    prediction_date TEXT NOT NULL,
    predicted_close REAL,
    confidence_low REAL,
    confidence_high REAL,
    model_version TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    condition_type TEXT NOT NULL,
    threshold REAL NOT NULL,
    active INTEGER DEFAULT 1,
    last_triggered_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(active);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT,
    tickers_processed INTEGER,
    status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);