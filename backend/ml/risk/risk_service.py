"""
Risk-Based Inspection Service
==============================
Sistem penilaian risiko distribusi obat berbasis 6 dimensi:
  1. Volume Risk     — jumlah distribusi vs threshold
  2. Anomaly Risk    — hasil deteksi anomali
  3. Expiry Risk     — jarak dengan tanggal kadaluwarsa
  4. Address Risk    — kelengkapan data alamat & faktur
  5. Sarana Risk     — jenis fasilitas distribusi
  6. Cross-Region    — distribusi lintas provinsi

Cocok sebagai referensi mata kuliah:
  - Manajemen Risiko
  - Sistem Pendukung Keputusan
  - Data Science & Analytics
  - Rekayasa Perangkat Lunak
"""

import numpy  as np
import pandas as pd
from datetime import datetime, timedelta

from sqlalchemy import func, distinct
from backend.database.db   import db
from backend.models.distribution_model import Distribution


# ─────────────────────────────────────────────────────────────
# BOBOT DIMENSI RISIKO (total = 100)
# ─────────────────────────────────────────────────────────────
WEIGHTS = {
    "volume":      25,    # jumlah distribusi
    "anomaly":     25,    # label anomali
    "expiry":      20,    # kadaluwarsa
    "address":     15,    # kelengkapan data
    "sarana":      10,    # jenis sarana
    "cross_region": 5,    # lintas provinsi
}

# Threshold volume
VOL_LOW    = 1_000
VOL_MEDIUM = 10_000
VOL_HIGH   = 100_000
VOL_CRIT   = 500_000

# Sarana berisiko lebih rendah karena terdaftar & terstandar
SARANA_LOW_RISK  = {"rumah sakit", "puskesmas", "instalasi farmasi"}
SARANA_HIGH_RISK = {"", "-", "nan", "lainnya", "tidak resmi", "perorangan"}


# ─────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────
def _f(v):
    try:    return float(v or 0)
    except: return 0.0

def _s(v):
    s = str(v or "").strip()
    return "" if s.lower() in ("nan", "none", "null") else s

def _days_exp(exp_str: str):
    if not exp_str:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return (datetime.strptime(exp_str.strip(), fmt) - datetime.today()).days
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────────────────────
# SCORE SETIAP DIMENSI (0–100)
# ─────────────────────────────────────────────────────────────
def _score_volume(jumlah: float) -> tuple[int, str]:
    if jumlah >= VOL_CRIT:   return 100, f"Volume kritis (≥{VOL_CRIT:,.0f})"
    if jumlah >= VOL_HIGH:   return 75,  f"Volume sangat besar (≥{VOL_HIGH:,.0f})"
    if jumlah >= VOL_MEDIUM: return 50,  f"Volume besar (≥{VOL_MEDIUM:,.0f})"
    if jumlah >= VOL_LOW:    return 25,  f"Volume sedang (≥{VOL_LOW:,.0f})"
    if jumlah <= 0:          return 80,  "Jumlah tidak valid (≤0)"
    return 0, ""


def _score_anomaly(anomaly_label) -> tuple[int, str]:
    if anomaly_label == -1:
        return 100, "Terdeteksi sebagai anomali"
    return 0, ""


def _score_expiry(exp_str: str) -> tuple[int, str]:
    days = _days_exp(exp_str)
    if days is None:       return 30, "Tanggal kadaluwarsa tidak ada"
    if days < 0:           return 100, f"Sudah kadaluwarsa ({abs(int(days))} hari lalu)"
    if days <= 30:         return 90, f"Kadaluwarsa dalam {int(days)} hari"
    if days <= 90:         return 60, f"Hampir kadaluwarsa ({int(days)} hari)"
    if days <= 180:        return 30, f"Kadaluwarsa dalam {int(days)} hari"
    return 0, ""


def _score_address(alamat: str, tujuan: str, no_faktur: str) -> tuple[int, str]:
    issues = []
    score  = 0
    if not alamat:    score += 50; issues.append("Alamat kosong")
    if not tujuan:    score += 30; issues.append("Tujuan kosong")
    if not no_faktur: score += 20; issues.append("No. faktur kosong")
    return min(score, 100), ", ".join(issues)


def _score_sarana(jenis_sarana: str) -> tuple[int, str]:
    s = jenis_sarana.lower()
    if s in SARANA_HIGH_RISK: return 80, "Jenis sarana tidak dikenal"
    if "apotek"   in s:       return 20, ""
    if "klinik"   in s:       return 30, ""
    if s in SARANA_LOW_RISK:  return 0,  ""
    return 10, ""


def _score_cross_region(prov_asal: str, prov_tujuan: str) -> tuple[int, str]:
    a = _s(prov_asal).upper()
    b = _s(prov_tujuan).upper()
    if a and b and a != b:
        return 100, f"Lintas provinsi ({a} → {b})"
    return 0, ""


# ─────────────────────────────────────────────────────────────
# LEVEL & BADGE
# ─────────────────────────────────────────────────────────────
def _classify(score: float) -> str:
    if score >= 70: return "HIGH"
    if score >= 40: return "MEDIUM"
    return "LOW"


# ─────────────────────────────────────────────────────────────
# HITUNG RISK SATU ITEM
# ─────────────────────────────────────────────────────────────
def _calc_one(item) -> dict:
    jumlah      = _f(item.jumlah)
    alamat      = _s(item.alamat_tujuan)
    tujuan      = _s(item.tujuan_penyaluran)
    no_faktur   = _s(item.no_faktur)
    jenis_sarana = _s(item.jenis_sarana)
    prov_asal   = _s(item.provinsi)
    prov_tujuan = _s(item.nama_provinsi_tujuan)
    exp_str     = _s(item.tanggal_kedaluwarsa)

    d_vol,  r_vol  = _score_volume(jumlah)
    d_anom, r_anom = _score_anomaly(item.anomaly_label)
    d_exp,  r_exp  = _score_expiry(exp_str)
    d_addr, r_addr = _score_address(alamat, tujuan, no_faktur)
    d_sar,  r_sar  = _score_sarana(jenis_sarana)
    d_cr,   r_cr   = _score_cross_region(prov_asal, prov_tujuan)

    # Weighted composite score
    composite = (
        d_vol  * WEIGHTS["volume"]       / 100 +
        d_anom * WEIGHTS["anomaly"]      / 100 +
        d_exp  * WEIGHTS["expiry"]       / 100 +
        d_addr * WEIGHTS["address"]      / 100 +
        d_sar  * WEIGHTS["sarana"]       / 100 +
        d_cr   * WEIGHTS["cross_region"] / 100
    )
    score = round(composite, 1)

    reasons = [r for r in [r_vol, r_anom, r_exp, r_addr, r_sar, r_cr] if r]

    return {
        "id":                   item.id,
        "nama_obat_jadi":       item.nama_obat_jadi       or "-",
        "tujuan_penyaluran":    item.tujuan_penyaluran    or "-",
        "nama_kota_kab_tujuan": item.nama_kota_kab_tujuan or "-",
        "nama_provinsi_tujuan": item.nama_provinsi_tujuan or "-",
        "jumlah":               jumlah,
        "satuan":               item.satuan               or "-",
        "jenis_sarana":         item.jenis_sarana         or "-",
        "tanggal_penyaluran":   item.tanggal_penyaluran   or "-",
        "tanggal_kedaluwarsa":  item.tanggal_kedaluwarsa  or "-",
        "nama_pbf":             item.nama_pbf             or "-",
        "anomaly_label":        item.anomaly_label,
        # Scores per dimensi
        "score_volume":       d_vol,
        "score_anomaly":      d_anom,
        "score_expiry":       d_exp,
        "score_address":      d_addr,
        "score_sarana":       d_sar,
        "score_cross_region": d_cr,
        # Composite
        "risk_score": score,
        "risk_level": _classify(score),
        "reasons":    ", ".join(reasons) if reasons else "Tidak ada faktor risiko",
        "reasons_list": reasons,
    }


# ─────────────────────────────────────────────────────────────
# MAIN — dipanggil dari route
# ─────────────────────────────────────────────────────────────
def calculate_risk(items) -> list[dict]:
    """Hitung risk score untuk semua item. Return list dict."""
    results = [_calc_one(it) for it in items]
    results.sort(key=lambda x: x["risk_score"], reverse=True)
    return results


# ─────────────────────────────────────────────────────────────
# SUMMARY STATS
# ─────────────────────────────────────────────────────────────
def get_risk_summary(results: list[dict]) -> dict:
    if not results:
        return {
            "total": 0, "high": 0, "medium": 0, "low": 0,
            "avg_score": 0, "top_factors": [], "by_sarana": [],
            "score_dist": [], "dimension_avg": {},
        }

    total  = len(results)
    high   = sum(1 for r in results if r["risk_level"] == "HIGH")
    medium = sum(1 for r in results if r["risk_level"] == "MEDIUM")
    low    = total - high - medium
    avg    = round(sum(r["risk_score"] for r in results) / total, 1)

    # Top factors
    from collections import Counter
    ctr = Counter()
    for r in results:
        for reason in r["reasons_list"]:
            ctr[reason] += 1
    top_factors = [{"reason": k, "count": v, "pct": round(v/total*100,1)}
                   for k, v in ctr.most_common(8)]

    # By sarana
    from collections import defaultdict
    sar_map = defaultdict(lambda: {"high": 0, "medium": 0, "low": 0, "total": 0, "sum_score": 0})
    for r in results:
        s = r["jenis_sarana"] or "-"
        sar_map[s]["total"]    += 1
        sar_map[s]["sum_score"] += r["risk_score"]
        sar_map[s][r["risk_level"].lower()] += 1
    by_sarana = [
        {
            "jenis_sarana": k,
            "total":    v["total"],
            "high":     v["high"],
            "medium":   v["medium"],
            "low":      v["low"],
            "avg_score": round(v["sum_score"] / v["total"], 1),
            "pct_high": round(v["high"] / v["total"] * 100, 1),
        }
        for k, v in sorted(sar_map.items(), key=lambda x: -x[1]["total"])
    ][:10]

    # Score distribution histogram
    scores = [r["risk_score"] for r in results]
    hist, edges = np.histogram(scores, bins=10, range=(0, 100))
    score_dist = [
        {"range": f"{int(edges[i])}–{int(edges[i+1])}", "count": int(hist[i])}
        for i in range(len(hist))
    ]

    # Average score per dimensi
    dimension_avg = {
        "volume":       round(sum(r["score_volume"]       for r in results) / total, 1),
        "anomaly":      round(sum(r["score_anomaly"]      for r in results) / total, 1),
        "expiry":       round(sum(r["score_expiry"]       for r in results) / total, 1),
        "address":      round(sum(r["score_address"]      for r in results) / total, 1),
        "sarana":       round(sum(r["score_sarana"]       for r in results) / total, 1),
        "cross_region": round(sum(r["score_cross_region"] for r in results) / total, 1),
    }

    return {
        "total": total, "high": high, "medium": medium, "low": low,
        "avg_score": avg, "top_factors": top_factors,
        "by_sarana": by_sarana, "score_dist": score_dist,
        "dimension_avg": dimension_avg,
    }


# ─────────────────────────────────────────────────────────────
# BACKWARD COMPAT — route lama pakai ini
# ─────────────────────────────────────────────────────────────
def calculate_risk_df(df: pd.DataFrame) -> pd.DataFrame:
    """Wrapper lama: menerima DataFrame, return DataFrame + risk cols."""
    result_df  = df.copy()
    risk_scores = []
    risk_levels = []

    for _, row in df.iterrows():
        jumlah = float(row.get("jumlah", 0) or 0)
        score  = 0

        if jumlah > VOL_MEDIUM:              score += 40
        if row.get("anomaly_label") == -1:   score += 40
        sarana = str(row.get("jenis_sarana", "") or "").lower()
        if "apotek" in sarana:               score += 10
        if "klinik" in sarana:               score += 10

        level = "HIGH" if score >= 70 else "MEDIUM" if score >= 40 else "LOW"
        risk_scores.append(score)
        risk_levels.append(level)

    result_df["risk_score"] = risk_scores
    result_df["risk_level"] = risk_levels
    return result_df