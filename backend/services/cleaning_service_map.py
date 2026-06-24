"""
backend/services/cleaning_service.py
──────────────────────────────────────
Pipeline:
  parse_bpom_csv()       → correct column names, jumlah as float
  run_pipeline()         → 6-step cleaning/labeling (see processing_pipeline_main)
  drop_duplicates()      → remove exact duplicates

Returns (before_df, cleaned_df, summary_dict, process_log_str)

summary keys
────────────
  rows_before          int
  rows_after           int
  duplicates_removed   int
  empty_city_columns   int
  renamed_columns      int
  jenis_sarana_created int
  sarana_distribution  dict[str, int]  — jenis_sarana → row count
  null_summary         dict[str, int]  — column → empty/NaN row count (non-zero only)
  kategori_jenis_obat  str
  preview_data         list[dict]      — first 5 cleaned rows (human-readable)
"""

import pandas as pd

from backend.preprocessing.csv_parser               import parse_bpom_csv
from backend.preprocessing.processing_pipeline_main import run_pipeline


# ─────────────────────────────────────────────────────────────
# Column map for preview_data
# ─────────────────────────────────────────────────────────────
_PREVIEW_COL_MAP: list[tuple[str, str, int | None]] = [
    ("tujuan_penyaluran",    "Tujuan Penyaluran", 35),
    ("jenis_sarana",         "Jenis Sarana",      None),
    ("nama_obat_jadi",       "Nama Obat",         30),
    ("kategori_obat",        "Kategori Obat",     None),
    ("jumlah",               "Jumlah",            None),
    ("satuan",               "Satuan",            None),
    ("nama_kota_kab_tujuan", "Kota/Kab Tujuan",  None),
    ("nama_provinsi_tujuan", "Provinsi Tujuan",  None),
    ("tanggal_penyaluran",   "Tgl Penyaluran",   None),
]

# Columns checked for null/empty in the null_summary report
_NULL_CHECK_COLS = [
    "nama_kota_kab_tujuan",
    "nama_provinsi_tujuan",
    "tujuan_penyaluran",
    "alamat_tujuan",
    "nama_obat_jadi",
    "nama_zat_aktif",
    "jenis_sarana",
    "kategori_obat",
    "jumlah",
    "satuan",
    "tanggal_penyaluran",
    "tanggal_kedaluwarsa",
    "no_faktur",
]


# ─────────────────────────────────────────────────────────────
# Safe value helpers
# ─────────────────────────────────────────────────────────────
def _str(val, maxlen: int = 255) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "null"):
        return ""
    return s[:maxlen]


def _float(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _build_preview_data(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, series in df.head(5).iterrows():
        entry = {}
        for col, label, maxlen in _PREVIEW_COL_MAP:
            if col not in df.columns:
                continue
            raw = series[col]
            val = "-" if (raw is None or (isinstance(raw, float) and pd.isna(raw))) \
                      else str(raw).strip()
            if val.lower() in ("nan", "none", "null", ""):
                val = "-"
            if maxlen and len(val) > maxlen:
                val = val[:maxlen] + "…"
            entry[label] = val
        rows.append(entry)
    return rows


def _build_null_summary(df: pd.DataFrame) -> dict[str, int]:
    """
    Count empty/NaN cells per column for the columns in _NULL_CHECK_COLS.
    Returns only columns that have at least one empty value, sorted by count.
    """
    null_counts = {}
    _empty = {"", "nan", "none", "null", "-"}
    for col in _NULL_CHECK_COLS:
        if col not in df.columns:
            continue
        n_null = int(
            df[col].isna().sum()
            + df[col].astype(str).str.strip().str.lower().isin(_empty).sum()
        )
        if n_null > 0:
            null_counts[col] = n_null
    return dict(sorted(null_counts.items(), key=lambda x: -x[1]))


# ─────────────────────────────────────────────────────────────
# Main cleaning function
# ─────────────────────────────────────────────────────────────
def clean_dataframe(
    file_source,
    path_json: str = None,
    kategori_jenis_obat: str = "",
) -> tuple[pd.DataFrame, pd.DataFrame, dict, str]:
    """
    Parameters
    ----------
    file_source : str | file-like
    path_json   : path to master_wilayah.json
    kategori_jenis_obat : user-entered drug label (max 100 chars)

    Returns
    -------
    (before_df, cleaned_df, summary, process_log)
    """

    # ── 1. Parse CSV ─────────────────────────────────────────
    df        = parse_bpom_csv(file_source)
    before_df = df.copy()
    summary: dict = {
        "rows_before":          len(df),
        "rows_after":           0,
        "duplicates_removed":   0,
        "empty_city_columns":   0,
        "renamed_columns":      0,
        "jenis_sarana_created": 0,
        "sarana_distribution":  {},
        "null_summary":         {},
        "kategori_jenis_obat":  kategori_jenis_obat,
        "preview_data":         [],
    }

    # ── 2. Query DataMatchModelAlamat from PostgreSQL ─────────
    # Passed to run_pipeline → Automatisasi_Fill_Tujuan_Lokasi_Sarana.
    # If the DB is unavailable (no Flask context, empty table, etc.)
    # the class falls back gracefully to no-DB mode.
    db_records = None
    try:
        from backend.models.data_match_model_alamat import DataMatchModelAlamat
        db_records = DataMatchModelAlamat.build_lookup_df()
    except Exception as exc:
        print(f"[WARNING] Tidak dapat memuat data_match_model_alamat: {exc}")

    # ── 3. Run the 6-step pipeline ────────────────────────────
    cleaned_df, process_log = run_pipeline(
        df.copy(),
        path_json=path_json,
        kategori_obat=kategori_jenis_obat,
        db_records=db_records,
    )

    summary["renamed_columns"] = sum(
        o != n
        for o, n in zip(list(before_df.columns), list(cleaned_df.columns))
    )

    # ── 4. Jenis sarana stats ─────────────────────────────────
    if "jenis_sarana" not in cleaned_df.columns:
        cleaned_df["jenis_sarana"] = "Lainnya"

    summary["jenis_sarana_created"] = cleaned_df["jenis_sarana"].nunique()
    summary["sarana_distribution"]  = dict(
        cleaned_df["jenis_sarana"].value_counts().items()
    )

    # ── 5. Null / empty column summary ───────────────────────
    summary["null_summary"] = _build_null_summary(cleaned_df)

    # ── 6. Preview data ───────────────────────────────────────
    summary["preview_data"] = _build_preview_data(cleaned_df)

    # ── 7. Remove duplicates ──────────────────────────────────
    rows_before_dedup             = len(cleaned_df)
    cleaned_df                    = cleaned_df.drop_duplicates()
    summary["duplicates_removed"] = rows_before_dedup - len(cleaned_df)
    summary["empty_city_columns"] = int(
        (cleaned_df.get("nama_kota_kab_tujuan", pd.Series(dtype=str))
         .astype(str).str.strip()
         .isin(["", "nan", "None"])).sum()
    )
    summary["rows_after"] = len(cleaned_df)

    # ── Summary log ───────────────────────────────────────────
    dist_lines = "\n".join(
        f"    {nama:<25}: {jml:,}"
        for nama, jml in summary["sarana_distribution"].items()
    )
    null_lines = "\n".join(
        f"    {col:<30}: {cnt:,}"
        for col, cnt in summary["null_summary"].items()
    ) or "    (tidak ada)"

    process_log += (
        f"\n{'='*55}\n"
        f"  RINGKASAN CLEANING SERVICE\n"
        f"{'='*55}\n"
        f"  Baris sebelum cleaning  : {summary['rows_before']:,}\n"
        f"  Duplikat dihapus        : {summary['duplicates_removed']:,}\n"
        f"  Baris setelah cleaning  : {summary['rows_after']:,}\n"
        f"  Kota/Kab masih kosong   : {summary['empty_city_columns']:,}\n"
        f"  Kategori Jenis Obat     : {kategori_jenis_obat or '(auto)'}\n"
        f"  Distribusi Jenis Sarana :\n{dist_lines}\n"
        f"  Kolom Kosong / NaN      :\n{null_lines}\n"
        f"{'='*55}\n"
    )

    return before_df, cleaned_df, summary, process_log


# ─────────────────────────────────────────────────────────────
# Row → Distribution model kwargs
# ─────────────────────────────────────────────────────────────
def row_to_model_kwargs(row, upload_session_id: int) -> dict:
    return dict(
        upload_session_id    = upload_session_id,
        jumlah               = _float(row.get("jumlah")),
        tanggal_penyaluran   = _str(row.get("tanggal_penyaluran")),
        tanggal_kedaluwarsa  = _str(row.get("tanggal_kedaluwarsa")),
        nama_zat_aktif       = _str(row.get("nama_zat_aktif")),
        nama_obat_jadi       = _str(row.get("nama_obat_jadi")),
        produsen_obat_jadi   = _str(row.get("produsen_obat_jadi")),
        nama_pbf             = _str(row.get("nama_pbf")),
        provinsi             = _str(row.get("provinsi")),
        kabupaten_kota       = _str(row.get("kabupaten_kota")),
        jenis_transaksi      = _str(row.get("jenis_transaksi")),
        batch                = _str(row.get("batch")),
        satuan               = _str(row.get("satuan"),        maxlen=255),
        keterangan           = _str(row.get("keterangan"),    maxlen=9999),
        jenis_sarana         = _str(row.get("jenis_sarana")),
        kategori_obat        = _str(row.get("kategori_obat")) or "General",
        no_faktur            = _str(row.get("no_faktur")),
        tujuan_penyaluran    = _str(row.get("tujuan_penyaluran"), maxlen=9999),
        alamat_tujuan        = _str(row.get("alamat_tujuan"),     maxlen=9999),
        nama_kota_kab_tujuan = _str(row.get("nama_kota_kab_tujuan")),
        nama_provinsi_tujuan = _str(row.get("nama_provinsi_tujuan")),
        anomaly_label        = int(row.get("anomaly_label", 1) or 1),
    )
