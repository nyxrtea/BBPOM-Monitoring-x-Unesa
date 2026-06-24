"""
Bayesian Risk Analysis Blueprint
Route: /bayesian

Caching (mirrors Risk-Based Inspection implementation — see
backend/ml/risk/risk_route.py for the original pattern this follows)
──────────────────────────────────────────────────────────────────────
Results are cached per filter combination (tahun, jenis_sarana, kategori)
in the bayesian_cache table. Cache is only invalidated when the row count
for that filter combo changes. The "Refresh Data" button (if added to
bayesian.html) forces recomputation regardless via /bayesian/api/refresh.

PERUBAHAN dari versi sebelumnya:
  - Ditambahkan layer cache (bayesian_cache_model.BayesianCache) supaya
    hasil tidak dihitung ulang dari nol di setiap request — konsisten
    dengan Risk Inspection dan Forecasting yang sudah pakai cache serupa.
  - _get_data() sekarang menambahkan Distribution.id.desc() sebagai
    secondary sort key, sama seperti fix di risk_route.py, supaya
    potongan LIMIT 5000 deterministik antar pemanggilan (sebelumnya
    hanya order_by created_at.desc() yang bisa ambigu jika banyak baris
    berbagi timestamp yang sama).
  - Import Distribution diseragamkan ke distribution_model_main (re-export
    wrapper) supaya konsisten dengan app.py dan modul lain yang sudah
    dimigrasikan ke pola import yang sama.
"""

import json
from datetime import datetime

from flask import Blueprint, render_template, session, redirect, jsonify, request
from sqlalchemy import func, distinct
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.database.db import db
from backend.models.distribution_model_main import Distribution
from backend.models.bayesian_cache_model import BayesianCache
from backend.ml.bayesian.bayesian_service import (
    calculate_bayesian_risk,
    get_bayesian_summary,
)

bayesian_bp = Blueprint("bayesian", __name__)


def _need_login():
    if "user_id" not in session:
        return redirect("/login")
    return None


# ─────────────────────────────────────────────────────────────
# Filter helpers
# ─────────────────────────────────────────────────────────────
def _filters_from_request() -> dict:
    return {
        "jenis_sarana": request.args.get("jenis_sarana", ""),
        "tahun":        request.args.get("tahun", ""),
        "kategori":     request.args.get("kategori", ""),
    }


def _apply_filters(q, filters: dict):
    if filters.get("jenis_sarana"):
        q = q.filter(Distribution.jenis_sarana.ilike(f"%{filters['jenis_sarana']}%"))
    if filters.get("tahun"):
        q = q.filter(Distribution.tanggal_penyaluran.like(f"{filters['tahun']}%"))
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


def _get_data(filters: dict = None):
    """
    Ambil data dari DB dengan filter opsional, max 5000 record.

    FIX: secondary sort by id.desc() ditambahkan supaya potongan LIMIT
    5000 deterministik antar pemanggilan — sama seperti fix yang sudah
    diterapkan di risk_route.py._get_items().
    """
    q = Distribution.query
    if filters:
        q = _apply_filters(q, filters)
    return (
        q.order_by(Distribution.created_at.desc(), Distribution.id.desc())
        .limit(10000)
        .all()
    )


# ─────────────────────────────────────────────────────────────
# Cache layer (identik dengan pola di risk_route.py)
# ─────────────────────────────────────────────────────────────
def _get_or_compute(filters: dict, force_refresh: bool = False):
    """
    Return (summary, analysis, meta) using the cache when possible.

    meta = {"from_cache": bool, "computed_at": iso-str, "row_count": int}
    """
    key           = _filter_key(filters)
    current_count = _count_matching(filters)

    cache_row = BayesianCache.query.filter_by(filter_key=key).first()

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
    data     = _get_data(filters)
    analysis = calculate_bayesian_risk(data)
    summary  = get_bayesian_summary(analysis)

    now = datetime.utcnow()

    # FIX (race condition / UniqueViolation pada uq_bayesian_cache_filter_key):
    # Versi sebelumnya melakukan SELECT untuk cek apakah baris cache
    # sudah ada, lalu INSERT baru jika tidak ditemukan. Kalau dua
    # request datang hampir bersamaan dengan filter_key yang sama
    # (umum terjadi: browser memanggil /summary dan /data paralel saat
    # halaman dimuat), KEDUANYA bisa melihat "belum ada" di waktu yang
    # sama, lalu keduanya mencoba INSERT — request kedua gagal dengan
    # psycopg2.errors.UniqueViolation karena constraint unique sudah
    # terisi oleh request pertama.
    #
    # Sekarang dipakai UPSERT atomik (INSERT ... ON CONFLICT DO UPDATE)
    # yang dijamin PostgreSQL sendiri sebagai satu operasi tak terpisah
    # (atomic) — tidak ada lagi jendela waktu antara "cek" dan "insert"
    # yang bisa ditabrak request lain.
    stmt = pg_insert(BayesianCache).values(
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
@bayesian_bp.route("/bayesian")
def bayesian():
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
        "bayesian.html",
        tahun_list=tahun_list,
        sarana_list=sarana_list,
        kategori_list=kategori_list,
    )


@bayesian_bp.route("/bayesian/api/summary")
def api_summary():
    g = _need_login()
    if g: return g
    filters = _filters_from_request()
    summary, _, meta = _get_or_compute(filters)
    summary["_meta"] = meta
    return jsonify(summary)


@bayesian_bp.route("/bayesian/api/data")
def api_data():
    g = _need_login()
    if g: return g

    page     = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    mode     = request.args.get("mode", "all")    # all | high | medium | low
    q_search = request.args.get("search", "")
    filters  = _filters_from_request()

    _, analysis, meta = _get_or_compute(filters)

    # Filter by risk level
    if mode == "high":
        analysis = [r for r in analysis if r["risk_level"] == "High Risk"]
    elif mode == "medium":
        analysis = [r for r in analysis if r["risk_level"] == "Medium Risk"]
    elif mode == "low":
        analysis = [r for r in analysis if r["risk_level"] == "Low Risk"]

    # Search
    if q_search:
        ql = q_search.lower()
        analysis = [
            r for r in analysis
            if ql in (r["nama_obat"] or "").lower()
            or ql in (r["tujuan"] or "").lower()
        ]

    # Sort by risk_score desc
    analysis.sort(key=lambda x: x["risk_score"], reverse=True)

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


@bayesian_bp.route("/bayesian/api/refresh", methods=["POST"])
def api_refresh():
    """Force recompute for the current filter combo, bypass cache."""
    g = _need_login()
    if g: return g
    filters = _filters_from_request()
    summary, _, meta = _get_or_compute(filters, force_refresh=True)
    summary["_meta"] = meta
    return jsonify({"success": True, "summary": summary, "meta": meta})


@bayesian_bp.errorhandler(Exception)
def handle_error(e):
    import traceback
    traceback.print_exc()
    return jsonify({"success": False, "error": str(e)}), 500