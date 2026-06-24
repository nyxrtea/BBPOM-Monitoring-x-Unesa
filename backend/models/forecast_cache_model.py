"""
backend/models/forecast_cache_model.py
────────────────────────────────────────
Caches each forecast run result keyed by model + filter combo.
Invalidated when distribution row count changes, same pattern as
BayesianCache and RiskCache.
"""

from datetime import datetime
from backend.database.db import db


class ForecastCache(db.Model):
    __tablename__ = "forecast_cache"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # e.g. "model=linear|tahun=2025|obat=|sarana=|n_periods=6"
    filter_key  = db.Column(db.String(512), unique=True, nullable=False, index=True)
    row_count   = db.Column(db.Integer, nullable=False, default=0)
    result_json = db.Column(db.Text, nullable=True)
    computed_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<ForecastCache key={self.filter_key!r} rows={self.row_count}>"
