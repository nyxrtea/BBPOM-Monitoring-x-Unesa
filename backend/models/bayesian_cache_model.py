"""
backend/models/bayesian_cache_model.py
─────────────────────────────────────────
Caches the output of the Bayesian risk analysis so repeated tab
switches / page revisits show a STABLE result instead of a different
random 5000-row sample every time (the previous instability came from
an unordered/tie-broken SQL query, not from the Bayesian math itself).

Cache invalidation strategy
────────────────────────────
Each row is keyed by the exact filter combination used (tahun,
jenis_sarana, kategori). Alongside the cached result we store the
row_count — the COUNT(*) of distribution rows matching those same
filters at computation time.

On every request:
  • If a cache row exists for this filter combo AND its stored
    row_count still equals the CURRENT row_count → return the cached
    result untouched (fast, stable, no recomputation).
  • If row_count differs (new data was uploaded/added) → recompute
    and overwrite the cache.
  • The "Refresh Data" button bypasses this check entirely and always
    recomputes, regardless of row_count.

This keeps predictions flexible per filter selection (each filter
combo gets its own independent cache slot) while remaining stable
within a single combo until the underlying data actually changes.
"""

from datetime import datetime
from backend.database.db import db


class BayesianCache(db.Model):
    __tablename__ = "bayesian_cache"

    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # Deterministic string built from sorted filter values, e.g.
    # "tahun=2025|jenis_sarana=Apotek|kategori=Antibiotik"
    filter_key = db.Column(db.String(255), unique=True, nullable=False, index=True)

    # COUNT(*) of distribution rows matching this filter combo at the
    # time of computation — used as the change-detector.
    row_count  = db.Column(db.Integer, nullable=False, default=0)

    # JSON-serialised get_bayesian_summary() output
    summary_json = db.Column(db.Text, nullable=True)

    # JSON-serialised calculate_bayesian_risk() full result list
    data_json    = db.Column(db.Text, nullable=True)

    computed_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"<BayesianCache key={self.filter_key!r} "
            f"rows={self.row_count} at={self.computed_at}>"
        )
