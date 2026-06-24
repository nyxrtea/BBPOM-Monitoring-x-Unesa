"""
Forecasting Blueprint  —  Route: /forecast

Cache: each run_forecast() result is stored in forecast_cache per (model, filters).
Invalidated when row count changes. /forecast/api/refresh forces recompute.

/forecast/api/compare  runs all 4 models in one request (was 4 separate fetch()
calls in the old frontend — that endpoint simply did not exist, which is why the
Perbandingan Model page showed HTTP 200 but rendered nothing).
"""

import json
from datetime import datetime

from flask import Blueprint, render_template, session, redirect, jsonify, request
from sqlalchemy import func, distinct
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.database.db   import db
from backend.models.distribution_model_main import Distribution
from backend.models.forecast_cache_model import ForecastCache
from backend.ml.forecasting.forecasting_service import (
    run_forecast, get_forecast_summary,
)

forecast_bp = Blueprint("forecast", __name__)


def _need_login():
    if "user_id" not in session:
        return redirect("/login")
    return None


def _apply_filters(q, filters):
    if filters.get("tahun"):
        q = q.filter(Distribution.tanggal_penyaluran.like(f"{filters['tahun']}%"))
    return q


def _all_items(filters):
    return _apply_filters(Distribution.query, filters).all()


def _row_count(filters):
    return _apply_filters(Distribution.query, filters).count()


def _filter_key(model_name, filters, obat, sarana, n_periods):
    return (f"model={model_name}|tahun={filters.get('tahun','')}|"
            f"obat={obat or ''}|sarana={sarana or ''}|n_periods={n_periods}")


def _get_or_compute(model_name, filters, obat, sarana, n_periods, force=False):
    key     = _filter_key(model_name, filters, obat, sarana, n_periods)
    cur_cnt = _row_count(filters)
    row     = ForecastCache.query.filter_by(filter_key=key).first()

    if row and row.row_count == cur_cnt and not force:
        result = json.loads(row.result_json)
        result["_meta"] = {"from_cache": True,
                           "computed_at": row.computed_at.isoformat(),
                           "row_count":   row.row_count}
        return result

    items  = _all_items(filters)
    result = run_forecast(items, n_periods=n_periods, model_name=model_name,
                          filter_obat=obat or None, filter_sarana=sarana or None)
    now = datetime.utcnow()

    # FIX (race condition / UniqueViolation pada uq_forecast_cache_filter_key):
    # Sama seperti bayesian_routes.py dan risk_route.py — pola
    # check-then-insert sebelumnya bisa ditabrak request paralel
    # dengan filter_key sama, apalagi endpoint /forecast/api/compare
    # menjalankan 4 model dalam satu siklus request, memperbesar
    # kemungkinan tabrakan. Diganti UPSERT atomik via
    # INSERT ... ON CONFLICT DO UPDATE.
    stmt = pg_insert(ForecastCache).values(
        filter_key=key,
        row_count=cur_cnt,
        result_json=json.dumps(result),
        computed_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["filter_key"],
        set_={
            "row_count":   cur_cnt,
            "result_json": json.dumps(result),
            "computed_at": now,
        },
    )
    db.session.execute(stmt)
    db.session.commit()

    result["_meta"] = {"from_cache": False,
                       "computed_at": now.isoformat(),
                       "row_count":   cur_cnt}
    return result


def _params():
    return {
        "filters":    {"tahun": request.args.get("tahun", "")},
        "model_name": request.args.get("model", "linear"),
        "n_periods":  int(request.args.get("n_periods", 6)),
        "obat":       request.args.get("obat", ""),
        "sarana":     request.args.get("filter_sarana", ""),
    }


@forecast_bp.route("/forecast")
def forecast():
    g = _need_login()
    if g: return g
    tahun_rows = (db.session.query(func.substr(Distribution.tanggal_penyaluran,1,4).label("yr"))
                  .filter(Distribution.tanggal_penyaluran.isnot(None)).distinct().all())
    tahun_list = sorted({r.yr for r in tahun_rows if r.yr and r.yr.isdigit()}, reverse=True)
    obat_list  = [r[0] for r in
                  db.session.query(distinct(Distribution.nama_obat_jadi))
                  .filter(Distribution.nama_obat_jadi.isnot(None), Distribution.nama_obat_jadi != "")
                  .order_by(Distribution.nama_obat_jadi).limit(200).all() if r[0]]
    sarana_list = [r[0] for r in
                   db.session.query(distinct(Distribution.jenis_sarana))
                   .filter(Distribution.jenis_sarana.isnot(None), Distribution.jenis_sarana != "")
                   .order_by(Distribution.jenis_sarana).all() if r[0]]
    return render_template("forecast.html", tahun_list=tahun_list,
                           obat_list=obat_list, sarana_list=sarana_list,
                           labels=[], values=[])


@forecast_bp.route("/forecast/api/run")
def api_run():
    g = _need_login()
    if g: return g
    p = _params()
    return jsonify(_get_or_compute(p["model_name"], p["filters"],
                                   p["obat"], p["sarana"], p["n_periods"]))


@forecast_bp.route("/forecast/api/compare")
def api_compare():
    """Run all 4 models — one round-trip, each model independently cached."""
    g = _need_login()
    if g: return g
    p = _params()
    models  = ["linear", "polynomial", "moving_average", "exponential"]
    results = {}
    for m in models:
        try:
            results[m] = _get_or_compute(m, p["filters"],
                                         p["obat"], p["sarana"], p["n_periods"])
        except Exception as exc:
            results[m] = {"error": str(exc), "model_name": m}
    return jsonify(results)


@forecast_bp.route("/forecast/api/refresh", methods=["POST"])
def api_refresh():
    g = _need_login()
    if g: return g
    p = _params()
    r = _get_or_compute(p["model_name"], p["filters"],
                        p["obat"], p["sarana"], p["n_periods"], force=True)
    return jsonify({"success": True, "result": r, "meta": r.get("_meta", {})})


@forecast_bp.route("/forecast/api/summary")
def api_summary():
    g = _need_login()
    if g: return g
    items = _all_items({"tahun": request.args.get("tahun", "")})
    return jsonify(get_forecast_summary(items))


@forecast_bp.route("/forecast/api/top-obat")
def api_top_obat():
    g = _need_login()
    if g: return g
    rows = (db.session.query(Distribution.nama_obat_jadi,
                             func.sum(Distribution.jumlah).label("total"))
            .filter(Distribution.nama_obat_jadi.isnot(None))
            .group_by(Distribution.nama_obat_jadi)
            .order_by(func.sum(Distribution.jumlah).desc()).limit(20).all())
    return jsonify([{"obat": r[0], "total": float(r[1] or 0)} for r in rows])


@forecast_bp.errorhandler(Exception)
def handle_error(e):
    import traceback; traceback.print_exc()
    return jsonify({"success": False, "error": str(e)}), 500