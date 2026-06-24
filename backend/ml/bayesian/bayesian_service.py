"""
Bayesian Risk Analysis Service
===============================
Pendekatan yang digunakan:
  1. Prior Probability       — probabilitas awal berdasarkan data historis
  2. Likelihood              — probabilitas bukti muncul jika hipotesis benar
  3. Posterior Probability   — P(Risk|Evidence) via Bayes' Theorem
  4. Multi-Factor Scoring    — 7 faktor risiko berbobot

Formula Bayes yang diimplementasi:
  P(H|E) = P(E|H) * P(H) / P(E)
  dimana:
    H = hipotesis "distribusi berisiko"
    E = evidence (kumpulan faktor risiko)

Cocok sebagai referensi mata kuliah:
  - Statistika Bayesian
  - Sistem Pendukung Keputusan
  - Manajemen Risiko
  - Kecerdasan Buatan
  - Data Science & Analytics
"""

import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

from sqlalchemy          import func
from backend.database.db import db
from backend.models.distribution_model import Distribution


# ─────────────────────────────────────────────────────────────
# PRIOR PROBABILITIES  (dari data domain BPOM)
# Ini adalah pengetahuan awal sebelum melihat data spesifik.
# ─────────────────────────────────────────────────────────────
PRIOR = {
    "distribusi_berisiko":  0.15,   # 15% distribusi secara historis berisiko
    "distribusi_aman":      0.85,
}

# ─────────────────────────────────────────────────────────────
# LIKELIHOOD TABLE  P(Evidence|Hypothesis)
# Seberapa mungkin setiap bukti muncul jika hipotesis benar.
# ─────────────────────────────────────────────────────────────
LIKELIHOOD = {
    # P(anomaly detected | berisiko) vs P(anomaly detected | aman)
    "anomaly_detected": {"berisiko": 0.90, "aman": 0.05},

    # P(jumlah sangat besar | berisiko) vs P(jumlah sangat besar | aman)
    "jumlah_sangat_besar": {"berisiko": 0.70, "aman": 0.10},

    # P(hampir kadaluwarsa | berisiko) vs P(hampir kadaluwarsa | aman)
    "hampir_kadaluwarsa": {"berisiko": 0.60, "aman": 0.15},

    # P(alamat kosong | berisiko) vs P(alamat kosong | aman)
    "alamat_kosong": {"berisiko": 0.50, "aman": 0.05},

    # P(sarana tidak terklasifikasi | berisiko)
    "sarana_tidak_terklasifikasi": {"berisiko": 0.40, "aman": 0.08},

    # P(tidak ada no faktur | berisiko)
    "no_faktur_kosong": {"berisiko": 0.35, "aman": 0.10},

    # P(lintas provinsi | berisiko)
    "lintas_provinsi": {"berisiko": 0.30, "aman": 0.20},
}


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
def _f(v):
    try: return float(v or 0)
    except: return 0.0

def _s(v):
    s = str(v or "").strip()
    return "" if s.lower() in ("nan", "none", "null") else s

def _days_to_exp(exp_str: str):
    if not exp_str:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return (datetime.strptime(exp_str.strip(), fmt) - datetime.today()).days
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────────────────────
# EVIDENCE EXTRACTOR
# ─────────────────────────────────────────────────────────────
def _extract_evidence(item) -> dict:
    """
    Ekstrak evidence (bukti/fitur) dari satu record distribusi.
    Kembalikan dict boolean — setiap evidence True/False.
    """
    jumlah  = _f(item.jumlah)
    alamat  = _s(item.alamat_tujuan)
    tujuan  = _s(item.tujuan_penyaluran)
    no_faktur = _s(item.no_faktur)
    sarana  = _s(item.jenis_sarana).lower()
    provinsi_asal  = _s(item.provinsi).upper()
    provinsi_tujuan = _s(item.nama_provinsi_tujuan).upper()
    days    = _days_to_exp(_s(item.tanggal_kedaluwarsa))

    high_volume_threshold = 500_000

    unclassified_sarana = {
        "", "-", "nan", "unknown", "lainnya", "perorangan", "tidak resmi"
    }

    return {
        "anomaly_detected":             item.anomaly_label == -1,
        "jumlah_sangat_besar":          jumlah >= high_volume_threshold,
        "hampir_kadaluwarsa":           days is not None and 0 <= days <= 90,
        "alamat_kosong":                not alamat,
        "sarana_tidak_terklasifikasi":  sarana in unclassified_sarana,
        "no_faktur_kosong":             not no_faktur,
        "lintas_provinsi": (
            bool(provinsi_asal) and
            bool(provinsi_tujuan) and
            provinsi_asal != provinsi_tujuan
        ),
    }


# ─────────────────────────────────────────────────────────────
# BAYESIAN POSTERIOR CALCULATOR
# ─────────────────────────────────────────────────────────────
def _bayesian_posterior(evidence: dict) -> float:
    """
    Hitung P(berisiko | evidence) menggunakan Naive Bayes:
      P(H|E1,E2,...,En) ∝ P(H) * Π P(Ei|H)

    Return nilai posterior antara 0 dan 1.
    """
    # Log-space untuk mencegah underflow numerik
    log_prior_risk = np.log(PRIOR["distribusi_berisiko"])
    log_prior_safe = np.log(PRIOR["distribusi_aman"])

    for ev_name, ev_value in evidence.items():
        if ev_name not in LIKELIHOOD:
            continue
        lik = LIKELIHOOD[ev_name]
        if ev_value:
            # P(E=True | H)
            log_prior_risk += np.log(lik["berisiko"] + 1e-9)
            log_prior_safe += np.log(lik["aman"]      + 1e-9)
        else:
            # P(E=False | H) = 1 - P(E=True | H)
            log_prior_risk += np.log(1 - lik["berisiko"] + 1e-9)
            log_prior_safe += np.log(1 - lik["aman"]      + 1e-9)

    # Normalisasi → posterior probability
    max_log = max(log_prior_risk, log_prior_safe)
    exp_risk = np.exp(log_prior_risk - max_log)
    exp_safe = np.exp(log_prior_safe - max_log)
    posterior = exp_risk / (exp_risk + exp_safe)
    return float(np.clip(posterior, 0.0, 1.0))


# ─────────────────────────────────────────────────────────────
# RISK LEVEL & SCORE
# ─────────────────────────────────────────────────────────────
def _classify_risk(posterior: float) -> str:
    if posterior >= 0.75: return "High Risk"
    if posterior >= 0.45: return "Medium Risk"
    return "Low Risk"

def _risk_score(posterior: float) -> int:
    """Skor 0–100 untuk kemudahan tampilan."""
    return int(round(posterior * 100))


# ─────────────────────────────────────────────────────────────
# REASON BUILDER
# ─────────────────────────────────────────────────────────────
EVIDENCE_LABELS = {
    "anomaly_detected":             "Terdeteksi sebagai anomali",
    "jumlah_sangat_besar":          "Volume distribusi sangat besar (≥500rb)",
    "hampir_kadaluwarsa":           "Produk hampir/sudah kadaluwarsa (≤90 hari)",
    "alamat_kosong":                "Alamat tujuan tidak terisi",
    "sarana_tidak_terklasifikasi":  "Jenis sarana tidak terklasifikasi",
    "no_faktur_kosong":             "Nomor faktur tidak ada",
    "lintas_provinsi":              "Distribusi lintas provinsi",
}

def _build_reasons(evidence: dict) -> list[str]:
    return [EVIDENCE_LABELS[k] for k, v in evidence.items() if v and k in EVIDENCE_LABELS]


# ─────────────────────────────────────────────────────────────
# MAIN FUNCTION — dipanggil dari route
# ─────────────────────────────────────────────────────────────
def calculate_bayesian_risk(data: list) -> list[dict]:
    """
    Jalankan analisis Bayesian pada list Distribution objects.
    Return list dict lengkap dengan posterior probability dan risk level.
    """
    results = []
    for item in data:
        evidence  = _extract_evidence(item)
        posterior = _bayesian_posterior(evidence)
        reasons   = _build_reasons(evidence)
        risk_lvl  = _classify_risk(posterior)
        score     = _risk_score(posterior)

        results.append({
            # Data dasar
            "id":           item.id,
            "nama_obat":    item.nama_obat_jadi    or "-",
            "tujuan":       item.tujuan_penyaluran  or "-",
            "kota":         item.nama_kota_kab_tujuan or "-",
            "provinsi":     item.nama_provinsi_tujuan or "-",
            "jumlah":       _f(item.jumlah),
            "satuan":       item.satuan             or "-",
            "jenis_sarana": item.jenis_sarana       or "-",
            "tanggal_penyaluran":  item.tanggal_penyaluran  or "-",
            "tanggal_kedaluwarsa": item.tanggal_kedaluwarsa or "-",
            "nama_pbf":     item.nama_pbf           or "-",
            # Bayesian output
            "probability":  round(posterior, 4),
            "risk_score":   score,
            "risk_level":   risk_lvl,
            "reasons":      ", ".join(reasons) if reasons else "Tidak ada faktor risiko",
            "reasons_list": reasons,
            "evidence":     {k: bool(v) for k, v in evidence.items()},
        })

    return results


# ─────────────────────────────────────────────────────────────
# STATISTIK RINGKASAN BAYESIAN
# ─────────────────────────────────────────────────────────────
def get_bayesian_summary(analysis: list[dict]) -> dict:
    """Hitung statistik ringkasan dari hasil analisis."""
    if not analysis:
        return {
            "total": 0, "high": 0, "medium": 0, "low": 0,
            "avg_probability": 0, "top_factors": [],
            "risk_by_sarana": [], "risk_distribution": [],
        }

    total  = len(analysis)
    high   = sum(1 for r in analysis if r["risk_level"] == "High Risk")
    medium = sum(1 for r in analysis if r["risk_level"] == "Medium Risk")
    low    = total - high - medium
    avg_p  = round(sum(r["probability"] for r in analysis) / total, 4)

    # Top faktor risiko
    from collections import Counter
    factor_counter = Counter()
    for r in analysis:
        for reason in r["reasons_list"]:
            factor_counter[reason] += 1
    top_factors = [
        {"factor": k, "count": v, "pct": round(v / total * 100, 1)}
        for k, v in factor_counter.most_common(8)
    ]

    # Risk per jenis sarana
    sarana_map = defaultdict(lambda: {"high": 0, "medium": 0, "low": 0, "total": 0})
    for r in analysis:
        s = r["jenis_sarana"] or "-"
        sarana_map[s]["total"] += 1
        sarana_map[s][r["risk_level"].split()[0].lower()] += 1
    risk_by_sarana = [
        {
            "jenis_sarana": k,
            "total":  v["total"],
            "high":   v["high"],
            "medium": v["medium"],
            "low":    v["low"],
            "pct_high": round(v["high"] / v["total"] * 100, 1),
        }
        for k, v in sorted(sarana_map.items(), key=lambda x: -x[1]["total"])
        if v["total"] > 0
    ][:10]

    # Distribusi skor risiko (histogram 10 bucket)
    scores = [r["risk_score"] for r in analysis]
    hist, edges = np.histogram(scores, bins=10, range=(0, 100))
    risk_distribution = [
        {"range": f"{int(edges[i])}–{int(edges[i+1])}", "count": int(hist[i])}
        for i in range(len(hist))
    ]

    return {
        "total":  total,
        "high":   high,
        "medium": medium,
        "low":    low,
        "avg_probability": avg_p,
        "top_factors":     top_factors,
        "risk_by_sarana":  risk_by_sarana,
        "risk_distribution": risk_distribution,
    }