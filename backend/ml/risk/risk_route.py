"""
Risk-Based Inspection Blueprint
Route: /risk

Caching (mirrors Bayesian Risk implementation)
──────────────────────────────────────────────
Results are cached per filter combination (tahun, jenis_sarana, kategori)
in the risk_cache table. Cache is only invalidated when the row count for
that filter combo changes. The "Refresh Data" button forces recomputation
regardless.

Also fixed: _get_items() now uses Distribution.id.desc() as a secondary
sort key so the 10000-row LIMIT is deterministic across calls.
"""

import json
from datetime import datetime

from flask import Blueprint, render_template, session, redirect, jsonify, request
from sqlalchemy import func, distinct
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.database.db   import db
from backend.models.distribution_model_main import Distribution
from backend.models.risk_cache_model   import RiskCache
from backend.ml.risk.risk_service      import calculate_risk, get_risk_summary

risk_bp = Blueprint("risk", __name__)


def _need_login():
    if "user_id" not in session:
        return redirect("/login")
    return None


# ─────────────────────────────────────────────────────────────
# Filter helpers
# ─────────────────────────────────────────────────────────────
def _filters_from_request() -> dict:
    return {
        "tahun":        request.args.get("tahun", ""),
        "jenis_sarana": request.args.get("jenis_sarana", ""),
        "kategori":     request.args.get("kategori", ""),
    }


def _apply_filters(q, filters: dict):
    if filters.get("tahun"):
        q = q.filter(Distribution.tanggal_penyaluran.like(f"{filters['tahun']}%"))
    if filters.get("jenis_sarana"):
        q = q.filter(Distribution.jenis_sarana.ilike(f"%{filters['jenis_sarana']}%"))
    if filters.get("kategori"):
        q = q.filter(Distribution.kategori_obat.ilike(f"%{filters['kategori']}%"))
    return q


def _filter_key(filters: dict) -> str:
    return (
        f"tahun={filters.get('tahun','')}"
        f"|jenis_sarana={filters.get('jenis_sarana','')}"
        f"|kategori={filters.get('kategori','')}"
    )


def _count_matching(filters: dict) -> int:
    return _apply_filters(Distribution.query, filters).count()


def _get_items(filters: dict):
    """
    Fetch up to 10 000 rows with a deterministic order.
    Secondary sort by id.desc() ensures the same LIMIT slice is returned
    every time even when many rows share the same created_at timestamp
    (the root cause of the 'predictions keep changing' bug).
    """
    q = _apply_filters(Distribution.query, filters)
    return (
        q.order_by(Distribution.created_at.desc(), Distribution.id.desc())
        .limit(10000)
        .all()
    )


# ─────────────────────────────────────────────────────────────
# Cache layer
# ─────────────────────────────────────────────────────────────
def _get_or_compute(filters: dict, force_refresh: bool = False):
    """
    Return (summary, analysis, meta) using the cache when possible.

    meta = {"from_cache": bool, "computed_at": iso-str, "row_count": int}
    """
    key           = _filter_key(filters)
    current_count = _count_matching(filters)

    cache_row = RiskCache.query.filter_by(filter_key=key).first()

    if (
        cache_row is not None
        and cache_row.row_count == current_count
        and not force_refresh
    ):
        summary  = json.loads(cache_row.summary_json)
        analysis = json.loads(cache_row.data_json)
        meta = {
            "from_cache":  True,
            "computed_at": cache_row.computed_at.isoformat(),
            "row_count":   cache_row.row_count,
        }
        return summary, analysis, meta

    # ── Recompute ─────────────────────────────────────────────
    items    = _get_items(filters)
    analysis = calculate_risk(items)
    summary  = get_risk_summary(analysis)

    now = datetime.utcnow()

    # FIX (race condition / UniqueViolation pada uq_risk_cache_filter_key):
    # Pola check-then-insert sebelumnya rentan ditabrak request paralel
    # dengan filter_key yang sama (lihat catatan identik di
    # bayesian_routes.py — bug ini muncul di sana lewat traceback nyata:
    # psycopg2.errors.UniqueViolation saat /api/summary dan /api/data
    # dipanggil hampir bersamaan). Sekarang dipakai UPSERT atomik via
    # INSERT ... ON CONFLICT DO UPDATE, dijamin PostgreSQL sebagai satu
    # operasi tak terpisah — tidak ada lagi jendela race antara cek dan
    # insert.
    stmt = pg_insert(RiskCache).values(
        filter_key=key,
        row_count=current_count,
        summary_json=json.dumps(summary),
        data_json=json.dumps(analysis),
        computed_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["filter_key"],
        set_={
            "row_count":    current_count,
            "summary_json": json.dumps(summary),
            "data_json":    json.dumps(analysis),
            "computed_at":  now,
        },
    )
    db.session.execute(stmt)
    db.session.commit()

    meta = {
        "from_cache":  False,
        "computed_at": now.isoformat(),
        "row_count":   current_count,
    }
    return summary, analysis, meta


# ─────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────
@risk_bp.route("/risk")
def risk():
    g = _need_login()
    if g: return g

    tahun_rows = (
        db.session.query(func.substr(Distribution.tanggal_penyaluran, 1, 4).label("yr"))
        .filter(Distribution.tanggal_penyaluran.isnot(None))
        .distinct().all()
    )
    tahun_list = sorted(
        {r.yr for r in tahun_rows if r.yr and r.yr.isdigit()}, reverse=True
    )
    sarana_list = [
        r[0] for r in
        db.session.query(distinct(Distribution.jenis_sarana))
        .filter(Distribution.jenis_sarana.isnot(None), Distribution.jenis_sarana != "")
        .order_by(Distribution.jenis_sarana).all()
        if r[0]
    ]
    kategori_list = [
        r[0] for r in
        db.session.query(distinct(Distribution.kategori_obat))
        .filter(Distribution.kategori_obat.isnot(None), Distribution.kategori_obat != "")
        .order_by(Distribution.kategori_obat).all()
        if r[0]
    ]

    return render_template(
        "risk.html",
        tahun_list=tahun_list,
        sarana_list=sarana_list,
        kategori_list=kategori_list,
    )


@risk_bp.route("/risk/api/summary")
def api_summary():
    g = _need_login()
    if g: return g
    filters = _filters_from_request()
    summary, _, meta = _get_or_compute(filters)
    summary["_meta"] = meta
    return jsonify(summary)


@risk_bp.route("/risk/api/data")
def api_data():
    g = _need_login()
    if g: return g

    page     = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    mode     = request.args.get("mode", "all")
    search   = request.args.get("search", "")
    filters  = _filters_from_request()

    _, analysis, meta = _get_or_compute(filters)

    if mode == "high":
        analysis = [r for r in analysis if r["risk_level"] == "HIGH"]
    elif mode == "medium":
        analysis = [r for r in analysis if r["risk_level"] == "MEDIUM"]
    elif mode == "low":
        analysis = [r for r in analysis if r["risk_level"] == "LOW"]

    if search:
        ql = search.lower()
        analysis = [
            r for r in analysis
            if ql in (r["nama_obat_jadi"] or "").lower()
            or ql in (r["tujuan_penyaluran"] or "").lower()
            or ql in (r["jenis_sarana"] or "").lower()
        ]

    total  = len(analysis)
    offset = (page - 1) * per_page
    paged  = analysis[offset: offset + per_page]

    return jsonify({
        "data":  paged,
        "total": total,
        "page":  page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "_meta": meta,
    })


@risk_bp.route("/risk/api/refresh", methods=["POST"])
def api_refresh():
    """Force recompute for the current filter combo, bypass cache."""
    g = _need_login()
    if g: return g
    filters = _filters_from_request()
    summary, _, meta = _get_or_compute(filters, force_refresh=True)
    summary["_meta"] = meta
    return jsonify({"success": True, "summary": summary, "meta": meta})


@risk_bp.errorhandler(Exception)
def handle_error(e):
    import traceback
    traceback.print_exc()
    return jsonify({"success": False, "error": str(e)}), 500