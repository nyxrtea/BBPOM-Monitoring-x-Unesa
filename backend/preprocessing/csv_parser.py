"""
backend/preprocessing/csv_parser.py
──────────────────────────────────────
Handles BPOM CSV and Excel uploads.

Supported formats
─────────────────
• BPOM "wrapped-row" CSV:
    Header:  Jenis Transaksi,Tujuan Penyaluran,...;;
    Row:     "Dalam Negeri,apotek k24,...,orlistat";;
• Plain CSV (comma, semicolon, or tab separated — auto-detected)
• Excel: .xlsx  (openpyxl)
         .xls   (xlrd)

Entry points
────────────
parse_bpom_file(source, filename=None) → pd.DataFrame
    Smart dispatcher: routes to CSV or Excel parser.

parse_bpom_csv(source) → pd.DataFrame
    CSV-only path (kept for backwards-compat).
"""

import csv
import io
import os
import re
from datetime import datetime

import pandas as pd


# ─────────────────────────────────────────────────────────────
# Column map  (original display header → internal snake_case)
# The lookup is done case-insensitively, so casing variants like
# "Nama Provinsi TUjuan" still map correctly.
# ─────────────────────────────────────────────────────────────
COLUMN_MAP = {
    "Jenis Transaksi":       "jenis_transaksi",
    "Tujuan Penyaluran":     "tujuan_penyaluran",
    "Alamat Tujuan":         "alamat_tujuan",
    "Nama Kota/Kab Tujuan":  "nama_kota_kab_tujuan",
    "Nama Provinsi Tujuan":  "nama_provinsi_tujuan",
    "Nama Zat Aktif":        "nama_zat_aktif",
    "Nama Obat Jadi":        "nama_obat_jadi",
    "Produsen Obat Jadi":    "produsen_obat_jadi",
    "Nama PBF":              "nama_pbf",
    "Provinsi":              "provinsi",
    "Kabupaten/Kota":        "kabupaten_kota",
    "Jenis Sarana":          "jenis_sarana",
    "No. Faktur":            "no_faktur",
    "Tanggal Penyaluran":    "tanggal_penyaluran",
    "Batch":                 "batch",
    "Jumlah":                "jumlah",
    "Satuan":                "satuan",
    "Tanggal Kedaluwarsa":   "tanggal_kedaluwarsa",
    "Keterangan":            "keterangan",
    "Kategori Obat":         "kategori_obat",
    # trailing ;; variant
    "Kategori Obat;;":       "kategori_obat",
}

# Pre-compute a lowercase → snake_case lookup for case-insensitive matching
_COLUMN_MAP_LOWER: dict[str, str] = {k.lower().strip(): v for k, v in COLUMN_MAP.items()}


# ─────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────

def clean_header(col_name: str) -> str:
    """Lowercase, replace non-alphanumeric runs with single underscore."""
    cleaned = re.sub(r'[^a-zA-Z0-9]', '_', str(col_name))
    cleaned = re.sub(r'_+', '_', cleaned)
    cleaned = cleaned.strip('_')
    return cleaned.lower()


def _strip_annotation(header: str) -> str:
    """
    Strip trailing parenthetical suffixes like '(Cleaned)', '(Pcs)' so
    that variant export formats still hit COLUMN_MAP.
    """
    return re.sub(r"\s*\([^)]*\)\s*$", "", header).strip()


def _map_column(col: str) -> str:
    """
    Return the normalised snake_case name for a raw header string.

    Priority:
      1. Annotation-stripped, case-insensitive COLUMN_MAP match
      2. Exact COLUMN_MAP match (handles ";;" variant)
      3. clean_header() fallback  → always produces a usable snake_case name
    """
    base = _strip_annotation(col)
    # Case-insensitive lookup (covers "Nama Provinsi TUjuan" → "nama_provinsi_tujuan")
    mapped = _COLUMN_MAP_LOWER.get(base.lower().strip())
    if mapped:
        return mapped
    # Exact match (catches "Kategori Obat;;" etc.)
    if col in COLUMN_MAP:
        return COLUMN_MAP[col]
    # Fallback: convert the raw header to snake_case
    return clean_header(col)


_DATE_INPUT_FORMATS = [
    "%d/%m/%Y",   # 31/01/2025  — primary target (Indonesian/European style)
    "%d/%m/%y",   # 31/01/25
    "%d-%m-%Y",   # 31-01-2025
    "%d-%m-%y",   # 31-01-25
    "%Y/%m/%d",   # 2025/01/31
    "%Y%m%d",     # 20250131    (compact)
    "%m/%d/%Y",   # 01/31/2025  (US-style fallback, tried last)
]


def _normalize_date(val) -> str:
    """
    Convert a date string to ISO yyyy-mm-dd format.

    Handles common input styles found in BPOM / Indonesian Excel exports:
      dd/mm/yyyy   →  yyyy-mm-dd   (primary target)
      dd-mm-yyyy   →  yyyy-mm-dd
      yyyy/mm/dd   →  yyyy-mm-dd
      yyyy-mm-dd   →  unchanged    (already correct)
      empty/NaN    →  ''           (stored as empty string)

    Non-parseable values are returned unchanged so the pipeline never
    silently drops data.
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    v = str(val).strip()
    if not v or v.lower() in ("nan", "none", "null", "-", ""):
        return ""

    # Already yyyy-mm-dd — return immediately
    if re.match(r"^\d{4}-\d{2}-\d{2}$", v):
        return v

    # Try each known format in priority order (dayfirst variants first)
    for fmt in _DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(v, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Last-resort: let pandas guess, with dayfirst=True so dd/mm wins over mm/dd
    try:
        return pd.to_datetime(v, dayfirst=True, errors="raise").strftime("%Y-%m-%d")
    except Exception:
        pass

    return v   # Unparseable — keep original rather than silently erase


def _apply_common_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce numeric and date columns to correct dtypes."""
    if "jumlah" in df.columns:
        df["jumlah"] = pd.to_numeric(df["jumlah"], errors="coerce").fillna(0.0)

    # Normalize date columns to yyyy-mm-dd regardless of input format
    for col in ("tanggal_penyaluran", "tanggal_kedaluwarsa"):
        if col in df.columns:
            df[col] = df[col].apply(_normalize_date)

    str_cols = [c for c in df.columns if c != "jumlah"]
    for col in str_cols:
        df[col] = df[col].astype(str).str.strip().replace("nan", "")

    return df


# ─────────────────────────────────────────────────────────────
# CSV helpers
# ─────────────────────────────────────────────────────────────

def _detect_delimiter(header_line: str) -> str:
    """
    Detect the field delimiter used in *header_line*.

    Strategy
    --------
    1. Try csv.Sniffer (works well for semicolons and tabs).
    2. Count occurrences of likely delimiters and pick the winner.
    3. Fall back to comma.
    """
    try:
        dialect = csv.Sniffer().sniff(header_line, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        pass

    counts = {d: header_line.count(d) for d in (",", ";", "\t", "|")}
    best = max(counts, key=counts.get)
    if counts[best] > 0:
        return best
    return ","


def _clean_row_text(raw: str) -> str:
    """Strip surrounding quotes and trailing ;; from a BPOM-style data row."""
    raw = raw.strip()
    if raw.endswith(";;"):
        raw = raw[:-2].strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    raw = raw.replace('""', '"')
    return raw


def _read_content(source) -> str:
    """Read source (path or file-like) into a string, auto-detecting encoding."""
    if hasattr(source, "read"):
        content = source.read()
        if isinstance(content, bytes):
            try:
                return content.decode("utf-8-sig")
            except UnicodeDecodeError:
                return content.decode("latin-1")
        return content
    else:
        try:
            with open(source, encoding="utf-8-sig") as f:
                return f.read()
        except UnicodeDecodeError:
            with open(source, encoding="latin-1") as f:
                return f.read()


# ─────────────────────────────────────────────────────────────
# CSV parser
# ─────────────────────────────────────────────────────────────

def parse_bpom_csv(source) -> pd.DataFrame:
    """
    Parse a BPOM-format or plain CSV file.

    Supports:
    • BPOM "wrapped-row" format (rows enclosed in double-quotes + ;; suffix)
    • Plain CSV with any delimiter (comma / semicolon / tab — auto-detected)

    Parameters
    ----------
    source : str (file path) or file-like object

    Returns
    -------
    pd.DataFrame with normalised snake_case column names and correct dtypes.
    """
    content = _read_content(source)

    lines = content.splitlines()
    if not lines:
        raise ValueError("CSV file is empty")

    # ── Parse header ────────────────────────────────────────
    raw_header = lines[0]
    if raw_header.endswith(";;"):
        raw_header = raw_header[:-2]

    # Auto-detect delimiter BEFORE splitting
    delimiter = _detect_delimiter(raw_header)
    header_cols = [h.strip() for h in raw_header.split(delimiter)]

    # ── Detect row style: BPOM-wrapped vs plain ──────────────
    # BPOM rows look like: "val1,val2,val3";;
    # Plain rows look like: val1<delim>val2<delim>val3
    first_data = next((l.strip() for l in lines[1:] if l.strip()), "")
    is_bpom_wrapped = first_data.startswith('"') and (
        first_data.endswith(';;') or first_data.endswith('"')
    )

    # ── Parse data rows ──────────────────────────────────────
    records = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue

        if is_bpom_wrapped:
            # BPOM wrapped format — inner delimiter is always comma
            cleaned = _clean_row_text(line)
            try:
                row_vals = next(csv.reader(io.StringIO(cleaned)))
            except StopIteration:
                continue
        else:
            # Plain CSV — use the detected delimiter
            try:
                row_vals = next(csv.reader(io.StringIO(line), delimiter=delimiter))
            except StopIteration:
                continue

        # Align length to header
        if len(row_vals) < len(header_cols):
            row_vals += [""] * (len(header_cols) - len(row_vals))
        elif len(row_vals) > len(header_cols):
            row_vals = row_vals[: len(header_cols)]

        records.append(dict(zip(header_cols, row_vals)))

    df = pd.DataFrame(records)

    # ── Rename columns to normalised names ──────────────────
    rename = {col: _map_column(col) for col in df.columns}
    df.rename(columns=rename, inplace=True)

    return _apply_common_dtypes(df)


# ─────────────────────────────────────────────────────────────
# Excel parser
# ─────────────────────────────────────────────────────────────

def parse_excel_bpom(source, filename: str = "") -> pd.DataFrame:
    """
    Parse an Excel file (.xlsx or .xls) into a normalised DataFrame.

    Parameters
    ----------
    source   : str (file path) or file-like object
    filename : original filename, used to pick the engine when source is file-like

    Returns
    -------
    pd.DataFrame with normalised snake_case column names and correct dtypes.
    """
    ext = ""
    if isinstance(source, str):
        ext = os.path.splitext(source)[1].lower()
    elif filename:
        ext = os.path.splitext(filename)[1].lower()

    if ext == ".xls":
        df = pd.read_excel(source, engine="xlrd", dtype=str)
    else:
        df = pd.read_excel(source, engine="openpyxl", dtype=str)

    # Rename all columns (NaN column names from merged headers → "unnamed_N")
    rename = {}
    for col in df.columns:
        raw = str(col) if not pd.isna(col) else f"unnamed_{list(df.columns).index(col)}"
        rename[col] = _map_column(raw)
    df.rename(columns=rename, inplace=True)

    # Drop fully-empty columns produced by empty Excel cells in the header row
    df.dropna(axis=1, how="all", inplace=True)

    return _apply_common_dtypes(df)


# ─────────────────────────────────────────────────────────────
# Smart dispatcher
# ─────────────────────────────────────────────────────────────

def parse_bpom_file(source, filename: str = "") -> pd.DataFrame:
    """
    Auto-detect format and parse.

    Parameters
    ----------
    source   : str (file path) or file-like object
    filename : original filename (used to pick the parser when source is file-like)

    Returns
    -------
    pd.DataFrame with normalised column names and correct dtypes.
    """
    # Determine extension
    ext = ""
    if isinstance(source, str):
        ext = os.path.splitext(source)[1].lower()
    elif filename:
        ext = os.path.splitext(filename)[1].lower()

    if ext in (".xlsx", ".xls", ".xlsm", ".ods"):
        return parse_excel_bpom(source, filename=filename)

    # Default → CSV (handles both BPOM-wrapped and plain)
    return parse_bpom_csv(source)
