import numpy as np
import pandas as pd

FEATURES = ["lag_1", "lag_2", "lag_3", "sma_5", "sma_10", "sma_20", "rsi", "vol_change"]


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
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

    df["target"] = df["close"].shift(-1)
    return df.dropna()