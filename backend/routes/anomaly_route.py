"""
Anomaly Detection Blueprint
Route: /anomaly
"""

from flask import Blueprint, render_template, session, redirect, jsonify, request
from backend.database.db import db
from backend.models.distribution_model import Distribution
from backend.ml.anomaly.anomaly_service import (
    run_anomaly_detection,
    get_anomaly_summary,
    update_anomaly_labels,
)

anomaly_bp = Blueprint("anomaly", __name__)


def _need_login():
    if "user_id" not in session:
        return redirect("/login")
    return None


# ─── HALAMAN UTAMA ────────────────────────────────────────────
@anomaly_bp.route("/anomaly")
def anomaly():
    g = _need_login()
    if g: return g

    summary = get_anomaly_summary()
    return render_template("anomaly.html", summary=summary)


# ─── API: DATA ANOMALI (paginasi + filter) ────────────────────
@anomaly_bp.route("/anomaly/api/data")
def api_data():
    g = _need_login()
    if g: return g

    mode     = request.args.get("mode", "all")    # all | anomaly | normal
    page     = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    q_sarana = request.args.get("jenis_sarana", "")
    q_search = request.args.get("search", "")

    query = Distribution.query
    if mode == "anomaly":
        query = query.filter(Distribution.anomaly_label == -1)
    elif mode == "normal":
        query = query.filter(Distribution.anomaly_label == 1)

    if q_sarana:
        query = query.filter(Distribution.jenis_sarana.ilike(f"%{q_sarana}%"))
    if q_search:
        query = query.filter(
            db.or_(
                Distribution.nama_obat_jadi.ilike(f"%{q_search}%"),
                Distribution.tujuan_penyaluran.ilike(f"%{q_search}%"),
            )
        )

    total    = query.count()
    items    = query.order_by(Distribution.created_at.desc()) \
                    .offset((page - 1) * per_page).limit(per_page).all()

    rows = []
    for it in items:
        rows.append({
            "id":                   it.id,
            "tujuan_penyaluran":    it.tujuan_penyaluran    or "-",
            "nama_obat_jadi":       it.nama_obat_jadi       or "-",
            "nama_kota_kab_tujuan": it.nama_kota_kab_tujuan or "-",
            "jumlah":               float(it.jumlah or 0),
            "jenis_sarana":         it.jenis_sarana         or "-",
            "tanggal_penyaluran":   it.tanggal_penyaluran   or "-",
            "anomaly_label":        it.anomaly_label,
            "anomaly_reason":       it.anomaly_reason or "Normal",
        })

    return jsonify({
        "data":  rows,
        "total": total,
        "page":  page,
        "pages": (total + per_page - 1) // per_page,
    })


# ─── API: SUMMARY / KPI ───────────────────────────────────────
@anomaly_bp.route("/anomaly/api/summary")
def api_summary():
    g = _need_login()
    if g: return g
    return jsonify(get_anomaly_summary())


# ─── API: JALANKAN ULANG DETEKSI ─────────────────────────────
@anomaly_bp.route("/anomaly/run", methods=["POST"])
def run_detection():
    g = _need_login()
    if g: return g
    use_ml = request.json.get("use_ml", True) if request.is_json else True
    result = update_anomaly_labels(use_ml=use_ml)
    return jsonify({"success": True, **result})


# ─── ERROR HANDLER ────────────────────────────────────────────
@anomaly_bp.errorhandler(Exception)
def handle_error(e):
    import traceback
    traceback.print_exc()
    return jsonify({"success": False, "error": str(e)}), 500