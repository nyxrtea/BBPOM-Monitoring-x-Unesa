"""
Analytics Service — FIXED
==========================
Perbaikan:
1. Case-insensitive filter menggunakan func.lower() di PostgreSQL
   → 'Jawa Timur' == 'JAWA TIMUR' == 'jawa timur'
2. Filter options tampil dalam Title Case agar konsisten di dropdown
3. Semua fungsi menerima filters dict yang sama persis dari request.args
"""

from datetime import datetime, timedelta
import re

from sqlalchemy     import func, distinct
from backend.database.db import db
from backend.models.distribution_model import Distribution


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
def _s(v):
    s = str(v or "").strip()
    return "" if s.lower() in ("nan", "none", "null") else s

def _f(v):
    try:    return float(v or 0)
    except: return 0.0


# ─────────────────────────────────────────────────────────────
# BASE QUERY — filter dengan func.lower() agar case-insensitive
# di PostgreSQL (ilike sudah case-insensitive tapi value dari
# dropdown mungkin berbeda case dengan data di DB)
# ─────────────────────────────────────────────────────────────
def _base_q(filters: dict):
    q = db.session.query(Distribution)

    if filters.get("tahun"):
        q = q.filter(
            Distribution.tanggal_penyaluran.like(f"{filters['tahun']}%")
        )

    # All string comparisons use func.lower() — works on PostgreSQL + SQLite.
    # DB may store lowercase; frontend sends Title Case from dropdown.
    if filters.get("provinsi"):
        q = q.filter(
            func.lower(Distribution.nama_provinsi_tujuan)
            == filters["provinsi"].strip().lower()
        )

    if filters.get("kota"):
        kota = filters["kota"].strip().lower()
        q = q.filter(
            func.lower(Distribution.nama_kota_kab_tujuan).like(f"%{kota}%")
        )

    if filters.get("kategori"):
        q = q.filter(
            func.lower(Distribution.kategori_obat)
            == filters["kategori"].strip().lower()
        )

    if filters.get("jenis_sarana"):
        q = q.filter(
            func.lower(Distribution.jenis_sarana)
            == filters["jenis_sarana"].strip().lower()
        )

    if filters.get("search"):
        s = filters["search"].strip().lower()
        q = q.filter(
            func.lower(Distribution.tujuan_penyaluran).like(f"%{s}%")
        )

    return q


# ─────────────────────────────────────────────────────────────
# FILTER OPTIONS — ambil nilai unik, normalisasi Title Case
# agar dropdown konsisten (tidak ada duplikat JAWA TIMUR/Jawa Timur)
# ─────────────────────────────────────────────────────────────
def get_filter_options() -> dict:
    """
    Ambil daftar unik untuk setiap dropdown filter.
    Normalisasi menggunakan func.initcap (PostgreSQL) → Title Case.
    Fallback: Python title() jika initcap tidak tersedia.
    """

    def uniq_normalized(col):
        """
        Ambil nilai unik dari kolom, normalisasi ke Title Case,
        deduplikasi, lalu sort.
        """
        rows = (
            db.session.query(col)
            .filter(col.isnot(None), col != "")
            .distinct()
            .order_by(col)
            .all()
        )
        # Deduplikasi case-insensitive, simpan Title Case
        seen  = {}
        for (v,) in rows:
            if not v or not str(v).strip():
                continue
            key = str(v).strip().lower()
            if key not in seen:
                # Simpan versi Title Case
                seen[key] = str(v).strip().title()
        return sorted(seen.values())

    # Tahun — dari 4 karakter pertama tanggal_penyaluran
    tahun_rows = (
        db.session.query(
            func.substr(Distribution.tanggal_penyaluran, 1, 4).label("yr")
        )
        .filter(Distribution.tanggal_penyaluran.isnot(None))
        .distinct()
        .all()
    )
    tahun_list = sorted(
        {r.yr for r in tahun_rows if r.yr and r.yr.strip().isdigit()},
        reverse=True,
    )

    return {
        "tahun_list":    tahun_list,
        "provinsi_list": uniq_normalized(Distribution.nama_provinsi_tujuan),
        "kategori_list": uniq_normalized(Distribution.kategori_obat),
        "sarana_list":   uniq_normalized(Distribution.jenis_sarana),
    }


# ─────────────────────────────────────────────────────────────
# KPI + TREN  (Tab 1)
# ─────────────────────────────────────────────────────────────
def get_tren(filters: dict) -> dict:
    q = _base_q(filters)

    total_trx  = q.count()
    total_qty  = q.with_entities(func.sum(Distribution.jumlah)).scalar() or 0
    total_obat = q.with_entities(
        func.count(distinct(Distribution.nama_obat_jadi))
    ).scalar() or 0

    today_str = datetime.today().strftime("%Y-%m-%d")
    batas_str = (datetime.today() + timedelta(days=90)).strftime("%Y-%m-%d")

    hampir = (
        q.filter(
            Distribution.tanggal_kedaluwarsa.isnot(None),
            Distribution.tanggal_kedaluwarsa != "",
            Distribution.tanggal_kedaluwarsa > today_str,
            Distribution.tanggal_kedaluwarsa <= batas_str,
        ).count()
    )

    # Tren bulanan
    tren_rows = (
        q.with_entities(
            func.substr(Distribution.tanggal_penyaluran, 1, 7).label("bulan"),
            func.sum(Distribution.jumlah).label("total"),
        )
        .filter(Distribution.tanggal_penyaluran.isnot(None))
        .group_by(func.substr(Distribution.tanggal_penyaluran, 1, 7))
        .order_by(func.substr(Distribution.tanggal_penyaluran, 1, 7))
        .all()
    )

    tren_bulanan = [
        {"bulan": r.bulan, "jumlah": _f(r.total)}
        for r in tren_rows
        if r.bulan and re.match(r"^\d{4}-\d{2}$", r.bulan)
    ]

    return {
        "kpi": {
            "total_transaksi":  total_trx,
            "total_distribusi": float(total_qty),
            "jenis_obat":       total_obat,
            "hampir_expired":   hampir,
        },
        "tren_bulanan": tren_bulanan,
        "bulan_list":   [r["bulan"] for r in tren_bulanan],
    }


def get_detail_bulan(filters: dict, bulan: str) -> dict:
    q = _base_q(filters).filter(
        Distribution.tanggal_penyaluran.like(f"{bulan}%")
    )

    total_trx = q.count()
    total_qty = q.with_entities(func.sum(Distribution.jumlah)).scalar() or 0

    harian_rows = (
        q.with_entities(
            func.substr(Distribution.tanggal_penyaluran, 1, 10).label("tgl"),
            func.sum(Distribution.jumlah).label("total"),
        )
        .filter(Distribution.tanggal_penyaluran.isnot(None))
        .group_by(func.substr(Distribution.tanggal_penyaluran, 1, 10))
        .order_by(func.substr(Distribution.tanggal_penyaluran, 1, 10))
        .all()
    )
    harian = [
        {"tanggal": r.tgl, "total": _f(r.total)}
        for r in harian_rows if r.tgl
    ]

    rekap_rows = (
        q.with_entities(
            Distribution.tujuan_penyaluran.label("apotek"),
            Distribution.nama_kota_kab_tujuan.label("kota"),
            Distribution.nama_obat_jadi.label("obat"),
            func.count(Distribution.id).label("jumlah_transaksi"),
            func.sum(Distribution.jumlah).label("total_distribusi"),
        )
        .group_by(
            Distribution.tujuan_penyaluran,
            Distribution.nama_kota_kab_tujuan,
            Distribution.nama_obat_jadi,
        )
        .order_by(func.count(Distribution.id).desc())
        .limit(200)
        .all()
    )

    rekap = [
        {
            "apotek": _s(r.apotek), "kota": _s(r.kota), "obat": _s(r.obat),
            "jumlah_transaksi": r.jumlah_transaksi or 0,
            "total_distribusi": _f(r.total_distribusi),
        }
        for r in rekap_rows
    ]

    top = rekap[0] if rekap else {}
    return {
        "harian":       harian,
        "rekap_apotek": rekap,
        "metrics": {
            "total_transaksi":  total_trx,
            "total_distribusi": float(total_qty),
            "top_apotek":       top.get("apotek", "-"),
            "top_trx":          top.get("jumlah_transaksi", 0),
        },
    }


# ─────────────────────────────────────────────────────────────
# PETA  (Tab 2)
# ─────────────────────────────────────────────────────────────
def get_peta(filters: dict) -> dict:
    q = _base_q(filters)

    rows = (
        q.with_entities(
            func.initcap(Distribution.nama_kota_kab_tujuan).label("kota"),
            func.sum(Distribution.jumlah).label("total"),
        )
        .filter(
            Distribution.nama_kota_kab_tujuan.isnot(None),
            Distribution.nama_kota_kab_tujuan != "",
        )
        .group_by(func.initcap(Distribution.nama_kota_kab_tujuan))
        .order_by(func.sum(Distribution.jumlah).desc())
        .all()
    )

    totals = [_f(r.total) for r in rows]
    n      = len(totals)
    vals_sorted = sorted(totals)
    p33 = vals_sorted[int(n * 0.33)] if n else 0
    p66 = vals_sorted[int(n * 0.66)] if n else 0
    grand = sum(totals) or 1

    def kat(v):
        if v < p33: return "Ringan"
        if v < p66: return "Sedang"
        return "Berat"

    # dist_level uses volume percentile tiers (Ringan/Sedang/Berat)
    # matching the standalone /map page and map_visualization.js legend.
    totals_list = [_f(r.total) for r in rows]
    n_rows      = len(totals_list)
    ts          = sorted(totals_list)
    p33 = ts[int(n_rows * 0.33)] if n_rows > 2 else (ts[0]  if ts else 0)
    p67 = ts[int(n_rows * 0.67)] if n_rows > 2 else (ts[-1] if ts else 0)

    def dist_level(v):
        if v >= p67: return "berat"
        if v >= p33: return "sedang"
        return "ringan"

    kota_data = [
        {
            "wilayah":     _s(r.kota),
            "total":       _f(r.total),
            "persen":      round(_f(r.total) / grand * 100, 2),
            "kategori":    kat(_f(r.total)),
            "dist_level":  dist_level(_f(r.total)),
        }
        for r in rows
    ]

    return {
        "kota_data":       kota_data,
        "top_kota":        kota_data[:10],
        "thresh_low":      round(p33),
        "thresh_high":     round(p66),
        "invalid_count":   0,
        "invalid_summary": [],
    }


# ─────────────────────────────────────────────────────────────
# OBAT  (Tab 3)
# ─────────────────────────────────────────────────────────────
def get_obat(filters: dict) -> dict:
    q = _base_q(filters)

    rows = (
        q.with_entities(
            Distribution.nama_obat_jadi.label("obat"),
            func.sum(Distribution.jumlah).label("total"),
        )
        .filter(Distribution.nama_obat_jadi.isnot(None))
        .group_by(Distribution.nama_obat_jadi)
        .order_by(func.sum(Distribution.jumlah).desc())
        .all()
    )

    grand = sum(_f(r.total) for r in rows) or 1
    obat_list = [
        {
            "rank":   i,
            "obat":   _s(r.obat),
            "jumlah": _f(r.total),
            "persen": round(_f(r.total) / grand * 100, 2),
        }
        for i, r in enumerate(rows, 1)
    ]

    return {
        "obat_list":         obat_list,
        "invalid_obat":      [],
        "invalid_total_qty": 0,
        "invalid_total_trx": 0,
    }


# ─────────────────────────────────────────────────────────────
# PRODUSEN  (Tab 4)
# ─────────────────────────────────────────────────────────────
def get_produsen(filters: dict) -> dict:
    q = _base_q(filters)

    rows = (
        q.with_entities(
            Distribution.produsen_obat_jadi.label("produsen"),
            func.sum(Distribution.jumlah).label("total"),
        )
        .filter(Distribution.produsen_obat_jadi.isnot(None))
        .group_by(Distribution.produsen_obat_jadi)
        .order_by(func.sum(Distribution.jumlah).desc())
        .all()
    )

    grand = sum(_f(r.total) for r in rows) or 1
    produsen_list = [
        {
            "produsen": _s(r.produsen),
            "jumlah":   _f(r.total),
            "persen":   round(_f(r.total) / grand * 100, 2),
        }
        for r in rows
    ]

    today_str = datetime.today().strftime("%Y-%m-%d")
    batas_str = (datetime.today() + timedelta(days=90)).strftime("%Y-%m-%d")

    exp_rows = (
        q.with_entities(
            Distribution.produsen_obat_jadi.label("produsen"),
            Distribution.nama_obat_jadi.label("obat"),
            func.sum(Distribution.jumlah).label("total"),
        )
        .filter(
            Distribution.tanggal_kedaluwarsa.isnot(None),
            Distribution.tanggal_kedaluwarsa > today_str,
            Distribution.tanggal_kedaluwarsa <= batas_str,
        )
        .group_by(Distribution.produsen_obat_jadi, Distribution.nama_obat_jadi)
        .order_by(func.sum(Distribution.jumlah).desc())
        .all()
    )

    return {
        "produsen_list": produsen_list,
        "produsen_expired": [
            {"produsen": _s(r.produsen), "obat": _s(r.obat), "jumlah": _f(r.total)}
            for r in exp_rows
        ],
        "produsen_names": [r["produsen"] for r in produsen_list if r["produsen"] != "-"],
    }


def get_produsen_expired(filters: dict, nama: str) -> list:
    q = _base_q(filters)
    today_str = datetime.today().strftime("%Y-%m-%d")
    batas_str = (datetime.today() + timedelta(days=90)).strftime("%Y-%m-%d")

    rows = (
        q.with_entities(
            Distribution.produsen_obat_jadi.label("produsen"),
            Distribution.nama_obat_jadi.label("obat"),
            func.sum(Distribution.jumlah).label("total"),
        )
        .filter(
            Distribution.produsen_obat_jadi == nama,
            Distribution.tanggal_kedaluwarsa.isnot(None),
            Distribution.tanggal_kedaluwarsa > today_str,
            Distribution.tanggal_kedaluwarsa <= batas_str,
        )
        .group_by(Distribution.produsen_obat_jadi, Distribution.nama_obat_jadi)
        .order_by(func.sum(Distribution.jumlah).desc())
        .all()
    )

    return [
        {"produsen": _s(r.produsen), "obat": _s(r.obat), "jumlah": _f(r.total)}
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────
# PBF  (Tab 5)
# ─────────────────────────────────────────────────────────────
def get_pbf(filters: dict) -> dict:
    q = _base_q(filters)

    rows = (
        q.with_entities(
            Distribution.nama_pbf.label("pbf"),
            func.sum(Distribution.jumlah).label("total"),
            func.count(Distribution.id).label("trx"),
        )
        .filter(Distribution.nama_pbf.isnot(None), Distribution.nama_pbf != "")
        .group_by(Distribution.nama_pbf)
        .order_by(func.sum(Distribution.jumlah).desc())
        .all()
    )

    pbf_list = [
        {
            "pbf_clean":        _s(r.pbf),
            "total_distribusi": _f(r.total),
            "total_transaksi":  r.trx or 0,
        }
        for r in rows
    ]

    return {
        "pbf_list":     pbf_list,
        "top_pbf":      pbf_list[:10],
        "top_pbf_name": pbf_list[0]["pbf_clean"] if pbf_list else "-",
        "total_pbf":    len(pbf_list),
    }


def search_pbf(filters: dict, query: str) -> list:
    all_pbf = get_pbf(filters)["pbf_list"]
    if not query:
        return all_pbf
    q = query.lower()
    return [r for r in all_pbf if q in r["pbf_clean"].lower()][:50]


# ─────────────────────────────────────────────────────────────
# SARANA  (Tab 6)
# ─────────────────────────────────────────────────────────────
def get_sarana(filters: dict) -> dict:
    q = _base_q(filters)

    rows = (
        q.with_entities(
            Distribution.jenis_sarana.label("jenis"),
            func.sum(Distribution.jumlah).label("total"),
        )
        .filter(Distribution.jenis_sarana.isnot(None), Distribution.jenis_sarana != "")
        .group_by(Distribution.jenis_sarana)
        .order_by(func.sum(Distribution.jumlah).desc())
        .all()
    )

    grand = sum(_f(r.total) for r in rows) or 1
    sarana_list = [
        {
            "jenis_sarana": _s(r.jenis),
            "jumlah":       _f(r.total),
            "persen":       round(_f(r.total) / grand * 100, 2),
        }
        for r in rows
    ]

    detail_rows = (
        q.with_entities(
            Distribution.jenis_sarana.label("jenis"),
            Distribution.tujuan_penyaluran.label("apotek"),
            Distribution.nama_kota_kab_tujuan.label("kota"),
            func.sum(Distribution.jumlah).label("total"),
            func.count(Distribution.id).label("trx"),
        )
        .filter(Distribution.jenis_sarana.isnot(None))
        .group_by(
            Distribution.jenis_sarana,
            Distribution.tujuan_penyaluran,
            Distribution.nama_kota_kab_tujuan,
        )
        .order_by(func.sum(Distribution.jumlah).desc())
        .limit(500)
        .all()
    )

    top = sarana_list[0] if sarana_list else {}
    return {
        "sarana_list": sarana_list,
        "detail_sarana": [
            {
                "jenis_sarana":     _s(r.jenis),
                "apotek":           _s(r.apotek),
                "kota":             _s(r.kota),
                "total_distribusi": _f(r.total),
                "transaksi":        r.trx or 0,
            }
            for r in detail_rows
        ],
        "total_jenis_sarana":  len(sarana_list),
        "sarana_terbanyak":    top.get("jenis_sarana", "-"),
        "distribusi_terbesar": top.get("jumlah", 0),
    }


# ─────────────────────────────────────────────────────────────
# PENCARIAN SARANA  (Tab 7)
# ─────────────────────────────────────────────────────────────
def search_sarana(filters: dict, query: str) -> dict:
    if not query or len(query) < 3:
        return {"results": [], "query": query}

    q = _base_q(filters)
    rows = (
        q.with_entities(Distribution.tujuan_penyaluran)
        .filter(Distribution.tujuan_penyaluran.ilike(f"%{query}%"))
        .distinct()
        .limit(50)
        .all()
    )
    return {"results": [_s(r[0]) for r in rows if r[0]], "query": query}


def get_sarana_detail(filters: dict, apotek: str) -> dict:
    q = _base_q(filters).filter(Distribution.tujuan_penyaluran == apotek)

    total_trx  = q.count()
    total_qty  = q.with_entities(func.sum(Distribution.jumlah)).scalar() or 0
    total_obat = q.with_entities(
        func.count(distinct(Distribution.nama_obat_jadi))
    ).scalar() or 0

    today_str = datetime.today().strftime("%Y-%m-%d")
    batas_str = (datetime.today() + timedelta(days=90)).strftime("%Y-%m-%d")
    hampir = q.filter(
        Distribution.tanggal_kedaluwarsa.isnot(None),
        Distribution.tanggal_kedaluwarsa > today_str,
        Distribution.tanggal_kedaluwarsa <= batas_str,
    ).count()

    obat_rows = (
        q.with_entities(
            Distribution.nama_obat_jadi.label("obat"),
            func.sum(Distribution.jumlah).label("total"),
            func.count(Distribution.id).label("trx"),
        )
        .group_by(Distribution.nama_obat_jadi)
        .order_by(func.sum(Distribution.jumlah).desc())
        .all()
    )

    obat_list = [
        {
            "obat":             _s(r.obat),
            "total_distribusi": _f(r.total),
            "jumlah_transaksi": r.trx or 0,
        }
        for r in obat_rows
    ]

    return {
        "apotek": apotek,
        "kpi": {
            "total_transaksi":  total_trx,
            "total_distribusi": float(total_qty),
            "jenis_obat":       total_obat,
            "hampir_expired":   hampir,
        },
        "obat_list":   obat_list,
        "top10_chart": obat_list[:10],
    }


def get_obat_transaksi(filters: dict, apotek: str, obat: str) -> dict:
    q = _base_q(filters).filter(
        Distribution.tujuan_penyaluran == apotek,
        Distribution.nama_obat_jadi    == obat,
    )

    total_trx  = q.count()
    total_qty  = q.with_entities(func.sum(Distribution.jumlah)).scalar() or 0
    total_prod = q.with_entities(
        func.count(distinct(Distribution.produsen_obat_jadi))
    ).scalar() or 0

    today_str = datetime.today().strftime("%Y-%m-%d")
    batas_str = (datetime.today() + timedelta(days=90)).strftime("%Y-%m-%d")
    hampir = q.filter(
        Distribution.tanggal_kedaluwarsa.isnot(None),
        Distribution.tanggal_kedaluwarsa > today_str,
        Distribution.tanggal_kedaluwarsa <= batas_str,
    ).count()

    trx_rows = (
        q.with_entities(
            Distribution.tanggal_penyaluran.label("tanggal"),
            Distribution.jumlah,
            Distribution.produsen_obat_jadi.label("produsen"),
            Distribution.nama_pbf.label("pbf_clean"),
            Distribution.tanggal_kedaluwarsa.label("expired"),
        )
        .order_by(Distribution.tanggal_penyaluran.desc())
        .limit(200)
        .all()
    )

    pbf_rows = (
        q.with_entities(
            Distribution.nama_pbf.label("pbf_clean"),
            func.sum(Distribution.jumlah).label("total"),
            func.count(Distribution.id).label("trx"),
        )
        .group_by(Distribution.nama_pbf)
        .order_by(func.sum(Distribution.jumlah))
        .all()
    )

    return {
        "apotek": apotek,
        "obat":   obat,
        "kpi": {
            "total_distribusi": float(total_qty),
            "total_transaksi":  total_trx,
            "jumlah_produsen":  total_prod,
            "hampir_expired":   hampir,
        },
        "transaksi": [
            {
                "tanggal":   _s(r.tanggal),
                "jumlah":    _f(r.jumlah),
                "produsen":  _s(r.produsen),
                "pbf_clean": _s(r.pbf_clean),
                "expired":   _s(r.expired),
            }
            for r in trx_rows
        ],
        "pbf_chart": [
            {"pbf_clean": _s(r.pbf_clean), "total": _f(r.total), "transaksi": r.trx or 0}
            for r in pbf_rows
        ],
    }


# ─────────────────────────────────────────────────────────────
# EXPORT  (Tab 8)
# ─────────────────────────────────────────────────────────────
KOLOM_MAP = {
    "Jenis Transaksi":      "jenis_transaksi",
    "Tujuan Penyaluran":    "tujuan_penyaluran",
    "Alamat Tujuan":        "alamat_tujuan",
    "Nama Kota/Kab Tujuan": "nama_kota_kab_tujuan",
    "Nama Provinsi Tujuan": "nama_provinsi_tujuan",
    "Nama Zat Aktif":       "nama_zat_aktif",
    "Nama Obat Jadi":       "nama_obat_jadi",
    "Produsen Obat Jadi":   "produsen_obat_jadi",
    "Nama PBF":             "nama_pbf",
    "Provinsi":             "provinsi",
    "Kabupaten/Kota":       "kabupaten_kota",
    "Jenis Sarana":         "jenis_sarana",
    "No. Faktur":           "no_faktur",
    "Tanggal Penyaluran":   "tanggal_penyaluran",
    "Batch":                "batch",
    "Jumlah":               "jumlah",
    "Satuan":               "satuan",
    "Tanggal Kedaluwarsa":  "tanggal_kedaluwarsa",
    "Keterangan":           "keterangan",
    "Kategori Obat":        "kategori_obat",
}

KOLOM_TERSEDIA = list(KOLOM_MAP.keys())


def get_export_df(filters: dict, selected_cols: list, mode: str, n_terbaru: int):
    q = _base_q(filters)
    if mode == "terbaru":
        q = q.order_by(Distribution.tanggal_penyaluran.desc()).limit(n_terbaru)
    else:
        q = q.limit(100_000)

    rows = q.all()
    cols = selected_cols or KOLOM_TERSEDIA
    selected_attrs = [
        (label, KOLOM_MAP[label])
        for label in cols
        if label in KOLOM_MAP and hasattr(Distribution, KOLOM_MAP[label])
    ]
    if not selected_attrs:
        selected_attrs = [(k, v) for k, v in KOLOM_MAP.items() if hasattr(Distribution, v)]

    data = [
        {label: (getattr(row, attr, "") or "") for label, attr in selected_attrs}
        for row in rows
    ]
    return data, [s[0] for s in selected_attrs]


# ─────────────────────────────────────────────────────────────
# KPI LAMA — backward compat untuk route lama
# ─────────────────────────────────────────────────────────────
def get_kpi():
    total_trx  = db.session.query(func.count(Distribution.id)).scalar() or 0
    total_qty  = db.session.query(func.sum(Distribution.jumlah)).scalar() or 0
    total_obat = db.session.query(func.count(distinct(Distribution.nama_obat_jadi))).scalar() or 0
    return {
        "total_transaksi":  total_trx,
        "total_distribusi": float(total_qty),
        "total_obat":       total_obat,
    }


def get_top_provinsi():
    rows = (
        db.session.query(
            func.initcap(Distribution.nama_provinsi_tujuan).label("prov"),
            func.sum(Distribution.jumlah).label("total"),
        )
        .filter(Distribution.nama_provinsi_tujuan.isnot(None))
        .group_by(func.initcap(Distribution.nama_provinsi_tujuan))
        .order_by(func.sum(Distribution.jumlah).desc())
        .limit(10)
        .all()
    )
    return [{"provinsi": _s(r.prov), "total": _f(r.total)} for r in rows]


def get_top_kota():
    rows = (
        db.session.query(
            func.initcap(Distribution.nama_kota_kab_tujuan).label("kota"),
            func.sum(Distribution.jumlah).label("total"),
        )
        .filter(Distribution.nama_kota_kab_tujuan.isnot(None))
        .group_by(func.initcap(Distribution.nama_kota_kab_tujuan))
        .order_by(func.sum(Distribution.jumlah).desc())
        .limit(10)
        .all()
    )
    return [{"kota": _s(r.kota), "total": _f(r.total)} for r in rows]


def get_top_pbf():
    return get_pbf({})["top_pbf"]