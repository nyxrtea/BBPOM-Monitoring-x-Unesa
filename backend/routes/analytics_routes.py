from flask import Blueprint, jsonify, render_template, request, Response, session, redirect
import pandas as pd
import io
from datetime import datetime

from backend.services.analytics_service import (
    get_filter_options,
    get_tren, get_detail_bulan,
    get_peta, get_obat,
    get_produsen, get_produsen_expired,
    get_pbf, search_pbf,
    get_sarana, search_sarana,
    get_sarana_detail, get_obat_transaksi,
    get_export_df, KOLOM_TERSEDIA,
    get_kpi, get_top_provinsi, get_top_kota, get_top_pbf,
)

analytics_bp = Blueprint("analytics", __name__, url_prefix="/analytics")


def _need_login():
    if "user_id" not in session:
        return redirect("/login")
    return None


def _filters():
    """Ambil semua filter dari query string — dipakai di semua endpoint."""
    return {
        "tahun":        request.args.get("tahun",        "").strip(),
        "provinsi":     request.args.get("provinsi",     "").strip(),
        "kota":         request.args.get("kota",         "").strip(),
        "kategori":     request.args.get("kategori",     "").strip(),
        "jenis_sarana": request.args.get("jenis_sarana", "").strip(),
        "search":       request.args.get("search",       "").strip(),
    }


# ── HALAMAN UTAMA ─────────────────────────────────────────────
@analytics_bp.route("/")
def analytics_page():
    g = _need_login()
    if g: return g
    opts = get_filter_options()
    return render_template(
        "analytics.html",
        filters=_filters(),
        kolom_tersedia=KOLOM_TERSEDIA,
        **opts,
    )


# ── TAB 1: TREN ───────────────────────────────────────────────
@analytics_bp.route("/api/tren")
def api_tren():
    g = _need_login()
    if g: return g
    return jsonify(get_tren(_filters()))


@analytics_bp.route("/api/tren/detail")
def api_tren_detail():
    g = _need_login()
    if g: return g
    bulan = request.args.get("bulan", "").strip()
    if not bulan:
        return jsonify({"error": "Parameter bulan wajib diisi"}), 400
    return jsonify(get_detail_bulan(_filters(), bulan))


# ── TAB 2: PETA ───────────────────────────────────────────────
@analytics_bp.route("/api/peta")
def api_peta():
    g = _need_login()
    if g: return g
    return jsonify(get_peta(_filters()))


# ── TAB 3: OBAT ───────────────────────────────────────────────
@analytics_bp.route("/api/obat")
def api_obat():
    g = _need_login()
    if g: return g
    return jsonify(get_obat(_filters()))


# ── TAB 4: PRODUSEN ───────────────────────────────────────────
@analytics_bp.route("/api/produsen")
def api_produsen():
    g = _need_login()
    if g: return g
    return jsonify(get_produsen(_filters()))


@analytics_bp.route("/api/produsen/expired")
def api_produsen_expired():
    g = _need_login()
    if g: return g
    nama = request.args.get("nama", "").strip()
    data = get_produsen_expired(_filters(), nama) if nama else []
    return jsonify({"data": data, "produsen": nama})


# ── TAB 5: PBF ────────────────────────────────────────────────
@analytics_bp.route("/api/pbf")
def api_pbf():
    g = _need_login()
    if g: return g
    return jsonify(get_pbf(_filters()))


@analytics_bp.route("/api/pbf/search")
def api_pbf_search():
    g = _need_login()
    if g: return g
    q = request.args.get("q", "").strip()
    return jsonify({"results": search_pbf(_filters(), q), "query": q})


# ── TAB 6: SARANA ─────────────────────────────────────────────
@analytics_bp.route("/api/sarana")
def api_sarana():
    g = _need_login()
    if g: return g
    return jsonify(get_sarana(_filters()))


# ── TAB 7: PENCARIAN SARANA ───────────────────────────────────
@analytics_bp.route("/api/sarana/search")
def api_sarana_search():
    g = _need_login()
    if g: return g
    q = request.args.get("q", "").strip()
    return jsonify(search_sarana(_filters(), q))


@analytics_bp.route("/api/sarana/detail")
def api_sarana_detail():
    g = _need_login()
    if g: return g
    apotek = request.args.get("apotek", "").strip()
    if not apotek:
        return jsonify({"error": "Parameter apotek wajib diisi"}), 400
    return jsonify(get_sarana_detail(_filters(), apotek))


@analytics_bp.route("/api/sarana/transaksi")
def api_sarana_transaksi():
    g = _need_login()
    if g: return g
    apotek = request.args.get("apotek", "").strip()
    obat   = request.args.get("obat",   "").strip()
    if not apotek or not obat:
        return jsonify({"error": "Parameter apotek dan obat wajib diisi"}), 400
    return jsonify(get_obat_transaksi(_filters(), apotek, obat))


# ── TAB 8: EXPORT ─────────────────────────────────────────────
@analytics_bp.route("/api/export/preview")
def api_export_preview():
    g = _need_login()
    if g: return g
    selected  = request.args.getlist("cols") or KOLOM_TERSEDIA
    mode      = request.args.get("mode", "cleaned")
    n_terbaru = int(request.args.get("n", 1000))
    data, cols = get_export_df(_filters(), selected, mode, n_terbaru)
    preview = [{k: (v if v is not None else "") for k, v in row.items()} for row in data[:200]]
    return jsonify({"rows": preview, "columns": cols, "total": len(data)})


@analytics_bp.route("/export/csv")
def export_csv():
    g = _need_login()
    if g: return g
    selected  = request.args.getlist("cols") or KOLOM_TERSEDIA
    mode      = request.args.get("mode", "cleaned")
    n_terbaru = int(request.args.get("n", 1000))
    data, cols = get_export_df(_filters(), selected, mode, n_terbaru)
    if not data:
        return Response("Tidak ada data", status=400)
    df  = pd.DataFrame(data, columns=cols)
    buf = io.StringIO()
    df.to_csv(buf, index=False, encoding="utf-8-sig")
    fname = f"bpom_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        buf.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ── ENDPOINT LAMA (backward compat) ───────────────────────────
@analytics_bp.route("/kpi")
def kpi():
    g = _need_login()
    if g: return g
    return jsonify(get_kpi())


@analytics_bp.route("/top-provinsi")
def top_provinsi():
    g = _need_login()
    if g: return g
    return jsonify(get_top_provinsi())


@analytics_bp.route("/top-kota")
def top_kota():
    g = _need_login()
    if g: return g
    return jsonify(get_top_kota())


@analytics_bp.route("/top-pbf")
def top_pbf():
    g = _need_login()
    if g: return g
    return jsonify(get_top_pbf())


@analytics_bp.errorhandler(Exception)
def handle_error(e):
    import traceback
    traceback.print_exc()
    return jsonify({"success": False, "error": str(e)}), 500