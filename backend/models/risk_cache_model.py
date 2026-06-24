"""
backend/models/risk_cache_model.py
────────────────────────────────────
Caches the output of the Risk-Based Inspection service so repeated
tab switches / page revisits return a STABLE result instead of a
different 10000-row sample every time.

Caching strategy (identical to BayesianCache)
──────────────────────────────────────────────
Keyed by the filter combination (tahun, jenis_sarana, kategori).
Alongside the result we store the row_count at computation time.

On every request:
  • Same filter key + same row_count → return cached result instantly.
  • Row count differs (new data uploaded) → recompute + overwrite cache.
  • "Refresh Data" button → force recompute regardless of row_count.
"""

from datetime import datetime
from backend.database.db import db


class RiskCache(db.Model):
    __tablename__ = "risk_cache"

    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # Deterministic key built from sorted filter values
    # e.g. "tahun=2025|jenis_sarana=Apotek|kategori=Antibiotik"
    filter_key = db.Column(db.String(255), unique=True, nullable=False, index=True)

    # COUNT(*) at computation time — change-detector
    row_count  = db.Column(db.Integer, nullable=False, default=0)

    # JSON-serialised get_risk_summary() output
    summary_json = db.Column(db.Text, nullable=True)

    # JSON-serialised calculate_risk() full result list
    data_json    = db.Column(db.Text, nullable=True)

    computed_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"<RiskCache key={self.filter_key!r} "
            f"rows={self.row_count} at={self.computed_at}>"
        )
