"""
Forecasting Service
===================
Model yang digunakan:
  1. Linear Regression     — baseline trend forecasting
  2. Moving Average        — smoothing & short-term forecast
  3. Exponential Smoothing — weighted recent data
  4. Polynomial Regression — nonlinear trend

Semua model bekerja pada data bulanan yang diagregasi dari DB.

Cocok sebagai referensi mata kuliah:
  - Analisis Time Series
  - Komputasi Statistika
  - Machine Learning
  - Visualisasi Data
  - Sistem Pendukung Keputusan
"""

import numpy  as np
import pandas as pd
from datetime import datetime

from sklearn.linear_model   import LinearRegression
from sklearn.preprocessing  import PolynomialFeatures
from sklearn.metrics        import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline       import Pipeline

from sqlalchemy             import func
from backend.database.db    import db
from backend.models.distribution_model import Distribution


# ─────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────
def _f(v):
    try:    return float(v or 0)
    except: return 0.0

def _make_monthly(items, date_col="tanggal_penyaluran", qty_col="jumlah"):
    """
    Konversi list dict/row ke DataFrame bulanan.
    Return DataFrame dengan kolom: bulan (str), jumlah (float), index (int)
    """
    rows = [{"tanggal": str(getattr(it, date_col, "") or ""), "jumlah": _f(getattr(it, qty_col, 0))} for it in items]
    df   = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame()

    df["tanggal"] = pd.to_datetime(df["tanggal"], errors="coerce")
    df = df.dropna(subset=["tanggal"])
    if df.empty:
        return pd.DataFrame()

    monthly = (
        df.groupby(df["tanggal"].dt.to_period("M"))["jumlah"]
        .sum()
        .reset_index()
    )
    monthly.columns = ["bulan", "jumlah"]
    monthly["bulan"] = monthly["bulan"].astype(str)
    monthly = monthly.sort_values("bulan").reset_index(drop=True)
    monthly["index"] = range(len(monthly))
    return monthly


def _next_months(last_period_str: str, n: int) -> list[str]:
    """Generate n bulan setelah last_period_str (format YYYY-MM)."""
    try:
        y, m = map(int, last_period_str.split("-"))
    except:
        y, m = datetime.today().year, datetime.today().month
    result = []
    for _ in range(n):
        m += 1
        if m > 12:
            m  = 1
            y += 1
        result.append(f"{y}-{str(m).zfill(2)}")
    return result


def _metrics(y_true, y_pred) -> dict:
    """Hitung MAE, RMSE, R² untuk evaluasi model."""
    if len(y_true) < 2:
        return {"mae": 0, "rmse": 0, "r2": 0}
    mae  = round(mean_absolute_error(y_true, y_pred), 2)
    rmse = round(np.sqrt(mean_squared_error(y_true, y_pred)), 2)
    r2   = round(r2_score(y_true, y_pred), 4)
    return {"mae": mae, "rmse": rmse, "r2": r2}


# ─────────────────────────────────────────────────────────────
# MODEL 1 — LINEAR REGRESSION
# ─────────────────────────────────────────────────────────────
def _linear_forecast(monthly: pd.DataFrame, n_periods: int) -> dict:
    X = monthly[["index"]].values
    y = monthly["jumlah"].values

    model  = LinearRegression()
    model.fit(X, y)
    fitted = model.predict(X)

    future_idx   = np.array([[monthly["index"].max() + i + 1] for i in range(n_periods)])
    future_preds = np.clip(model.predict(future_idx), 0, None)
    future_bulan = _next_months(monthly["bulan"].iloc[-1], n_periods)

    return {
        "labels_history": monthly["bulan"].tolist(),
        "values_history": monthly["jumlah"].tolist(),
        "values_fitted":  [round(float(v), 2) for v in fitted],
        "labels_forecast": future_bulan,
        "values_forecast": [round(float(v), 2) for v in future_preds],
        "model_name":     "Linear Regression",
        "metrics":        _metrics(y, fitted),
        "coef":           round(float(model.coef_[0]), 2),
        "intercept":      round(float(model.intercept_), 2),
    }


# ─────────────────────────────────────────────────────────────
# MODEL 2 — POLYNOMIAL REGRESSION (degree 2)
# ─────────────────────────────────────────────────────────────
def _poly_forecast(monthly: pd.DataFrame, n_periods: int) -> dict:
    X = monthly[["index"]].values
    y = monthly["jumlah"].values

    pipe = Pipeline([
        ("poly", PolynomialFeatures(degree=2, include_bias=False)),
        ("lr",   LinearRegression()),
    ])
    pipe.fit(X, y)
    fitted = pipe.predict(X)

    future_idx   = np.array([[monthly["index"].max() + i + 1] for i in range(n_periods)])
    future_preds = np.clip(pipe.predict(future_idx), 0, None)
    future_bulan = _next_months(monthly["bulan"].iloc[-1], n_periods)

    return {
        "labels_history":  monthly["bulan"].tolist(),
        "values_history":  monthly["jumlah"].tolist(),
        "values_fitted":   [round(float(v), 2) for v in fitted],
        "labels_forecast": future_bulan,
        "values_forecast": [round(float(v), 2) for v in future_preds],
        "model_name":      "Polynomial Regression (degree=2)",
        "metrics":         _metrics(y, fitted),
    }


# ─────────────────────────────────────────────────────────────
# MODEL 3 — MOVING AVERAGE
# ─────────────────────────────────────────────────────────────
def _ma_forecast(monthly: pd.DataFrame, n_periods: int, window: int = 3) -> dict:
    y      = monthly["jumlah"].values
    w      = min(window, len(y))
    fitted = pd.Series(y).rolling(w, min_periods=1).mean().values

    last_ma = float(np.mean(y[-w:]))
    future_bulan = _next_months(monthly["bulan"].iloc[-1], n_periods)

    return {
        "labels_history":  monthly["bulan"].tolist(),
        "values_history":  monthly["jumlah"].tolist(),
        "values_fitted":   [round(float(v), 2) for v in fitted],
        "labels_forecast": future_bulan,
        "values_forecast": [round(last_ma, 2)] * n_periods,
        "model_name":      f"Moving Average (window={w})",
        "metrics":         _metrics(y, fitted),
        "window":          w,
        "last_ma":         round(last_ma, 2),
    }


# ─────────────────────────────────────────────────────────────
# MODEL 4 — EXPONENTIAL SMOOTHING
# ─────────────────────────────────────────────────────────────
def _exp_smoothing_forecast(monthly: pd.DataFrame, n_periods: int, alpha: float = 0.3) -> dict:
    y      = monthly["jumlah"].values
    fitted = [y[0]]
    for i in range(1, len(y)):
        fitted.append(alpha * y[i] + (1 - alpha) * fitted[-1])

    last_smooth  = fitted[-1]
    future_bulan = _next_months(monthly["bulan"].iloc[-1], n_periods)

    return {
        "labels_history":  monthly["bulan"].tolist(),
        "values_history":  monthly["jumlah"].tolist(),
        "values_fitted":   [round(float(v), 2) for v in fitted],
        "labels_forecast": future_bulan,
        "values_forecast": [round(float(last_smooth), 2)] * n_periods,
        "model_name":      f"Exponential Smoothing (α={alpha})",
        "metrics":         _metrics(y, fitted),
        "alpha":           alpha,
    }


# ─────────────────────────────────────────────────────────────
# MAIN — dipanggil dari route
# ─────────────────────────────────────────────────────────────
def run_forecast(
    items,
    n_periods:  int   = 6,
    model_name: str   = "linear",
    filter_obat: str  = None,
    filter_sarana: str = None,
) -> dict:
    """
    Jalankan forecasting pada data distribusi.
    Kembalikan dict lengkap: historis, fitted, forecast, metrics.
    """
    if filter_obat:
        items = [it for it in items if (it.nama_obat_jadi or "").lower() == filter_obat.lower()]
    if filter_sarana:
        items = [it for it in items if (it.jenis_sarana or "").lower() == filter_sarana.lower()]

    monthly = _make_monthly(items)
    if monthly.empty or len(monthly) < 3:
        return {"error": "Data tidak cukup (minimal 3 bulan)", "model_name": model_name}

    if model_name == "polynomial":
        result = _poly_forecast(monthly, n_periods)
    elif model_name == "moving_average":
        result = _ma_forecast(monthly, n_periods)
    elif model_name == "exponential":
        result = _exp_smoothing_forecast(monthly, n_periods)
    else:
        result = _linear_forecast(monthly, n_periods)

    result["n_periods"]    = n_periods
    result["total_months"] = len(monthly)
    result["filter_obat"]  = filter_obat or "Semua Obat"
    result["filter_sarana"] = filter_sarana or "Semua Sarana"
    return result


# ─────────────────────────────────────────────────────────────
# SUMMARY STATS — untuk KPI di halaman forecast
# ─────────────────────────────────────────────────────────────
def get_forecast_summary(items) -> dict:
    """KPI ringkasan: total, rata-rata, tren, growth rate."""
    monthly = _make_monthly(items)
    if monthly.empty:
        return {"total_bulan": 0, "avg_monthly": 0, "max_month": "-", "growth_rate": 0}

    y    = monthly["jumlah"].values
    avg  = float(np.mean(y))
    mx_i = int(np.argmax(y))

    # Growth rate: rata-rata 3 bulan terakhir vs 3 bulan sebelumnya
    if len(y) >= 6:
        recent   = np.mean(y[-3:])
        previous = np.mean(y[-6:-3])
        growth   = round((recent - previous) / (previous + 1e-9) * 100, 2)
    else:
        growth = 0.0

    return {
        "total_bulan":  len(monthly),
        "avg_monthly":  round(avg, 2),
        "max_month":    monthly["bulan"].iloc[mx_i],
        "max_value":    round(float(y[mx_i]), 2),
        "growth_rate":  growth,
        "total_qty":    round(float(np.sum(y)), 2),
    }


# ─────────────────────────────────────────────────────────────
# FORECAST LAMA (backward compat dengan upload_routes)
# ─────────────────────────────────────────────────────────────
def forecasting_distribution(df: pd.DataFrame):
    """Wrapper lama — menerima DataFrame, return (labels, values)."""
    if df.empty or len(df) < 2:
        return [], []

    df = df.copy()
    df["tanggal_penyaluran"] = pd.to_datetime(df["tanggal_penyaluran"], errors="coerce")
    df = df.dropna(subset=["tanggal_penyaluran"])
    if df.empty:
        return [], []

    monthly = (
        df.groupby(df["tanggal_penyaluran"].dt.to_period("M"))["jumlah"]
        .sum()
        .reset_index()
    )
    monthly.columns = ["bulan", "jumlah"]
    monthly["bulan"] = monthly["bulan"].astype(str)
    monthly = monthly.sort_values("bulan").reset_index(drop=True)
    monthly["idx"] = range(len(monthly))

    X = monthly[["idx"]].values
    y = monthly["jumlah"].values
    model = LinearRegression()
    model.fit(X, y)

    future_idx  = len(monthly)
    future_pred = max(0, float(model.predict([[future_idx]])[0]))

    labels = monthly["bulan"].tolist() + ["Prediksi"]
    values = [float(v) for v in monthly["jumlah"].tolist()] + [round(future_pred, 2)]
    return labels, values