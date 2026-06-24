"""
Anomaly Detection Service
=========================
Metode yang digunakan:
  1. Rule-Based Detection   — 8 aturan bisnis BPOM
  2. Z-Score Statistical    — deteksi outlier statistik pada kolom jumlah
  3. Isolation Forest (ML)  — deteksi outlier multidimensi
  4. Local Outlier Factor   — density-based outlier (opsional)

Cocok sebagai referensi mata kuliah:
  - Penambangan Data / Data Mining
  - Kecerdasan Buatan
  - Statistika Komputasi
  - Sistem Pendukung Keputusan
"""

import numpy  as np
import pandas as pd
from datetime import datetime, timedelta

from sklearn.ensemble        import IsolationForest
from sklearn.neighbors       import LocalOutlierFactor
from sklearn.preprocessing   import StandardScaler
from scipy                   import stats

from sqlalchemy              import func, distinct
from backend.database.db     import db
from backend.models.distribution_model import Distribution


# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────
LARGE_DIST_THRESHOLD = 1_000_000   # rule: distribusi sangat besar
ZSCORE_THRESHOLD     = 3.0         # z-score cut-off
IF_CONTAMINATION     = 0.05        # isolation forest contamination rate
LOF_NEIGHBORS        = 20          # LOF n_neighbors


# ─────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────
def _safe_float(v, default=0.0):
    try:
        return float(v or 0)
    except (ValueError, TypeError):
        return default


def _safe_str(v):
    s = str(v or "").strip()
    return "" if s.lower() in ("nan", "none", "null") else s


def _days_until(date_str: str) -> float | None:
    """Return hari sampai tanggal, negatif = sudah lewat."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            d = datetime.strptime(date_str.strip(), fmt)
            return (d - datetime.today()).days
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────────────────────
# RULE-BASED DETECTION  (8 rules)
# ─────────────────────────────────────────────────────────────
def _rule_based(item) -> list[str]:
    """Kembalikan list alasan anomali. List kosong = normal."""
    reasons = []
    jumlah  = _safe_float(item.jumlah)
    alamat  = _safe_str(item.alamat_tujuan)
    obat    = _safe_str(item.nama_obat_jadi)
    tujuan  = _safe_str(item.tujuan_penyaluran)
    kota    = _safe_str(item.nama_kota_kab_tujuan)
    provinsi = _safe_str(item.nama_provinsi_tujuan)
    exp_str = _safe_str(item.tanggal_kedaluwarsa)

    # Rule 1 — Distribusi sangat besar
    if jumlah >= LARGE_DIST_THRESHOLD:
        reasons.append("Distribusi sangat besar (≥1.000.000)")

    # Rule 2 — jumlah tidak valid (≤ 0)
    if jumlah <= 0:
        reasons.append("jumlah distribusi tidak valid (≤0)")

    # Rule 3 — Alamat tujuan kosong
    if not alamat:
        reasons.append("Alamat tujuan kosong")

    # Rule 4 — Nama obat kosong
    if not obat:
        reasons.append("Nama obat jadi kosong")

    # Rule 5 — Tujuan penyaluran kosong
    if not tujuan:
        reasons.append("Tujuan penyaluran kosong")

    # Rule 6 — Kota tujuan kosong
    if not kota:
        reasons.append("Kota/Kab tujuan kosong")

    # Rule 7 — Tanggal kedaluwarsa sudah terlewat saat distribusi
    days = _days_until(exp_str)
    if days is not None and days < 0:
        reasons.append(f"Produk sudah kadaluwarsa ({abs(int(days))} hari lalu)")
    elif days is not None and 0 <= days <= 30:
        reasons.append(f"Hampir kadaluwarsa ({int(days)} hari lagi)")

    # Rule 8 — Provinsi tidak konsisten dengan kota (jika keduanya diisi tapi berbeda area umum)
    if not provinsi and kota:
        reasons.append("Provinsi tujuan kosong padahal kota diisi")

    return reasons


# ─────────────────────────────────────────────────────────────
# Z-SCORE DETECTION
# ─────────────────────────────────────────────────────────────
def _zscore_flags(jumlah_arr: np.ndarray) -> np.ndarray:
    """
    Kembalikan boolean array True = outlier Z-Score.
    Menggunakan scipy.stats.zscore (standarisasi kolom jumlah).
    """
    if len(jumlah_arr) < 10:
        return np.zeros(len(jumlah_arr), dtype=bool)
    z = np.abs(stats.zscore(jumlah_arr, nan_policy='omit'))
    return z > ZSCORE_THRESHOLD


# ─────────────────────────────────────────────────────────────
# ISOLATION FOREST
# ─────────────────────────────────────────────────────────────
def _isolation_forest_flags(features: np.ndarray) -> np.ndarray:
    """
    Kembalikan boolean array True = anomali IF.
    Input: matrix numerik (n_samples x n_features).
    """
    if len(features) < 20:
        return np.zeros(len(features), dtype=bool)
    model = IsolationForest(
        contamination=IF_CONTAMINATION,
        random_state=42,
        n_estimators=100,
    )
    preds = model.fit_predict(features)  # -1 = anomali
    return preds == -1


# ─────────────────────────────────────────────────────────────
# LOCAL OUTLIER FACTOR
# ─────────────────────────────────────────────────────────────
def _lof_flags(features: np.ndarray) -> np.ndarray:
    if len(features) < LOF_NEIGHBORS + 5:
        return np.zeros(len(features), dtype=bool)
    lof = LocalOutlierFactor(n_neighbors=min(LOF_NEIGHBORS, len(features) - 1))
    preds = lof.fit_predict(features)
    return preds == -1


# ─────────────────────────────────────────────────────────────
# MASTER DETECTION — dipanggil dari route
# ─────────────────────────────────────────────────────────────
def run_anomaly_detection(
    items: list,
    use_ml: bool = True,
    use_lof: bool = False,
) -> list[dict]:
    """
    Jalankan full anomaly pipeline pada list Distribution objects.
    Kembalikan list dict dengan field anomaly lengkap.
    """
    if not items:
        return []

    # ── Build feature matrix ───────────────────────────────
    jumlah_arr = np.array([_safe_float(it.jumlah) for it in items])

    # Multi-feature untuk IF & LOF: [jumlah, log_jumlah, has_alamat, has_tujuan]
    features = np.column_stack([
        jumlah_arr,
        np.log1p(np.clip(jumlah_arr, 0, None)),
        [1 if _safe_str(it.alamat_tujuan) else 0 for it in items],
        [1 if _safe_str(it.tujuan_penyaluran) else 0 for it in items],
    ])
    scaler = StandardScaler()
    features_scaled = scaler.fit_transform(features)

    # ── Run detectors ──────────────────────────────────────
    z_flags  = _zscore_flags(jumlah_arr)
    if_flags = _isolation_forest_flags(features_scaled) if use_ml else np.zeros(len(items), dtype=bool)
    lof_flags = _lof_flags(features_scaled)             if use_lof else np.zeros(len(items), dtype=bool)

    # ── Assemble results ───────────────────────────────────
    results = []
    for i, item in enumerate(items):
        rule_reasons = _rule_based(item)

        # Gabungkan semua sumber deteksi
        extra_reasons = []
        if z_flags[i]:  extra_reasons.append("Z-Score outlier (statistik)")
        if if_flags[i]: extra_reasons.append("Isolation Forest outlier (ML)")
        if lof_flags[i]: extra_reasons.append("Local Outlier Factor (ML)")

        all_reasons = rule_reasons + extra_reasons
        is_anomaly  = len(all_reasons) > 0

        results.append({
            "id":                   item.id,
            "tujuan_penyaluran":    item.tujuan_penyaluran  or "-",
            "nama_obat_jadi":       item.nama_obat_jadi     or "-",
            "nama_kota_kab_tujuan": item.nama_kota_kab_tujuan or "-",
            "nama_provinsi_tujuan": item.nama_provinsi_tujuan or "-",
            "nama_pbf":             item.nama_pbf            or "-",
            "jumlah":               _safe_float(item.jumlah),
            "satuan":               item.satuan              or "-",
            "tanggal_penyaluran":   item.tanggal_penyaluran  or "-",
            "tanggal_kedaluwarsa":  item.tanggal_kedaluwarsa or "-",
            "jenis_sarana":         item.jenis_sarana        or "-",
            "kategori_obat":        item.kategori_obat       or "-",
            "anomaly_label":        -1 if is_anomaly else 1,
            "anomaly_reason":       ", ".join(all_reasons) if all_reasons else "Normal",
            "rule_flags":           rule_reasons,
            "z_flag":               bool(z_flags[i]),
            "if_flag":              bool(if_flags[i]),
            "lof_flag":             bool(lof_flags[i]),
            "is_anomaly":           is_anomaly,
        })

    return results


# ─────────────────────────────────────────────────────────────
# STATISTIK RINGKASAN
# ─────────────────────────────────────────────────────────────
def _apply_filters(query, filters: dict = None):
    """
    Apply shared filter params to a Distribution query.
    Uses func.lower() for all string comparisons so filters work on both
    PostgreSQL and SQLite regardless of the case stored in DB or sent
    from the frontend dropdown.
    """
    if not filters:
        return query
    tahun        = (filters.get("tahun")        or "").strip()
    provinsi     = (filters.get("provinsi")     or "").strip().lower()
    kategori     = (filters.get("kategori")     or "").strip().lower()
    jenis_sarana = (filters.get("jenis_sarana") or "").strip().lower()
    search       = (filters.get("search")       or "").strip()

    if tahun:
        query = query.filter(
            func.substr(Distribution.tanggal_penyaluran, 1, 4) == tahun
        )
    if provinsi:
        query = query.filter(
            func.lower(Distribution.nama_provinsi_tujuan) == provinsi
        )
    if kategori:
        query = query.filter(
            func.lower(Distribution.kategori_obat) == kategori
        )
    if jenis_sarana:
        query = query.filter(
            func.lower(Distribution.jenis_sarana) == jenis_sarana
        )
    if search:
        s = search.lower()
        query = query.filter(
            db.or_(
                func.lower(Distribution.tujuan_penyaluran).like(f"%{s}%"),
                func.lower(Distribution.nama_obat_jadi).like(f"%{s}%"),
            )
        )
    return query


def get_anomaly_summary(filters: dict = None) -> dict:
    """Statistik anomali dari DB untuk dashboard (mendukung filter)."""
    base_query = _apply_filters(Distribution.query, filters)

    total      = base_query.count()
    n_anomaly  = base_query.filter(Distribution.anomaly_label == -1).count()
    n_normal   = total - n_anomaly
    pct        = round(n_anomaly / total * 100, 2) if total else 0

    # Top penyebab anomali (parsing anomaly_reason)
    reasons_raw = (
        _apply_filters(db.session.query(Distribution.anomaly_reason), filters)
        .filter(Distribution.anomaly_label == -1)
        .all()
    )
    from collections import Counter
    counter = Counter()
    for (reason,) in reasons_raw:
        if reason:
            for r in reason.split(","):
                r = r.strip()
                if r and r != "Normal":
                    counter[r] += 1
    top_reasons = [{"reason": k, "count": v} for k, v in counter.most_common(8)]

    # Distribusi anomali per jenis sarana
    sarana_q = _apply_filters(
        db.session.query(
            Distribution.jenis_sarana,
            func.count(Distribution.id).label("total"),
            func.sum(
                db.case((Distribution.anomaly_label == -1, 1), else_=0)
            ).label("anomaly"),
        ),
        filters,
    )
    sarana_stats = (
        sarana_q
        .group_by(Distribution.jenis_sarana)
        .order_by(func.count(Distribution.id).desc())
        .limit(10).all()
    )

    # Anomali per bulan
    monthly_q = _apply_filters(
        db.session.query(
            func.substr(Distribution.tanggal_penyaluran, 1, 7).label("bulan"),
            func.count(Distribution.id).label("total"),
            func.sum(
                db.case((Distribution.anomaly_label == -1, 1), else_=0)
            ).label("anomaly"),
        ),
        filters,
    )
    monthly = (
        monthly_q
        .filter(Distribution.tanggal_penyaluran.isnot(None))
        .group_by(func.substr(Distribution.tanggal_penyaluran, 1, 7))
        .order_by(func.substr(Distribution.tanggal_penyaluran, 1, 7))
        .all()
    )

    return {
        "total":      total,
        "n_anomaly":  n_anomaly,
        "n_normal":   n_normal,
        "pct_anomaly": pct,
        "top_reasons": top_reasons,
        "sarana_stats": [
            {
                "jenis_sarana": r.jenis_sarana or "-",
                "total":   r.total   or 0,
                "anomaly": r.anomaly or 0,
                "pct": round((r.anomaly or 0) / (r.total or 1) * 100, 1),
            }
            for r in sarana_stats
        ],
        "monthly": [
            {
                "bulan":   r.bulan,
                "total":   r.total   or 0,
                "anomaly": r.anomaly or 0,
            }
            for r in monthly if r.bulan
        ],
    }


# ─────────────────────────────────────────────────────────────
# UPDATE ANOMALY LABELS KE DB (batch)
# ─────────────────────────────────────────────────────────────
def update_anomaly_labels(use_ml: bool = True) -> dict:
    """
    Jalankan deteksi pada semua data dan simpan ke DB.
    Dipanggil dari route POST /anomaly/run.
    """
    items   = Distribution.query.all()
    results = run_anomaly_detection(items, use_ml=use_ml)

    updated = 0
    for res in results:
        obj = db.session.get(Distribution, res["id"])
        if obj:
            obj.anomaly_label  = res["anomaly_label"]
            obj.anomaly_reason = res["anomaly_reason"]
            updated += 1

    db.session.commit()
    n_anom = sum(1 for r in results if r["is_anomaly"])
    return {
        "total":   updated,
        "anomaly": n_anom,
        "normal":  updated - n_anom,
    }


# # ─────────────────────────────────────────────────────────────
# # COMPAT WRAPPER — dipanggil oleh upload_routes.py
# # Menerima DataFrame CSV (kolom nama asli), kembalikan DataFrame
# # dengan kolom anomaly_label dan anomaly_reason ditambahkan.
# # ─────────────────────────────────────────────────────────────
# def detect_anomaly(df: pd.DataFrame) -> pd.DataFrame:
#     """
#     Wrapper kompatibel untuk upload_routes.py.
#     Input : DataFrame hasil cleaning (kolom = nama CSV asli)
#     Output: DataFrame yang sama + kolom anomaly_label & anomaly_reason
#     """
#     result_df = df.copy()

#     # Default
#     result_df["anomaly_label"]  = 1
#     result_df["anomaly_reason"] = "Normal"

#     # Kolom jumlah
#     result_df["jumlah"] = pd.to_numeric(
#         result_df.get("jumlah"), errors="coerce"
#     ).fillna(0)

#     # ── Z-Score pada kolom jumlah ─────────────────────────
#     jumlah_arr = result_df["jumlah"].values.astype(float)
#     z_flags = _zscore_flags(jumlah_arr)

#     # ── Isolation Forest (multi-feature) ──────────────────
#     has_alamat  = result_df.get("Alamat Tujuan",        "").astype(str).apply(lambda x: 0 if x.strip() in ("", "nan") else 1).values
#     has_tujuan  = result_df.get("Tujuan Penyaluran",    "").astype(str).apply(lambda x: 0 if x.strip() in ("", "nan") else 1).values
#     features = np.column_stack([
#         jumlah_arr,
#         np.log1p(np.clip(jumlah_arr, 0, None)),
#         has_alamat,
#         has_tujuan,
#     ])
#     scaler = StandardScaler()
#     features_scaled = scaler.fit_transform(features)
#     if_flags = _isolation_forest_flags(features_scaled)

#     # ── Rule-based + gabung ────────────────────────────────
#     LARGE = 1_000_000
#     today_str = datetime.today().strftime("%Y-%m-%d")
#     batas_str = (datetime.today() + timedelta(days=90)).strftime("%Y-%m-%d")

#     for idx in range(len(result_df)):
#         row     = result_df.iloc[idx]
#         reasons = []

#         jumlah  = float(row.get("jumlah", 0) or 0)
#         alamat  = str(row.get("Alamat Tujuan",     "") or "").strip()
#         obat    = str(row.get("Nama Obat Jadi",    "") or "").strip()
#         tujuan  = str(row.get("Tujuan Penyaluran", "") or "").strip()
#         kota    = str(row.get("Nama Kota/Kab Tujuan", "") or "").strip()
#         provinsi = str(row.get("Nama Provinsi Tujuan", "") or "").strip()
#         exp_str = str(row.get("Tanggal Kedaluwarsa", "") or "").strip()

#         # Rule 1 — volume sangat besar
#         if jumlah >= LARGE:
#             reasons.append("Distribusi sangat besar (≥1.000.000)")
#         # Rule 2 — jumlah tidak valid
#         if jumlah <= 0:
#             reasons.append("jumlah distribusi tidak valid (≤0)")
#         # Rule 3 — alamat kosong
#         if not alamat or alamat.lower() in ("nan", "none"):
#             reasons.append("Alamat tujuan kosong")
#         # Rule 4 — nama obat kosong
#         if not obat or obat.lower() in ("nan", "none"):
#             reasons.append("Nama obat jadi kosong")
#         # Rule 5 — tujuan kosong
#         if not tujuan or tujuan.lower() in ("nan", "none"):
#             reasons.append("Tujuan penyaluran kosong")
#         # Rule 6 — kota kosong
#         if not kota or kota.lower() in ("nan", "none"):
#             reasons.append("Kota/Kab tujuan kosong")
#         # Rule 7 — kadaluwarsa
#         days = _days_until(exp_str)
#         if days is not None and days < 0:
#             reasons.append(f"Produk sudah kadaluwarsa ({abs(int(days))} hari lalu)")
#         elif days is not None and 0 <= days <= 30:
#             reasons.append(f"Hampir kadaluwarsa ({int(days)} hari lagi)")
#         # Rule 8 — provinsi kosong
#         if not provinsi or provinsi.lower() in ("nan", "none"):
#             if kota:
#                 reasons.append("Provinsi tujuan kosong padahal kota diisi")

#         # Z-Score flag
#         if z_flags[idx]:
#             reasons.append("Z-Score outlier (statistik)")
#         # IF flag
#         if if_flags[idx]:
#             reasons.append("Isolation Forest outlier (ML)")

#         if reasons:
#             result_df.at[result_df.index[idx], "anomaly_label"]  = -1
#             result_df.at[result_df.index[idx], "anomaly_reason"] = ", ".join(reasons)

#     return result_df
# ─────────────────────────────────────────────────────────────
# COMPAT WRAPPER — dipanggil oleh upload_routes.py
# Input : DataFrame hasil cleaning (snake_case)
# Output: DataFrame + anomaly_label + anomaly_reason
# ─────────────────────────────────────────────────────────────
def detect_anomaly(df: pd.DataFrame) -> pd.DataFrame:

    result_df = df.copy()

    # default
    result_df["anomaly_label"] = 1
    result_df["anomaly_reason"] = "Normal"

    # pastikan jumlah numerik
    result_df["jumlah"] = pd.to_numeric(
        result_df["jumlah"],
        errors="coerce"
    ).fillna(0)

    # ── Z-Score ────────────────────────────────────────────
    jumlah_arr = result_df["jumlah"].values.astype(float)
    z_flags = _zscore_flags(jumlah_arr)

    # ── Feature untuk Isolation Forest ─────────────────────
    has_alamat = (
        result_df["alamat_tujuan"]
        .astype(str)
        .apply(lambda x: 0 if x.strip().lower() in ("", "nan", "none") else 1)
        .values
    )

    has_tujuan = (
        result_df["tujuan_penyaluran"]
        .astype(str)
        .apply(lambda x: 0 if x.strip().lower() in ("", "nan", "none") else 1)
        .values
    )

    features = np.column_stack([
        jumlah_arr,
        np.log1p(np.clip(jumlah_arr, 0, None)),
        has_alamat,
        has_tujuan,
    ])

    scaler = StandardScaler()
    features_scaled = scaler.fit_transform(features)

    if_flags = _isolation_forest_flags(features_scaled)

    LARGE = 1_000_000

    # ── Rule-based + combine ───────────────────────────────
    for idx in range(len(result_df)):

        row = result_df.iloc[idx]
        reasons = []

        jumlah = float(row.get("jumlah", 0) or 0)

        alamat = str(
            row.get("alamat_tujuan", "") or ""
        ).strip()

        obat = str(
            row.get("nama_obat_jadi", "") or ""
        ).strip()

        tujuan = str(
            row.get("tujuan_penyaluran", "") or ""
        ).strip()

        kota = str(
            row.get("nama_kota_kab_tujuan", "") or ""
        ).strip()

        provinsi = str(
            row.get("nama_provinsi_tujuan", "") or ""
        ).strip()

        exp_str = str(
            row.get("tanggal_kedaluwarsa", "") or ""
        ).strip()

        # Rule 1
        if jumlah >= LARGE:
            reasons.append("Distribusi sangat besar (≥1.000.000)")

        # Rule 2
        if jumlah <= 0:
            reasons.append("Jumlah distribusi tidak valid (≤0)")

        # Rule 3
        if not alamat or alamat.lower() in ("nan", "none"):
            reasons.append("Alamat tujuan kosong")

        # Rule 4
        if not obat or obat.lower() in ("nan", "none"):
            reasons.append("Nama obat jadi kosong")

        # Rule 5
        if not tujuan or tujuan.lower() in ("nan", "none"):
            reasons.append("Tujuan penyaluran kosong")

        # Rule 6
        if not kota or kota.lower() in ("nan", "none"):
            reasons.append("Kota/Kab tujuan kosong")

        # Rule 7
        days = _days_until(exp_str)

        if days is not None and days < 0:
            reasons.append(
                f"Produk sudah kadaluwarsa ({abs(int(days))} hari lalu)"
            )

        elif days is not None and 0 <= days <= 30:
            reasons.append(
                f"Hampir kadaluwarsa ({int(days)} hari lagi)"
            )

        # Rule 8
        if (not provinsi or provinsi.lower() in ("nan", "none")) and kota:
            reasons.append(
                "Provinsi tujuan kosong padahal kota diisi"
            )

        # Z-score
        if z_flags[idx]:
            reasons.append("Z-Score outlier (statistik)")

        # Isolation Forest
        if if_flags[idx]:
            reasons.append("Isolation Forest outlier (ML)")

        if reasons:
            result_df.at[result_df.index[idx], "anomaly_label"] = -1
            result_df.at[result_df.index[idx], "anomaly_reason"] = ", ".join(reasons)

    return result_df