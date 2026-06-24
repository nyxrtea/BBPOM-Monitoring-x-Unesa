"""
backend/models/distribution_model_main.py
──────────────────────────────────────────
Re-exports Distribution from distribution_model so that both
  app.py  and  upload_routes.py  can keep their existing imports.

Do NOT put model logic here — edit distribution_model.py instead.
"""

from backend.models.distribution_model import Distribution  # noqa: F401

__all__ = ["Distribution"]
