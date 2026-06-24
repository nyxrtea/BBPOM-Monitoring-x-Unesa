"""
backend/routes/map_routes.py
─────────────────────────────
GET  /map                  → Kab/Kota choropleth (distribution data embedded as JSON)
GET  /api/geojson/kota     → Serve MERGED kab/kota GeoJSON (grouped by WADMKK)
GET  /api/geojson          → Serve id-all.geo.json (province polygons, kept for compat)

Merge logic mirrors the Streamlit/Folium prototype's build_kab_geojson():
every raw feature sharing the same WADMKK value is combined into a single
MultiPolygon feature, so kab/kota with multiple source polygons render as
one shape. (In the current source file each WADMKK has exactly one polygon,
so the merge is a pass-through — but the same code works unmodified if a
more granular per-village file is swapped in later.)
"""

import json
import os
from collections import defaultdict

from flask import Blueprint, render_template, session, redirect, jsonify, send_file
from sqlalchemy import func, case

from backend.database.db import db
from backend.models.distribution_model_main import Distribution

map_bp = Blueprint("map", __name__)

_DATA_DIR     = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_GEOJSON_KAB  = os.path.join(_DATA_DIR, "id-kab.geo.json")
_GEOJSON_PROV = os.path.join(_DATA_DIR, "id-all.geo.json")

# Module-level cache — merge runs once per process, not per request.
_MERGED_KAB_CACHE: dict | None = None


# ─────────────────────────────────────────────────────────────
# Merge helper (same approach as the Folium prototype)
# ─────────────────────────────────────────────────────────────
def _merge_by_wadmkk(raw_geojson: dict) -> dict:
    """
    Group all features sharing the same WADMKK (kab/kota name) and combine
    their coordinates into one MultiPolygon feature per kab/kota.
    """
    geo_kab = defaultdict(list)
    for feat in raw_geojson.get("features", []):
        nama = feat.get("properties", {}).get("WADMKK")
        geom = feat.get("geometry")
        if not nama or not geom or not geom.get("coordinates"):
            continue
        geo_kab[nama].append(feat)

    features_out = []
    for kab, feats in geo_kab.items():
        all_coords = []
        for f in feats:
            geom   = f["geometry"]
            gtype  = geom["type"]
            coords = geom["coordinates"]
            if gtype == "Polygon":
                all_coords.append(coords)
            elif gtype == "MultiPolygon":
                all_coords.extend(coords)
        if not all_coords:
            continue
        features_out.append({
            "type": "Feature",
            "properties": {"WADMKK": kab},
            "geometry": {"type": "MultiPolygon", "coordinates": all_coords},
        })

    return {"type": "FeatureCollection", "features": features_out}


def _get_merged_kab_geojson() -> dict:
    """Load + merge once, then serve from memory on subsequent requests."""
    global _MERGED_KAB_CACHE
    if _MERGED_KAB_CACHE is not None:
        return _MERGED_KAB_CACHE

    with open(_GEOJSON_KAB, "r", encoding="utf-8") as f:
        raw = json.load(f)

    _MERGED_KAB_CACHE = _merge_by_wadmkk(raw)
    print(
        f"[INFO] Kab/kota GeoJSON merged: "
        f"{len(raw.get('features', []))} raw → "
        f"{len(_MERGED_KAB_CACHE['features'])} merged features."
    )
    return _MERGED_KAB_CACHE


# ─────────────────────────────────────────────────────────────
# MAP PAGE
# ─────────────────────────────────────────────────────────────
@map_bp.route("/map")
def map_view():
    if "user_id" not in session:
        return redirect("/login")

    # ── Total + count per kab/kota ───────────────────────────
    rows = (
        db.session.query(
            Distribution.nama_kota_kab_tujuan,
            Distribution.nama_provinsi_tujuan,
            func.coalesce(func.sum(Distribution.jumlah), 0).label("total"),
            func.count(Distribution.id).label("cnt"),
        )
        .filter(
            Distribution.nama_kota_kab_tujuan.isnot(None),
            Distribution.nama_kota_kab_tujuan != "",
            Distribution.nama_kota_kab_tujuan != "nan",
        )
        .group_by(
            Distribution.nama_kota_kab_tujuan,
            Distribution.nama_provinsi_tujuan,
        )
        .order_by(func.sum(Distribution.jumlah).desc())
        .all()
    )

    kota_map: dict = {}
    for row in rows:
        key = (row.nama_kota_kab_tujuan or "").strip().upper()
        if not key:
            continue
        if key in kota_map:
            kota_map[key]["total"] += float(row.total or 0)
            kota_map[key]["count"] += row.cnt
        else:
            kota_map[key] = {
                "total":       float(row.total or 0),
                "count":       row.cnt,
                "provinsi":    (row.nama_provinsi_tujuan or "").strip().upper(),
                "top_kategori": [],
                # Risk fields — populated below from anomaly_label
                "n_anomaly":   0,
                "pct_anomaly": 0.0,
                "risk_level":  "LOW",   # default until anomaly data arrives
                # Volume-tier fields — populated below from total distribution
                "dist_level":  "ringan",  # default until percentile pass runs
            }

    # ── Volume tier (dist_level) per kab/kota ─────────────────
    # FIX: sebelumnya sidebar_list membaca v["dist_level"] padahal
    # key ini TIDAK PERNAH di-set ke kota_map manapun — hanya
    # "risk_level" yang di-assign. Ini menyebabkan KeyError saat
    # /map diakses. Sekarang dihitung dari percentile total volume,
    # konsisten dengan logika get_peta() di analytics_service.py
    # (binning 33%/67%) dan dipakai oleh map_visualization.js untuk
    # mewarnai choropleth (RINGAN/SEDANG/BERAT).
    _totals = [v["total"] for v in kota_map.values()]
    if _totals:
        _ts  = sorted(_totals)
        _n   = len(_ts)
        _p33 = _ts[int(_n * 0.33)] if _n > 2 else (_ts[0]  if _ts else 0)
        _p67 = _ts[int(_n * 0.67)] if _n > 2 else (_ts[-1] if _ts else 0)
        for v in kota_map.values():
            if v["total"] >= _p67:
                v["dist_level"] = "berat"
            elif v["total"] >= _p33:
                v["dist_level"] = "sedang"
            else:
                v["dist_level"] = "ringan"

    # ── Risk level per kab/kota from anomaly_label ────────────
    # Classify each kab/kota using the same thresholds as the
    # "Distribusi Level Risiko" donut in risk.html:
    #   pct_anomaly >= 40% → HIGH  (red   #e02424)
    #   pct_anomaly >= 15% → MEDIUM(orange #ff8a4c)
    #   pct_anomaly <  15% → LOW   (green  #0e9f6e)
    # This matches the colour legend on the Risk-Based Inspection page.
    anomaly_rows = (
        db.session.query(
            Distribution.nama_kota_kab_tujuan,
            func.count(Distribution.id).label("total_cnt"),
            func.sum(
                case((Distribution.anomaly_label == -1, 1), else_=0)
            ).label("anomaly_cnt"),
        )
        .filter(
            Distribution.nama_kota_kab_tujuan.isnot(None),
            Distribution.nama_kota_kab_tujuan != "",
            Distribution.nama_kota_kab_tujuan != "nan",
        )
        .group_by(Distribution.nama_kota_kab_tujuan)
        .all()
    )

    for arow in anomaly_rows:
        key   = (arow.nama_kota_kab_tujuan or "").strip().upper()
        if key not in kota_map:
            continue
        total_cnt   = int(arow.total_cnt   or 0)
        anomaly_cnt = int(arow.anomaly_cnt or 0)
        pct = (anomaly_cnt / total_cnt * 100) if total_cnt else 0.0
        level = "HIGH" if pct >= 40 else "MEDIUM" if pct >= 15 else "LOW"
        kota_map[key]["n_anomaly"]   = anomaly_cnt
        kota_map[key]["pct_anomaly"] = round(pct, 1)
        kota_map[key]["risk_level"]  = level

    # ── Top-5 kategori_obat per kab/kota (for popup breakdown) ─
    cat_rows = (
        db.session.query(
            Distribution.nama_kota_kab_tujuan,
            Distribution.kategori_obat,
            func.coalesce(func.sum(Distribution.jumlah), 0).label("total"),
        )
        .filter(
            Distribution.nama_kota_kab_tujuan.isnot(None),
            Distribution.nama_kota_kab_tujuan != "",
        )
        .group_by(
            Distribution.nama_kota_kab_tujuan,
            Distribution.kategori_obat,
        )
        .all()
    )

    cat_by_kota: dict = defaultdict(list)
    for r in cat_rows:
        key = (r.nama_kota_kab_tujuan or "").strip().upper()
        if not key:
            continue
        cat_by_kota[key].append({
            "label":  r.kategori_obat or "Lainnya",
            "jumlah": float(r.total or 0),
        })

    for key, cats in cat_by_kota.items():
        if key in kota_map:
            kota_map[key]["top_kategori"] = sorted(
                cats, key=lambda x: -x["jumlah"]
            )[:5]

    # ── Sidebar list — top 25 sorted by total ─────────────────
    sidebar_list = sorted(
        [
            {
                "kota":       k,
                "provinsi":   v["provinsi"],
                "total":      v["total"],
                "count":      v["count"],
                "dist_level": v["dist_level"],
            }
            for k, v in kota_map.items()
        ],
        key=lambda x: -x["total"],
    )[:25]

    total_all  = sum(v["total"] for v in kota_map.values())
    kota_count = len(kota_map)

    return render_template(
        "map_visualization.html",
        kota_map   = json.dumps(kota_map,    ensure_ascii=False),
        kota_list  = json.dumps(sidebar_list, ensure_ascii=False),
        total_all  = total_all,
        kota_count = kota_count,
    )


# ─────────────────────────────────────────────────────────────
# GEOJSON ENDPOINTS
# ─────────────────────────────────────────────────────────────
@map_bp.route("/api/geojson/kota")
def geojson_kota():
    """Merged kab/kota polygons — WADMKK property = kab/kota name."""
    if not os.path.exists(_GEOJSON_KAB):
        return jsonify({"error": "Kab/kota GeoJSON not found"}), 404
    return jsonify(_get_merged_kab_geojson())


@map_bp.route("/api/geojson")
def geojson_prov():
    """Province polygons — kept for backward compatibility."""
    if not os.path.exists(_GEOJSON_PROV):
        return jsonify({"error": "Province GeoJSON not found"}), 404
    return send_file(_GEOJSON_PROV, mimetype="application/geo+json")