from __future__ import annotations

import io
import json
import os
import re
import sys
import sqlite3
import contextlib

import pandas as pd

# rapidfuzz is required for Automatisasi_Fill_Tujuan_Lokasi_Sarana.
# If not installed, that class will skip fuzzy matching gracefully.
try:
    from rapidfuzz import process, fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False
    print("[WARNING] rapidfuzz tidak terinstall. "
          "Automatisasi_Fill_Tujuan_Lokasi_Sarana akan dilewati.")


# ─────────────────────────────────────────────────────────────
# DEFAULT PATH for master_wilayah.json
# ─────────────────────────────────────────────────────────────
_HERE         = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_JSON = os.path.join(_HERE, "..", "data", "master_wilayah.json")


# ─────────────────────────────────────────────────────────────
# TEXT NORMALISATION  (shared by multiple classes)
# ─────────────────────────────────────────────────────────────
def normalize_text(text: str) -> str:
    """Uppercase, strip, and collapse internal whitespace."""
    if not text or (isinstance(text, float)):
        return ""
    return re.sub(r'\s+', ' ', str(text).upper()).strip()


# ─────────────────────────────────────────────────────────────
# HEADER NORMALISATION
# ─────────────────────────────────────────────────────────────
def clean_header(col_name: str) -> str:
    """Lowercase, replace non-alphanumeric runs with single underscore."""
    cleaned = re.sub(r'[^a-zA-Z0-9]', '_', col_name)
    cleaned = re.sub(r'_+', '_', cleaned)
    cleaned = cleaned.strip('_')
    return cleaned.lower()


# ─────────────────────────────────────────────────────────────
# CLASS 1 — LABELING JENIS SARANA
# ─────────────────────────────────────────────────────────────
class Labeling_Nama_Sarana:
    """
    Pre-pipeline step: classifies 'tujuan_penyaluran' → 'jenis_sarana'.

    Runs BEFORE Perbaikan_Nama_Sarana so labels are assigned on the raw,
    unmodified facility-name strings which are more recognisable.

    Categories (in priority order — first match wins):
      Instalasi Farmasi  RS / hospital pharmacy units
      Rumah Sakit        all hospital variants
      Puskesmas          public health centres
      Klinik             clinics (general, utama, pratama)
      Balai Pengobatan   health posts, posyandu, polindes
      Apotek             pharmacies
      PBF                pharmaceutical distributors
      Lainnya            catch-all (later re-labeled via jenis_sarana_instansi)
    """

    COL_INPUT  = "tujuan_penyaluran"
    COL_OUTPUT = "jenis_sarana"

    _RULES: list[tuple[str, str]] = [
        (
            "Instalasi Farmasi",
            r'\b(INSTALASI\s*FARMASI|INST\.?\s*FARMASI|IF\s+RS|IF\s+RSUD|IF\s+RSUP)\b',
        ),
        (
            "Rumah Sakit",
            r'\b(RUMAH\s*SAKIT|RS\b|RSU\b|RSUD\b|RSUP\b|RSIA\b|RSAB\b|'
            r'RSK\b|RSI\b|RSD\b|RSB\b|HOSPITAL\b|KLINIK\s*RUMAH\s*SAKIT)\b',
        ),
        (
            "Puskesmas",
            r'\b(PUSKESMAS|PUSTU\b|PKM\b|PONKESDES\b|PUSKESDES\b)\b',
        ),
        (
            "Klinik",
            r'\b(KLINIK\b|KLINIK\s*UTAMA|KLINIK\s*PRATAMA|KLINIK\s*BERSALIN|'
            r'KLINIK\s*GIGI|CLINIC\b)\b',
        ),
        (
            "Balai Pengobatan",
            r'\b(BALAI\s*PENGOBATAN|BALAI\s*KESEHATAN|BP\s*UMUM|'
            r'POSYANDU\b|POLINDES\b|POSKESDES\b)\b',
        ),
        (
            "Apotek",
            r'\b(APOTEK\b|APOTIK\b|APT\b|PHARMACY\b|FARMASI\b)\b',
        ),
        (
            "PBF",
            r'\b(PBF\b|PEDAGANG\s*BESAR\s*FARMASI|DISTRIBUTOR\s*FARMASI|'
            r'PBAK\b|GROSIR\s*FARMASI)\b',
        ),
    ]

    def __init__(self, extra_rules: list[tuple[str, str]] | None = None):
        rules = list(extra_rules or []) + list(self._RULES)
        self._compiled: list[tuple[str, re.Pattern]] = [
            (label, re.compile(pattern, re.IGNORECASE))
            for label, pattern in rules
        ]

    def classify(self, nama: str) -> str:
        if not nama or str(nama).strip().lower() in ("", "nan", "none", "null"):
            return "Lainnya"
        text = str(nama).upper()
        for label, compiled in self._compiled:
            if compiled.search(text):
                return label
        return "Lainnya"

    def run(
        self,
        df: pd.DataFrame,
        verbose: bool = True,
    ) -> tuple[pd.DataFrame, dict[str, int]]:
        df = df.copy()
        if self.COL_INPUT not in df.columns:
            df[self.COL_OUTPUT] = "Lainnya"
            if verbose:
                print(f"  ⚠  Kolom '{self.COL_INPUT}' tidak ada — semua 'Lainnya'.")
            return df, {"Lainnya": len(df)}

        df[self.COL_OUTPUT] = df[self.COL_INPUT].apply(self.classify)
        distribution: dict[str, int] = df[self.COL_OUTPUT].value_counts().to_dict()

        if verbose:
            labeled   = (df[self.COL_OUTPUT] != "Lainnya").sum()
            unlabeled = (df[self.COL_OUTPUT] == "Lainnya").sum()
            print(f"  Berlabel    : {labeled:,}")
            print(f"  'Lainnya'   : {unlabeled:,}  (akan dicari di tabel instansi)")
            for label, cnt in sorted(distribution.items(), key=lambda x: -x[1]):
                bar = "▓" * min(int(cnt / max(distribution.values()) * 18), 18)
                print(f"    {label:<25}: {cnt:>6,}  {bar}")

        return df, distribution


# ─────────────────────────────────────────────────────────────
# CLASS 2 — PERBAIKAN NAMA SARANA
# ─────────────────────────────────────────────────────────────
class Perbaikan_Nama_Sarana:
    """
    Cleans and standardises the 'tujuan_penyaluran' (facility name) column.

    Pipeline: regex_clean → perbaikan_nama_apotek → perbaikan_nama_rs
              → hapus_kata_berulang → perbaikan_nama_pt → clean_text
    """

    COL_SARANA = "tujuan_penyaluran"
    RS_PREFIXES = ['RSAB', 'RSUP', 'RSIA', 'RSUD', 'RSB', 'RSK', 'RSI',
                   'RSD', 'RSU', 'RS']
    BADAN_USAHA = ['PT', 'CV', 'PBF']
    CITY_CODES  = r'\b(SBY|MLG|SDO|JBR|MJK|KDR|JMB|BJN|PSR|BWI|TLG|BGL|PBL|LWG|MJKT|BNYW)\b'

    def __init__(self, path_json: str = None):
        self.kab_to_prov:   dict[str, str] = {}
        self.wilayah_full:  set[str]       = set()
        self.wilayah_base:  set[str]       = set()
        self.wilayah_token: set[str]       = set()
        self.pattern:       re.Pattern     = None
        self._path_json = path_json or _DEFAULT_JSON
        self._load_kamus_wilayah()
        self._build_pattern()

    def _load_kamus_wilayah(self):
        try:
            with open(self._path_json, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.kab_to_prov = {
                k.upper().strip(): v.upper().strip() for k, v in raw.items()
            }
            for kab in self.kab_to_prov:
                self.wilayah_full.add(kab)
                base = re.sub(r'^KOTA\s+', '', kab).strip()
                self.wilayah_base.add(base)
                for token in base.split():
                    self.wilayah_token.add(token)
        except FileNotFoundError:
            pass

    def _build_pattern(self):
        if not self.wilayah_full:
            return
        sorted_phrases = sorted(self.wilayah_full, key=len, reverse=True)
        escaped        = [re.escape(p) for p in sorted_phrases]
        prefix         = r'(KABUPATEN\s+|KAB\.?\s+|KOTA\s+|PROVINSI\s+|PROV\.?\s+)'
        self.pattern   = re.compile(
            r'\b' + prefix + r'(' + '|'.join(escaped) + r')\b', re.IGNORECASE,
        )

    def clean_text(self, text) -> str | None:
        if pd.isna(text):
            return None
        text = str(text)
        text = re.sub(r'[^a-zA-Z0-9\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text.upper()

    def regex_clean(self, df: pd.DataFrame, col: str = None) -> pd.DataFrame:
        col = col or self.COL_SARANA
        df  = df.copy()
        df[col] = df[col].astype(str).str.upper()
        df[col] = df[col].str.replace(r'^(kabupaten|kab)\s*\.?\s*', '', case=False, regex=True)
        df[col] = df[col].str.replace(r'^[0-9]+\s*',          '', regex=True)
        df[col] = df[col].str.replace(r'\b[0-9]{5,}\b\s*',    '', regex=True)
        df[col] = df[col].str.replace(r'^(?:[A-Z]+[0-9]+[A-Z0-9]*|[0-9]+[A-Z]+[A-Z0-9]*)\s*', '', regex=True)
        df[col] = df[col].str.replace(r'\bK[-.\s]*24\b', 'K24',         regex=True)
        df[col] = df[col].str.replace(r'\bK\s*24\b',     'K24',         regex=True)
        df[col] = df[col].str.replace(r'\bKF\b',         'KIMIA FARMA', regex=True)
        df[col] = df[col].str.replace(r'\bAPOTIK\b',     'APOTEK',      regex=True)
        df[col] = df[col].str.replace(r'\bAPT\b',        'APOTEK',      regex=True)
        df[col] = df[col].str.replace(r'\bAP\b',         'APOTEK',      regex=True)
        df[col] = df[col].str.replace(self.CITY_CODES,   '',            regex=True)
        df[col] = df[col].str.replace(r'\s+', ' ', regex=True).str.strip()
        return df

    def perbaikan_nama_apotek(self, df: pd.DataFrame, col: str = None) -> pd.DataFrame:
        col = col or self.COL_SARANA
        # def move_apt(text):
        #     if pd.isna(text):
        #         return text
        #     if 'APOTEK' in str(text):
        #         body = re.sub(r'\bAPOTEK\b', '', text)
        #         return f"APOTEK {re.sub(r'\s+', ' ', body).strip()}".strip()
        #     return text
        def move_apt(text):
            if pd.isna(text):
                return text

            if 'APOTEK' in str(text):
                body = re.sub(r'\bAPOTEK\b', '', text)
                body = re.sub(r'\s+', ' ', body).strip()
                return f"APOTEK {body}".strip()

            return text
        df = df.copy()
        df[col] = df[col].apply(move_apt)
        return df

    def perbaikan_nama_rs(self, df: pd.DataFrame, col: str = None) -> pd.DataFrame:
        col = col or self.COL_SARANA
        # def move_rs(text):
        #     if pd.isna(text):
        #         return text
        #     for prefix in self.RS_PREFIXES:
        #         if re.search(rf'\b{prefix}\b', text):
        #             body = re.sub(rf'\b{prefix}\b', '', text)
        #             return f"{prefix} {re.sub(r'\\s+', ' ', body).strip()}".strip()
        #     return text
        def move_rs(text):
            if pd.isna(text):
                return text
        
            for prefix in self.RS_PREFIXES:
                if re.search(rf'\b{prefix}\b', text):
                    body = re.sub(rf'\b{prefix}\b', '', text)
                    body = re.sub(r'\s+', ' ', body).strip()
                    return f"{prefix} {body}".strip()
        
            return text
        df = df.copy()
        df[col] = df[col].apply(move_rs)
        return df

    def perbaikan_nama_pt(self, df: pd.DataFrame, col: str = None) -> pd.DataFrame:
        col = col or self.COL_SARANA
        # def move_pt(text):
        #     if pd.isna(text):
        #         return text
        #     for prefix in self.BADAN_USAHA:
        #         if re.search(rf'\b{prefix}\b', text):
        #             body = re.sub(rf'\b{prefix}\b', '', text)
        #             return f"{prefix} {re.sub(r'\\s+', ' ', body).strip()}".strip()
        #     return text
        def move_pt(text):
            if pd.isna(text):
                return text
        
            for prefix in self.BADAN_USAHA:
                if re.search(rf'\b{prefix}\b', text):
                    body = re.sub(rf'\b{prefix}\b', '', text)
                    body = re.sub(r'\s+', ' ', body).strip()
                    return f"{prefix} {body}".strip()
        
            return text
        df = df.copy()
        df[col] = df[col].apply(move_pt)
        return df

    def hapus_kata_berulang(self, df: pd.DataFrame, col: str = None) -> pd.DataFrame:
        col = col or self.COL_SARANA
        def dedup(text):
            if pd.isna(text):
                return text
            return ' '.join(dict.fromkeys(str(text).split()))
        df = df.copy()
        df[col] = df[col].apply(dedup)
        return df

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        col = self.COL_SARANA
        df = self.regex_clean(df, col)
        df = self.perbaikan_nama_apotek(df, col)
        df = self.perbaikan_nama_rs(df, col)
        df = self.hapus_kata_berulang(df, col)
        df = self.perbaikan_nama_pt(df, col)
        df[col] = df[col].apply(self.clean_text)
        return df

# ====================== AUTOMATISASI FILL TUJUAN LOKASI SARANA ======================

class Automatisasi_Fill_Tujuan_Lokasi_Sarana:
    COL_TUJUAN = "tujuan_penyaluran"
    COL_ALAMAT = "alamat_tujuan"
    COL_KOTA   = "nama_kota_kab_tujuan"
    COL_PROV   = "nama_provinsi_tujuan"

    PATH_JSON           = "master_wilayah.json"
    PATH_MEMORY_DATA_DB = "data_match_model_alamat.db"

    # ── Thresholds ─────────────────────────────────────────────────────────
    # DB lookup  : WRatio (full-string match against confirmed facility records)
    #   Score=95 when input matches a DB entry that has a leading numeric code  
    #   (e.g. "apotek araya malang" vs "00004 apotek araya malang")
    #   → 90 catches these and still rejects loosely related names
    THRESHOLD_DB_TUJUAN: int = 90
    THRESHOLD_DB_ALAMAT: int = 90

    # JSON extraction : partial_ratio (kota token found inside the text)
    THRESHOLD_TUJUAN:    int = 99
    THRESHOLD_ALAMAT:    int = 96

    def __init__(
        self,
        path_json:  str | None = None,
        path_db:    str | None = None,
        db_records: "pd.DataFrame | None" = None,
    ):
        """
        Parameters
        ----------
        path_json  : path to master_wilayah.json
        path_db    : path to SQLite .db file (CLI / offline fallback)
        db_records : DataFrame from DataMatchModelAlamat.build_lookup_df()
                     (Flask/PostgreSQL path).  If provided, SQLite is ignored.
        """
        # Always resolve to a proper path.
        # _DEFAULT_JSON uses __file__ so it works regardless of CWD.
        self.PATH_JSON           = path_json or _DEFAULT_JSON
        self.PATH_MEMORY_DATA_DB = path_db   or "data_match_model_alamat.db"

        self._db_records_df: "pd.DataFrame | None" = db_records

        # JSON wilayah structures
        self.kab_to_prov: dict[str, str] = {}
        self.kota_list:   list[str]      = []

        # DB lookup structures
        self._lookup_tujuan: dict[str, tuple[str, str]] = {}
        self._lookup_alamat: dict[str, tuple[str, str]] = {}
        self._tujuan_keys:   list[str]                  = []
        self._alamat_keys:   list[str]                  = []

        self._load_kamus_wilayah()
        self._load_db_lookup()

    # ── Private: load JSON ─────────────────────────────────────────────────

    def _load_kamus_wilayah(self) -> None:
        """Load kab/kota → provinsi mapping from master_wilayah.json."""
        try:
            with open(self.PATH_JSON, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.kab_to_prov = {
                normalize_text(k): normalize_text(v)
                for k, v in raw.items()
            }
            self.kota_list = list(self.kab_to_prov.keys())
            print(f"[INFO] JSON '{self.PATH_JSON}' loaded: {len(self.kota_list)} kota/kab.")
        except FileNotFoundError:
            print(
                f"[WARNING] master_wilayah.json tidak ditemukan di '{self.PATH_JSON}'.\n"
                "  Automatisasi_Fill akan berjalan tanpa data JSON wilayah."
            )
        except Exception as exc:
            print(f"[WARNING] Gagal memuat JSON wilayah: {exc}")
        
    # ── Private: load DB ───────────────────────────────────────────────────

    def _load_db_lookup(self) -> None:
        """
        Load confirmed tujuan/alamat → (kota, prov) mappings.

        Source priority:
        1. db_records DataFrame  — passed from Flask/PostgreSQL (preferred)
        2. SQLite file (path_db) — offline / CLI fallback
        """
        # ── Source 1: pre-loaded PostgreSQL DataFrame ──────────
        if self._db_records_df is not None and not self._db_records_df.empty:
            df_ref = self._db_records_df.copy()
            print(f"[INFO] DB lookup: {len(df_ref):,} records dari PostgreSQL.")
        else:
            # ── Source 2: SQLite fallback ──────────────────────
            try:
                conn = sqlite3.connect(self.PATH_MEMORY_DATA_DB)
                tables = pd.read_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'", conn
                )
                if tables.empty:
                    conn.close()
                    print(f"[WARNING] Tidak ada tabel dalam '{self.PATH_MEMORY_DATA_DB}'.")
                    return
                table_name = tables["name"].iloc[0]
                df_ref = pd.read_sql(f"SELECT * FROM [{table_name}]", conn)
                conn.close()
                print(f"[INFO] DB lookup: {len(df_ref):,} records dari SQLite.")
            except Exception as exc:
                print(f"[WARNING] Gagal load DB '{self.PATH_MEMORY_DATA_DB}': {exc}")
                return

        # Keep only rows that have a valid kota value
        has_kota = (
            df_ref[self.COL_KOTA].notna()
            & (df_ref[self.COL_KOTA].astype(str).str.strip() != "")
        )
        valid = df_ref[has_kota].copy()
        valid[self.COL_PROV] = valid[self.COL_PROV].fillna("").astype(str).str.strip()
        valid[self.COL_KOTA] = valid[self.COL_KOTA].astype(str).str.strip()

        def _index_col(col_text: str) -> dict[str, tuple[str, str]]:
            """
            Build: normalized_key → most-frequent (kota, prov).
            Deduplicates keys that map to more than one kota.
            """
            sub = (
                valid[[col_text, self.COL_KOTA, self.COL_PROV]]
                .dropna(subset=[col_text])
                .copy()
            )
            sub = sub[sub[col_text].astype(str).str.strip() != ""]
            sub["_key"] = sub[col_text].apply(normalize_text)

            freq = (
                sub.groupby(["_key", self.COL_KOTA, self.COL_PROV])
                .size()
                .reset_index(name="_count")
            )
            best = freq.loc[freq.groupby("_key")["_count"].idxmax()]
            return {
                row["_key"]: (row[self.COL_KOTA], row[self.COL_PROV])
                for _, row in best.iterrows()
            }

        self._lookup_tujuan = _index_col(self.COL_TUJUAN)
        self._lookup_alamat = _index_col(self.COL_ALAMAT)
        self._tujuan_keys   = list(self._lookup_tujuan.keys())
        self._alamat_keys   = list(self._lookup_alamat.keys())

       
    # ── Private: matchers ─────────────────────────────────────────────────

    def _match_db(
        self,
        teks,
        lookup: dict[str, tuple[str, str]],
        keys:   list[str],
        threshold: int,
    ) -> tuple[str, str] | None:
        """
        Fuzzy-match teks against confirmed DB reference keys using WRatio.

        WRatio is used (not partial_ratio) because here we are matching the
        full facility-name string against known facility names, not extracting
        a geographic token from inside a longer text.

        Handles DB entries with leading numeric codes (e.g. "00004 apotek araya
        malang") — WRatio's token_set_ratio component ignores extra tokens,
        giving a consistent 95-point score even when the test value lacks the
        code prefix.
        """
        if not _HAS_RAPIDFUZZ:
            return None
        if pd.isna(teks) or not str(teks).strip() or not keys:
            return None
        result = process.extractOne(
            normalize_text(str(teks)),
            keys,
            scorer=fuzz.WRatio,
            score_cutoff=threshold,
        )
        return lookup[result[0]] if result else None

    def _match_kota(self, teks, threshold: int) -> str | None:
        """
        Find which kota name from master_wilayah is best found INSIDE teks.

        Uses partial_ratio (substring-style match) — appropriate when the kota
        name is embedded within a longer facility-name or address string.
        """
        if not _HAS_RAPIDFUZZ:
            return None
        if pd.isna(teks) or not str(teks).strip():
            return None
        result = process.extractOne(
            normalize_text(str(teks)),
            self.kota_list,
            scorer=fuzz.partial_ratio,
            score_cutoff=threshold,
        )
        return result[0] if result else None

    # ── Private: row-level fill methods ───────────────────────────────────

    def _fill_row_db(self, row: pd.Series) -> pd.Series:
        """
        Pass 1 — DB lookup only.

        Priority:
          1. tujuan_penyaluran → DB WRatio match
          2. alamat_tujuan     → DB WRatio match (fallback)
        """
        kota_val    = row.get(self.COL_KOTA)
        prov_val    = row.get(self.COL_PROV)
        kota_filled = pd.notna(kota_val) and str(kota_val).strip()
        prov_filled = pd.notna(prov_val) and str(prov_val).strip()

        # Kota already set → only back-fill missing prov
        if kota_filled:
            if not prov_filled:
                row[self.COL_PROV] = self.kab_to_prov.get(
                    normalize_text(str(kota_val)), ""
                )
            return row

        result = self._match_db(
            row.get(self.COL_TUJUAN),
            self._lookup_tujuan,
            self._tujuan_keys,
            self.THRESHOLD_DB_TUJUAN,
        )
        if not result:
            result = self._match_db(
                row.get(self.COL_ALAMAT),
                self._lookup_alamat,
                self._alamat_keys,
                self.THRESHOLD_DB_ALAMAT,
            )
        if result:
            row[self.COL_KOTA], row[self.COL_PROV] = result

        return row

    def _fill_row_json(self, row: pd.Series) -> pd.Series:
        """
        Pass 2 — JSON geo-token extraction only (fallback for rows the DB
        could not resolve).

        Priority:
          1. tujuan_penyaluran → partial_ratio against kota list
          2. alamat_tujuan     → partial_ratio against kota list (fallback)
        """
        kota_val    = row.get(self.COL_KOTA)
        prov_val    = row.get(self.COL_PROV)
        kota_filled = pd.notna(kota_val) and str(kota_val).strip()
        prov_filled = pd.notna(prov_val) and str(prov_val).strip()

        # Kota already set (including those filled in Pass 1) → only back-fill prov
        if kota_filled:
            if not prov_filled:
                row[self.COL_PROV] = self.kab_to_prov.get(
                    normalize_text(str(kota_val)), ""
                )
            return row

        kota = self._match_kota(row.get(self.COL_TUJUAN), self.THRESHOLD_TUJUAN)
        if not kota:
            kota = self._match_kota(row.get(self.COL_ALAMAT), self.THRESHOLD_ALAMAT)
        if kota:
            row[self.COL_KOTA] = kota
            row[self.COL_PROV] = self.kab_to_prov.get(kota, "")

        return row

    # ── Public ─────────────────────────────────────────────────────────────

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Fill nama_kota_kab_tujuan and nama_provinsi_tujuan in two passes:

        Pass 1 — DB reference lookup (WRatio, primary)
            Searches 23,725 confirmed facility records. Faster resolution
            and higher confidence when a facility name is in the DB.

        Pass 2 — JSON geo-token extraction (partial_ratio, fallback)
            Looks for a master_wilayah kota name embedded inside the
            tujuan/alamat text. Catches rows the DB did not resolve.

        The summary log shows exactly how many rows each source filled.
        """
        # Ensure required columns exist — add empty ones if absent
        # rather than crashing, so uploads without alamat_tujuan still work.
        for col in [self.COL_TUJUAN, self.COL_ALAMAT]:
            if col not in df.columns:
                print(
                    f"[WARNING] Automatisasi_Fill: kolom '{col}' tidak ditemukan, "
                    "ditambahkan sebagai kosong."
                )
                df[col] = ""

        df = df.copy()

        for col in [self.COL_KOTA, self.COL_PROV]:
            if col not in df.columns:
                df[col] = pd.NA

        def _is_empty(series: pd.Series) -> pd.Series:
            return series.isna() | (
                series.astype(str).str.strip().isin(["", "nan", "None"])
            )

        n_total  = len(df)
        n_before = int(_is_empty(df[self.COL_KOTA]).sum())

        # ── Pass 1: DB lookup ──────────────────────────────────────────────
        df = df.apply(self._fill_row_db, axis=1)
        n_after_db = int(_is_empty(df[self.COL_KOTA]).sum())
        n_by_db    = n_before - n_after_db

        # ── Pass 2: JSON geo-token extraction (fallback) ──────────────────
        df = df.apply(self._fill_row_json, axis=1)
        n_after_json = int(_is_empty(df[self.COL_KOTA]).sum())
        n_by_json    = n_after_db - n_after_json

        n_k = int((~_is_empty(df[self.COL_KOTA])).sum())
        n_p = int((~_is_empty(df[self.COL_PROV])).sum())

        print(f"\n[INFO] Pengisian lokasi otomatis selesai:")
        print(f"  Terisi via DB referensi  : {n_by_db}")
        print(f"  Terisi via JSON wilayah  : {n_by_json}")
        print(f"  Kota/Kab terisi otomatis total    : {n_k}/{n_total}")
        print(f"  Provinsi terisi otomatis total    : {n_p}/{n_total}")
        
        return df

# ─────────────────────────────────────────────────────────────
# CLASS 3 — LABELING KATEGORI OBAT
# ─────────────────────────────────────────────────────────────
class Labeling_Kategori_Obat:
    """
    Stamps every row's 'kategori_obat' with the exact string the user
    typed into the upload form (max 100 chars, enforced by the route).

    No keyword matching, no built-in rules, no dictionaries.
    Whatever the user types becomes the label for every row in that upload.

    Examples
    --------
    User types "Merophenem"     → every row: kategori_obat = "Merophenem"
    User types "Antibiotik OKT" → every row: kategori_obat = "Antibiotik OKT"
    User types ""               → every row: kategori_obat = "general"
    """

    COL_OUTPUT = "kategori_obat"

    def __init__(self, kategori_label: str = ""):
        # Normalise to lowercase so frontend filters work regardless of how
        # the user capitalised the input (e.g. "Merophenem" → "merophenem").
        # Truncation / required-field validation is done upstream in the route.
        self.kategori_label: str = (
            str(kategori_label).strip().lower() if kategori_label else ""
        )

    def run(
        self,
        df: pd.DataFrame,
        verbose: bool = True,
    ) -> tuple[pd.DataFrame, dict[str, int]]:
        """
        Stamp every row.  Falls back to "general" if label is empty.

        Returns
        -------
        (df, distribution)
            distribution is always a single-entry dict: {label: len(df)}
        """
        df    = df.copy()
        label = self.kategori_label or "general"

        df[self.COL_OUTPUT] = label
        distribution        = {label: len(df)}

        if verbose:
            print(f"  Label digunakan : '{label}'")
            print(f"  Total baris     : {len(df):,}")

        return df, distribution


# ─────────────────────────────────────────────────────────────
# CLASS 4 — ANALISIS KETEPATAN ALAMAT
# ─────────────────────────────────────────────────────────────
class Analisis_Ketepatan_Alamat:
    """
    Uses master_wilayah.json to correct 'nama_kota_kab_tujuan' and
    'nama_provinsi_tujuan' by parsing 'alamat_tujuan'.

    NEW in this version
    -------------------
    clean_wilayah_output() — final-pass cleaner applied to EVERY row after
    corrections are written back.  It:
      • Removes all symbols/punctuation (dots, commas, slashes, brackets…)
      • Strips KAB. / KABUPATEN / KOTA / PROVINSI / PROV. prefixes
        and normalises the bare name to match the JSON dictionary key format
      • If a cleaned value matches a dict key/value exactly it is replaced
        with the canonical form (preserves consistent UPPERCASE JSON keys)
    """

    COL_ALAMAT = "alamat_tujuan"
    COL_KOTA   = "nama_kota_kab_tujuan"
    COL_PROV   = "nama_provinsi_tujuan"

    _KEYWORD_PATTERN = re.compile(
        r'\b(?:KAB\.?|KOTA|PROV(?:INSI)?\.?)\b', re.IGNORECASE
    )

    def __init__(self, path_json: str = None):
        self._path_json   = path_json or _DEFAULT_JSON
        self._json_loaded = False

        self.kab_to_prov:   dict[str, str] = {}
        self.wilayah_full:  set[str]       = set()
        self.wilayah_base:  set[str]       = set()
        self.wilayah_token: set[str]       = set()
        self.pattern:       re.Pattern     = None

        self._load_kamus_wilayah()
        self._build_pattern()

    def _load_kamus_wilayah(self):
        try:
            with open(self._path_json, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Store UPPERCASE keys/values (JSON uses Title Case)
            self.kab_to_prov = {
                k.upper().strip(): v.upper().strip() for k, v in raw.items()
            }
            kab_set  = set(self.kab_to_prov.keys())
            prov_set = set(self.kab_to_prov.values())
            for nama in kab_set:
                self.wilayah_base.add(
                    nama[5:] if nama.startswith("KOTA ") else nama
                )
            self.wilayah_full = self.wilayah_base | prov_set
            for phrase in self.wilayah_full:
                self.wilayah_token.update(phrase.split())
            self._json_loaded = True
        except FileNotFoundError:
            print(
                "⚠  master_wilayah.json tidak ditemukan.\n"
                "   Letakkan file di: backend/data/master_wilayah.json\n"
                "   Koreksi alamat dilewati."
            )

    def _build_pattern(self):
        if not self.wilayah_full:
            return
        sorted_phrases = sorted(self.wilayah_full, key=len, reverse=True)
        escaped        = [re.escape(p) for p in sorted_phrases]
        prefix         = r'(KABUPATEN\s+|KAB\.?\s+|KOTA\s+|PROVINSI\s+|PROV\.?\s+)'
        self.pattern   = re.compile(
            r'\b' + prefix + r'(' + '|'.join(escaped) + r')\b', re.IGNORECASE,
        )

    def _extract_matches(self, text: str) -> list[tuple[str, str]]:
        text    = str(text).upper()
        matches = [
            (m.group(1).strip().rstrip('.'), m.group(2).strip(), m.start())
            for m in self.pattern.finditer(text)
        ]
        return [(p, n) for p, n, _ in sorted(matches, key=lambda x: x[2], reverse=True)]

    def _resolve_kota_prov(self, matches: list[tuple[str, str]]) -> tuple[str, str]:
        kota_candidates, prov_candidates = [], []
        for prefix, nama in matches:
            prefix_up = prefix.upper().strip()
            if 'PROV' in prefix_up:
                if nama in set(self.kab_to_prov.values()):
                    prov_candidates.append(nama)
            elif 'KOTA' in prefix_up:
                kota_full = f"KOTA {nama}"
                if kota_full in self.kab_to_prov:
                    kota_candidates.append(kota_full)
                elif nama in self.kab_to_prov:
                    kota_candidates.append(nama)
            elif 'KAB' in prefix_up:
                if nama in self.kab_to_prov:
                    kota_candidates.append(nama)
            else:
                if nama in self.kab_to_prov:
                    kota_candidates.append(nama)

        best_kota = ""
        best_prov = ""
        if kota_candidates:
            best_kota = max(kota_candidates, key=len)
            best_prov = self.kab_to_prov[best_kota]
        elif prov_candidates:
            best_prov = max(prov_candidates, key=len)
        return best_kota, best_prov

    # ── Final-pass wilayah cleaners ──────────────────────────

    def _normalize_kota_kab(self, text: str) -> str:
        """
        Normalize a single kota/kab string to match the JSON dict key format.

        Steps:
        1. Remove all symbols — keep only letters, numbers, spaces
        2. Strip KAB. / KABUPATEN prefix
        3. Try to match against kab_to_prov:
           - Exact match → return as-is
           - With KOTA prefix → return with KOTA
           - Without KABUPATEN prefix → try both forms
        4. Fall back to cleaned text (unmatched)
        """
        if not text or str(text).strip().lower() in ("", "nan", "none", "null"):
            return ""

        # Remove symbols, normalise spaces
        cleaned = re.sub(r'[^a-zA-Z0-9\s]', ' ', str(text).upper())
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        if not cleaned:
            return ""

        # Exact match
        if cleaned in self.kab_to_prov:
            return cleaned

        # Strip KAB / KABUPATEN prefix
        stripped = cleaned
        for pat in [r'^KABUPATEN\s+', r'^KAB\s+']:
            m = re.match(pat, cleaned)
            if m:
                stripped = cleaned[m.end():].strip()
                break

        if stripped != cleaned:
            if stripped in self.kab_to_prov:
                return stripped
            kota_form = f"KOTA {stripped}"
            if kota_form in self.kab_to_prov:
                return kota_form

        # Try KOTA prefix on original
        kota_cleaned = f"KOTA {cleaned}"
        if kota_cleaned in self.kab_to_prov:
            return kota_cleaned

        # Return cleaned but unmatched
        return cleaned

    def _normalize_prov(self, text: str) -> str:
        """
        Normalize a single provinsi string to match the JSON dict value format.

        Steps:
        1. Remove all symbols
        2. Strip PROVINSI / PROV prefix
        3. Match against known province set
        4. Fall back to cleaned text
        """
        if not text or str(text).strip().lower() in ("", "nan", "none", "null"):
            return ""

        cleaned = re.sub(r'[^a-zA-Z0-9\s]', ' ', str(text).upper())
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        if not cleaned:
            return ""

        prov_set = set(self.kab_to_prov.values())

        if cleaned in prov_set:
            return cleaned

        # Strip PROVINSI / PROV prefix
        for pat in [r'^PROVINSI\s+', r'^PROV\s+']:
            m = re.match(pat, cleaned)
            if m:
                stripped = cleaned[m.end():].strip()
                if stripped in prov_set:
                    return stripped
                break

        return cleaned

    def clean_wilayah_output(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Final-pass cleaning applied to EVERY row.

        Called at the end of update_original() so both corrected and
        untouched rows pass through the same normalisation.

        What it does per cell:
        • Removes dots, commas, slashes, parentheses, and all other
          non-alphanumeric/space characters
        • Strips regional prefixes: KAB. KABUPATEN KOTA PROVINSI PROV.
          — but only if what remains matches the JSON dictionary
          (so 'KOTA SURABAYA' stays 'KOTA SURABAYA', not 'SURABAYA')
        • Trims and collapses whitespace
        • If the normalised value matches a JSON key/value exactly,
          uses that canonical form (ensures UPPERCASE consistency)
        """
        df = df.copy()

        if self.COL_KOTA in df.columns:
            before_kota = df[self.COL_KOTA].copy()
            df[self.COL_KOTA] = df[self.COL_KOTA].apply(self._normalize_kota_kab)
            n_changed_kota = (df[self.COL_KOTA] != before_kota).sum()
        else:
            n_changed_kota = 0

        if self.COL_PROV in df.columns:
            before_prov = df[self.COL_PROV].copy()
            df[self.COL_PROV] = df[self.COL_PROV].apply(self._normalize_prov)
            n_changed_prov = (df[self.COL_PROV] != before_prov).sum()
        else:
            n_changed_prov = 0

        # Verification stats
        n_kota_matched = (
            df[self.COL_KOTA].isin(self.kab_to_prov).sum()
            if self.COL_KOTA in df.columns else 0
        )
        n_prov_matched = (
            df[self.COL_PROV].isin(set(self.kab_to_prov.values())).sum()
            if self.COL_PROV in df.columns else 0
        )
        total = len(df)

        print(f"  [Pembersihan Akhir Wilayah]")
        print(f"  Kota/Kab diubah          : {n_changed_kota:,}")
        print(f"  Kota/Kab cocok kamus     : {n_kota_matched:,} / {total:,}")
        print(f"  Provinsi diubah          : {n_changed_prov:,}")
        print(f"  Provinsi cocok kamus     : {n_prov_matched:,} / {total:,}")

        return df

    # ── Existing methods (unchanged) ─────────────────────────

    def filter_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self._json_loaded:
            return df.assign(_matches=[[]] * len(df), _orig_idx=df.index)

        df = df.copy()
        df[self.COL_ALAMAT] = df[self.COL_ALAMAT].astype(str).str.upper()

        has_keyword = df[self.COL_ALAMAT].str.contains(
            self._KEYWORD_PATTERN, regex=True
        )
        df["_matches"] = (
            df[self.COL_ALAMAT]
            .where(has_keyword, other="")
            .apply(self._extract_matches)
        )

        before      = len(df)
        df_filtered = df[df["_matches"].map(len) > 0].copy()
        df_filtered["_orig_idx"] = df_filtered.index
        df_filtered = df_filtered.reset_index(drop=True)
        after = len(df_filtered)

        print(f"Baris total               : {before:,}")
        print(f"Baris kosong total        : {df[self.COL_KOTA].isna().sum():,}")
        print(f"Ada keyword KAB/KOTA/PROV : {has_keyword.sum():,}")
        print(f"Cocok kamus wilayah       : {after:,}")
        print(f"Baris di-skip             : {before - after:,}")

        return df_filtered

    def koreksi_kota_provinsi(self, df: pd.DataFrame) -> pd.DataFrame:
        if "_matches" not in df.columns:
            df = df.copy()
            df[self.COL_ALAMAT] = df[self.COL_ALAMAT].astype(str).str.upper()
            df["_matches"]      = df[self.COL_ALAMAT].apply(self._extract_matches)

        df = df.copy()
        df[self.COL_KOTA] = df[self.COL_KOTA].astype(str).str.upper().str.strip()
        df[self.COL_PROV] = df[self.COL_PROV].astype(str).str.upper().str.strip()

        resolved        = df["_matches"].apply(self._resolve_kota_prov)
        df["_kota_fix"] = resolved.apply(lambda x: x[0])
        df["_prov_fix"] = resolved.apply(lambda x: x[1])

        kota_salah = (df["_kota_fix"] != "") & (df[self.COL_KOTA] != df["_kota_fix"])
        prov_salah = (df["_prov_fix"] != "") & (df[self.COL_PROV] != df["_prov_fix"])
        df["_perlu_koreksi"] = kota_salah | prov_salah

        df["Kota/Kab Terkoreksi"] = df[self.COL_KOTA].copy()
        df["Provinsi Terkoreksi"] = df[self.COL_PROV].copy()
        df.loc[kota_salah, "Kota/Kab Terkoreksi"] = df.loc[kota_salah, "_kota_fix"]
        df.loc[prov_salah, "Provinsi Terkoreksi"] = df.loc[prov_salah, "_prov_fix"]

        n_kota  = kota_salah.sum()
        n_prov  = prov_salah.sum()
        n_total = df["_perlu_koreksi"].sum()

        print(f"  RINGKASAN KOREKSI")
        print(f"  Total baris dikoreksi : {n_total:,}")
        print(f"  Koreksi Kota/Kab      : {n_kota:,}")
        print(f"  Koreksi Provinsi      : {n_prov:,}")

        df.drop(
            columns=["_matches", "_kota_fix", "_prov_fix", "_perlu_koreksi"],
            inplace=True,
        )
        return df

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        df_filtered  = self.filter_rows(df)
        df_corrected = self.koreksi_kota_provinsi(df_filtered)
        return df_corrected

    def update_original(
        self, df_nama: pd.DataFrame, df_hasil: pd.DataFrame
    ) -> pd.DataFrame:
        df_nama = df_nama.copy()
        if "_orig_idx" not in df_hasil.columns:
            raise KeyError(
                "_orig_idx tidak ditemukan — pastikan filter_rows() dijalankan dulu."
            )

        orig_idx = df_hasil["_orig_idx"].values
        df_nama.loc[orig_idx, self.COL_KOTA] = df_hasil["Kota/Kab Terkoreksi"].values
        df_nama.loc[orig_idx, self.COL_PROV] = df_hasil["Provinsi Terkoreksi"].values

        n_updated = len(orig_idx)
        n_skip    = len(df_nama) - n_updated
        nan_idx   = (
            df_nama[self.COL_KOTA]
            .astype(str).str.strip()
            .replace(["", "nan", "None"], pd.NA)
            .isna().sum()
        )

        print(f"  OUTPUT DATA DETAIL")
        print(f"  Baris di-update                      : {n_updated:,}")
        print(f"  Baris tidak diubah (di-skip filter)  : {n_skip:,}")
        print(f"  Baris kota/kab kosong setelah update : {nan_idx:,}")

        # ── FINAL PASS: clean symbols + normalise ALL rows ────
        print()
        df_nama = self.clean_wilayah_output(df_nama)

        return df_nama


# ─────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────
def run_pipeline(
    df: pd.DataFrame,
    path_json: str = None,
    kategori_obat: str = "",
    db_records: "pd.DataFrame | None" = None,
) -> tuple[pd.DataFrame, str]:
    """
    Full cleaning + labeling pipeline.

    Steps (matches notebook prototype)
    ────────────────────────────────────
    1. Normalise column names
    2. Perbaikan_Nama_Sarana              → clean tujuan_penyaluran strings
    3. Automatisasi_Fill_Tujuan_Lokasi    → auto-fill kota/kab from DB+JSON match
    4. Analisis_Ketepatan_Alamat          → correct kota/kab from alamat_tujuan
    5. Labeling_Nama_Sarana               → jenis_sarana (on cleaned names)
    6. Labeling_Kategori_Obat             → kategori_obat (user input stamp)

    Parameters
    ----------
    df            : raw DataFrame
    path_json     : path to master_wilayah.json
    kategori_obat : label typed by user on the upload form (max 100 chars)
    db_records    : DataFrame from DataMatchModelAlamat.build_lookup_df()
                    (PostgreSQL lookup for Automatisasi step)
    """
    buf = io.StringIO()

    with contextlib.redirect_stdout(buf):

        # ── STEP 1: Normalise column names ─────────────────────
        print("=" * 55)
        print("  STEP 1 — NORMALISASI NAMA KOLOM")
        print("=" * 55)
        df.columns = [clean_header(col) for col in df.columns]
        print(f"  Kolom: {list(df.columns)[:6]} ...\n")

        # ── STEP 2: Clean facility names ────────────────────────
        print("=" * 55)
        print("  STEP 2 — PERBAIKAN NAMA SARANA")
        print("=" * 55)
        sarana = Perbaikan_Nama_Sarana(path_json)
        df     = sarana.run(df)
        print(f"  Kolom '{sarana.COL_SARANA}' selesai diproses.\n")

        # ── STEP 3: Auto-fill kota/kab from DB + JSON match ────
        print("=" * 55)
        print("  STEP 3 — AUTOMATISASI FILL TUJUAN LOKASI SARANA")
        print("=" * 55)
        predict = Automatisasi_Fill_Tujuan_Lokasi_Sarana(
            path_json=path_json,
            db_records=db_records,
        )
        df = predict.run(df)
        print()

        # ── STEP 4: Correct kota/kab using full address text ───
        print("=" * 55)
        print("  STEP 4 — ANALISIS KETEPATAN ALAMAT")
        print("=" * 55)
        alamat   = Analisis_Ketepatan_Alamat(path_json)
        df_hasil = alamat.run(df)
        df       = alamat.update_original(df, df_hasil)
        # Drop helper columns created by filter_rows()
        df = df.drop(columns=["_matches", "_orig_idx"], errors="ignore")
        print()

        # ── STEP 5: Label jenis_sarana (on cleaned names) ──────
        print("=" * 55)
        print("  STEP 5 — LABELING JENIS SARANA")
        print("=" * 55)
        sarana_labeler = Labeling_Nama_Sarana()
        df, _ = sarana_labeler.run(df, verbose=True)
        print(f"  Kolom '{Labeling_Nama_Sarana.COL_OUTPUT}' selesai dilabeli.\n")

        # ── STEP 6: Stamp kategori_obat from user input ────────
        print("=" * 55)
        print("  STEP 6 — LABELING KATEGORI OBAT")
        print("=" * 55)
        print(f"  Input label : '{(kategori_obat or 'general').lower()}'")
        obat_labeler = Labeling_Kategori_Obat(kategori_label=kategori_obat)
        df, _ = obat_labeler.run(df, verbose=True)
        print()

        print("=" * 55)
        print("  PIPELINE SELESAI ✓")
        print(f"  Total baris output : {len(df):,}")
        print("=" * 55)

    return df, buf.getvalue()


# ─────────────────────────────────────────────────────────────
# CLI  —  python processing_pipeline.py
# ─────────────────────────────────────────────────────────────
def _print_bar_chart(dist: dict[str, int], title: str) -> None:
    if not dist:
        return
    print(f"\n  {title}")
    print(f"  {'─' * 52}")
    max_val = max(dist.values()) if dist else 1
    total   = sum(dist.values())
    for label, cnt in sorted(dist.items(), key=lambda x: -x[1]):
        bar = "█" * min(int(cnt / max_val * 25), 25)
        pct = cnt / total * 100
        print(f"    {label:<30} {cnt:>7,}  ({pct:4.1f}%)  {bar}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="BPOM Drug Distribution — Processing Pipeline CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Contoh penggunaan:
  python processing_pipeline_main.py data.csv
  python processing_pipeline_main.py data.csv -k "Antibiotik OKT"
  python processing_pipeline_main.py data.csv -o cleaned.csv -k "Merophenem"
  python processing_pipeline_main.py data.csv -j path/to/wilayah.json

Catatan:
  -k / --kategori  : label kategori obat yang akan di-stamp ke SEMUA baris.
                     Jika tidak diisi, akan ditanya secara interaktif.
        """,
    )
    parser.add_argument("csv",  nargs="?", help="Path ke file CSV input")
    parser.add_argument("-o", "--output",   help="Path output CSV (default: <input>_cleaned.csv)")
    parser.add_argument("-k", "--kategori", help="Label kategori obat untuk semua baris (max 100 karakter)")
    parser.add_argument("-j", "--json",     help="Path ke master_wilayah.json")
    parser.add_argument("--no-interactive", action="store_true",
                        help="Non-interaktif: skip prompt jika --kategori tidak disupply")

    args = parser.parse_args()
    SEP  = "=" * 62

    print(SEP)
    print("  BPOM DRUG DISTRIBUTION — PROCESSING PIPELINE CLI")
    print(SEP)

    # ── Input CSV ─────────────────────────────────────────────
    csv_path: str = args.csv or ""
    if not csv_path:
        csv_path = input("\n  Masukkan path file CSV  : ").strip()

    if not os.path.exists(csv_path):
        print(f"\n  [ERROR] File tidak ditemukan: {csv_path}")
        sys.exit(1)

    print(f"\n  Memuat file: {csv_path}")
    try:
        df_input = pd.read_csv(csv_path, low_memory=False, encoding="utf-8-sig")
    except UnicodeDecodeError:
        df_input = pd.read_csv(csv_path, low_memory=False, encoding="latin-1")
    print(f"  Dimuat     : {len(df_input):,} baris × {len(df_input.columns)} kolom")

    # ── Kategori obat ─────────────────────────────────────────
    kategori_obat: str = (args.kategori or "").strip()[:100]

    if not kategori_obat and not args.no_interactive:
        print(
            "\n  Masukkan label kategori obat untuk semua baris."
            "\n  Contoh: Antibiotik, Merophenem, Narkotika, Suplemen"
            "\n  Tekan Enter untuk menggunakan default \'General\'."
            "\n"
        )
        try:
            raw = input("  Kategori obat > ").strip()[:100]
            if raw:
                kategori_obat = raw
        except EOFError:
            pass

    label_display = (kategori_obat or "general").lower()
    print(f"\n  Kategori obat  : \'{label_display}\'")

    # ── Run pipeline ─────────────────────────────────────────
    print(f"\n{SEP}")
    print("  MENJALANKAN PIPELINE ...")
    print(SEP)

    df_out, log = run_pipeline(
        df_input.copy(),
        path_json=args.json,
        kategori_obat=kategori_obat,
    )
    print(log)

    # ── Save output ───────────────────────────────────────────
    out_path: str = args.output or csv_path.replace(".csv", "_cleaned.csv")
    df_out.to_csv(out_path, index=False, encoding="utf-8-sig")

    print(f"\n{SEP}")
    print("  HASIL")
    print(SEP)
    print(f"  Input    : {len(df_input):,} baris")
    print(f"  Output   : {len(df_out):,} baris")
    print(f"  Disimpan : {out_path}")

    if "jenis_sarana" in df_out.columns:
        _print_bar_chart(df_out["jenis_sarana"].value_counts().to_dict(), "DISTRIBUSI JENIS SARANA")
    if "kategori_obat" in df_out.columns:
        _print_bar_chart(df_out["kategori_obat"].value_counts().to_dict(), "DISTRIBUSI KATEGORI OBAT")
    if "nama_kota_kab_tujuan" in df_out.columns:
        top10 = dict(df_out["nama_kota_kab_tujuan"].value_counts().head(10))
        _print_bar_chart(top10, "TOP 10 KOTA/KAB TUJUAN")

    print(f"\n{SEP}\n")