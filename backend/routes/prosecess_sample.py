import json
import re
import pandas as pd
from collections import Counter
from wordcloud import WordCloud
import matplotlib.pyplot as plt
import pandas as pd
import re

# ====================== COLUMN NAME PREPARATION ======================
def clean_header(col_name):
    cleaned = re.sub(r'[^a-zA-Z0-9]', '_', col_name)
    cleaned = re.sub(r'_+', '_', cleaned)
    cleaned = cleaned.strip('_')
    return cleaned.lower()

# ====================== CLEANING DAN PERBAIKAN NAMA SARANA ======================
class Perbaikan_Nama_Sarana:
# CONFIG SARANA
    COL_SARANA = "tujuan_penyaluran"
    PATH_JSON  = "/content/drive/MyDrive/Magang VIF & BPOM/Dataset Mini Project BPOM/master_wilayah.json"

    RS_PREFIXES = ['RSAB', 'RSUP', 'RSIA', 'RSUD', 'RSB', 'RSK', 'RSI', 'RSD', 'RSU', 'RS']
    BADAN_USAHA = ['PT', 'CV', 'PBF']
    CITY_CODES  = r'\b(SBY|MLG|SDO|JBR|MJK|KDR|JMB|BJN|PSR|BWI|TLG|BGL|PBL|LWG|MJKT|BNYW)\b'

    def __init__(self):
        self.kab_to_prov:   dict[str, str] = {}
        self.wilayah_full:  set[str]       = set()
        self.wilayah_base:  set[str]       = set()
        self.wilayah_token: set[str]       = set()
        self.pattern:       re.Pattern     = None

        self._load_kamus_wilayah()
        self._build_pattern()
    def _load_kamus_wilayah(self):
        try:
            with open(self.PATH_JSON, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.kab_to_prov = {
                k.upper().strip(): v.upper().strip()
                for k, v in raw.items()
            }

            for kab in self.kab_to_prov:
                self.wilayah_full.add(kab)
                # strip leading "KOTA " so "KOTA BLITAR" → base "BLITAR"
                base = re.sub(r'^KOTA\s+', '', kab).strip()
                self.wilayah_base.add(base)
                for token in base.split():
                    self.wilayah_token.add(token)

        except FileNotFoundError:
            pass   # path unavailable in this environment; sets remain empty

    def _build_pattern(self):
        if not self.wilayah_full:
            return
        sorted_phrases = sorted(self.wilayah_full, key=len, reverse=True)
        escaped        = [re.escape(p) for p in sorted_phrases]
        prefix         = r'(KABUPATEN\s+|KAB\.?\s+|KOTA\s+|PROVINSI\s+|PROV\.?\s+)'
        self.pattern   = re.compile(
            r'\b' + prefix + r'(' + '|'.join(escaped) + r')\b',
            re.IGNORECASE,
        )
    def clean_text(self, text):
        """Normalise a single string: strip non-alphanumeric, collapse
        whitespace, uppercase."""
        if pd.isna(text):
            return None
        text = str(text)
        text = re.sub(r'[^a-zA-Z0-9\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text.upper()

    def regex_clean(self, df: pd.DataFrame, col: str = None) -> pd.DataFrame:
      # NORMALISASI NAMA SARANA
        col = col or self.COL_SARANA
        df  = df.copy()
        df[col] = df[col].str.upper()
        df[col] = df[col].str.replace(r'^(kabupaten|kab)\s*\.?\s*', '', case=False, regex=True)
        df[col] = df[col].str.replace(r'^[0-9]+\s*', '', regex=True)
        df[col] = df[col].str.replace(r'\b[0-9]{5,}\b\s*', '', regex=True)
        df[col] = df[col].str.replace(r'^(?:[A-Z]+[0-9]+[A-Z0-9]*|[0-9]+[A-Z]+[A-Z0-9]*)\s*', '', regex=True)
        df[col] = df[col].str.replace(r'\bK[-.\s]*24\b', 'K24', regex=True)
        df[col] = df[col].str.replace(r'\bK\s*24\b',     'K24', regex=True)
        df[col] = df[col].str.replace(r'\bKF\b',     'KIMIA FARMA', regex=True)
        df[col] = df[col].str.replace(r'\bAPOTIK\b', 'APOTEK',      regex=True)
        df[col] = df[col].str.replace(r'\bAPT\b',    'APOTEK',      regex=True)
        df[col] = df[col].str.replace(r'\bAP\b',     'APOTEK',      regex=True)
        df[col] = df[col].str.replace(self.CITY_CODES, '', regex=True)
        df[col] = df[col].str.replace(r'\s+', ' ', regex=True).str.strip()
        return df

    def perbaikan_nama_apotek(self, df: pd.DataFrame, col: str = None) -> pd.DataFrame:
    # NAMA SARANA APOTEK AWAL KALIMAT
        col = col or self.COL_SARANA
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
        # NAMA SARANA RS AWAL KALIMAT
        col = col or self.COL_SARANA

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
         # NAMA SARANA PT/CV/PBF AWAL KALIMAT
        col = col or self.COL_SARANA

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
        # REMOVE DUPLICATE WORDS
        col = col or self.COL_SARANA

        def dedup(text):
            if pd.isna(text):
                return text
            words = str(text).split()
            return ' '.join(dict.fromkeys(words))

        df = df.copy()
        df[col] = df[col].apply(dedup)
        return df

    # PIPELINE CLASS PERBAIKAN NAMA SARANA
    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        col = self.COL_SARANA
        df  = self.regex_clean(df, col)
        df  = self.perbaikan_nama_apotek(df, col)
        df  = self.perbaikan_nama_rs(df, col)
        df  = self.hapus_kata_berulang(df, col)
        df  = self.perbaikan_nama_pt(df, col)
        df[col] = df[col].apply(self.clean_text)
        return df

# ====================== PERBAIKAN KETEPATAN ALAMAT LEGNGKAP ======================
class Analisis_Ketepatan_Alamat:
# CONFIG ALAMAT
    COL_ALAMAT = "alamat_tujuan"
    COL_KOTA   = "nama_kota_kab_tujuan"
    COL_PROV   = "nama_provinsi_tujuan"
    PATH_JSON  = "/content/drive/MyDrive/Magang VIF & BPOM/Dataset Mini Project BPOM/master_wilayah.json"
    _KEYWORD_PATTERN = re.compile(r'\b(?:KAB\.?|KOTA|PROV(?:INSI)?\.?)\b', re.IGNORECASE)
    def __init__(self, path_json: str = None):
        if path_json:
            self.PATH_JSON = path_json

        self.kab_to_prov:   dict[str, str] = {}
        self.wilayah_full:  set[str]       = set()
        self.wilayah_base:  set[str]       = set()
        self.wilayah_token: set[str]       = set()
        self.pattern:       re.Pattern     = None

        self._load_kamus_wilayah()
        self._build_pattern()

    # LOAD DICTIONARY JSON
    def _load_kamus_wilayah(self):
        with open(self.PATH_JSON, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # flat dict — iterate items directly
        self.kab_to_prov = {
            k.upper().strip(): v.upper().strip()
            for k, v in raw.items()}
        kab_set  = set(self.kab_to_prov.keys())
        prov_set = set(self.kab_to_prov.values())
        # wilayah_base: strip leading "KOTA " so the regex group(2) can be a
        # bare name and we still resolve it via "KOTA X" lookup
        self.wilayah_base = set()
        for nama in kab_set:
            if nama.startswith("KOTA "):
                self.wilayah_base.add(nama[5:])
            else:
                self.wilayah_base.add(nama)
        self.wilayah_full = self.wilayah_base | prov_set
        for phrase in self.wilayah_full:
            self.wilayah_token.update(phrase.split())

    # BUILD REGEX
    def _build_pattern(self):
        """Two capture groups: (1) prefix  (2) wilayah name.
        Sorted longest-first for greedy left-to-right matching."""
        sorted_phrases = sorted(self.wilayah_full, key=len, reverse=True)
        escaped        = [re.escape(p) for p in sorted_phrases]
        prefix         = r'(KABUPATEN\s+|KAB\.?\s+|KOTA\s+|PROVINSI\s+|PROV\.?\s+)'
        self.pattern   = re.compile(r'\b' + prefix + r'(' + '|'.join(escaped) + r')\b',re.IGNORECASE,
        )

    # EXTRACT RIGHT TO LEFT PRIORITY
    def _extract_matches(self, text: str) -> list[tuple[str, str]]:
        """Return [(prefix, nama), ...] ordered right-to-left (rightmost = most specific / closest to the end of the address string)."""
        text = str(text).upper()
        matches = [
            (m.group(1).strip().rstrip('.'), m.group(2).strip(), m.start())
            for m in self.pattern.finditer(text)
        ]
        # sort descending by position → rightmost first
        return [(p, n) for p, n, _ in sorted(matches, key=lambda x: x[2], reverse=True)]

    # RESOLVE KOTA OR KAB AND PROVINCE MATCHING
    def _resolve_kota_prov(self, matches: list[tuple[str, str]]) -> tuple[str, str]:
        kota_candidates = []
        prov_candidates = []
        for prefix, nama in matches:
            prefix_up = prefix.upper().strip()
    
            # PROVINSI
            if 'PROV' in prefix_up:
                if nama in set(self.kab_to_prov.values()):
                    prov_candidates.append(nama)
            # KOTA
            elif 'KOTA' in prefix_up:
                kota_full = f"KOTA {nama}"
                if kota_full in self.kab_to_prov:
                    kota_candidates.append(kota_full)
                elif nama in self.kab_to_prov:
                    kota_candidates.append(nama)
    
            # KABUPATEN / KAB
            elif 'KAB' in prefix_up:
                # NOT convert into "KOTA X"
                if nama in self.kab_to_prov:
                    kota_candidates.append(nama)
    
            # FALLBACK THE REST
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
    # FILTER
    def filter_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        # df = df.copy()
        df[self.COL_ALAMAT] = df[self.COL_ALAMAT].astype(str).str.upper()

        # QUICK KEYWORD CHECK
        has_keyword = df[self.COL_ALAMAT].str.contains(
            self._KEYWORD_PATTERN, regex=True
        )

        # FULL DICTIONARY MATCH (only on keyword-positive rows)
        df["_matches"] = (df[self.COL_ALAMAT].where(has_keyword, other="").apply(self._extract_matches))
        before      = len(df)
        df_filtered = df[df["_matches"].map(len) > 0].copy()
        df_filtered["_orig_idx"] = df_filtered.index
        df_filtered = df_filtered.reset_index(drop=True)
        after = len(df_filtered)
        print(f"Baris total               : {before:,}")
        print(f"Baris kosong total               : {df[self.COL_KOTA].isna().sum():,}")
        print(f"Ada keyword KAB/KOTA/PROV : {has_keyword.sum():,}")
        print(f"Cocok kamus wilayah       : {after:,}")
        print(f"Baris di-skip             : {before - after:,}")

        return df_filtered

    # KOREKSI
    def koreksi_kota_provinsi(self, df: pd.DataFrame) -> pd.DataFrame:
        # df = df.copy()

        if "_matches" not in df.columns:
            df[self.COL_ALAMAT] = df[self.COL_ALAMAT].astype(str).str.upper()
            df["_matches"]      = df[self.COL_ALAMAT].apply(self._extract_matches)

        df[self.COL_KOTA] = df[self.COL_KOTA].astype(str).str.upper().str.strip()
        df[self.COL_PROV] = df[self.COL_PROV].astype(str).str.upper().str.strip()

        resolved        = df["_matches"].apply(self._resolve_kota_prov)
        df["_kota_fix"] = resolved.apply(lambda x: x[0])
        df["_prov_fix"] = resolved.apply(lambda x: x[1])

        kota_salah = (df["_kota_fix"] != "") & (df[self.COL_KOTA] != df["_kota_fix"])
        prov_salah = (df["_prov_fix"] != "") & (df[self.COL_PROV] != df["_prov_fix"])
        df["_perlu_koreksi"] = kota_salah | prov_salah

        # output columns
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

        if n_total > 0:
            detail = df[df["_perlu_koreksi"]][[
                self.COL_ALAMAT,
                self.COL_KOTA,          "Kota/Kab Terkoreksi",
                self.COL_PROV,          "Provinsi Terkoreksi",
            ]].rename(columns={
                self.COL_ALAMAT : "Alamat Tujuan",
                self.COL_KOTA   : "Kota/Kab (Sebelum)",
                self.COL_PROV   : "Provinsi (Sebelum)",
            })
            # print(detail.to_string(index=True))

        df.drop(
            columns=["_matches", "_kota_fix", "_prov_fix", "_perlu_koreksi"],
            inplace=True,
        )
        return df


    # # PIPELINE CLASS ANALISIS KETEPATAN ALAMAT
    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        df_filtered = self.filter_rows(df)
        df_corrected = self.koreksi_kota_provinsi(df_filtered)

        return df_corrected

    # TULIS DATA KOREKSI BALIK KE DATAFRAME
    def update_original(self, df_nama: pd.DataFrame, df_hasil: pd.DataFrame) -> pd.DataFrame:
        """Write corrected kota/prov values back into the original full DataFrame
        using the _orig_idx saved by filter_rows()."""
        df_nama = df_nama.copy()

        if "_orig_idx" not in df_hasil.columns:
            raise KeyError("_orig_idx tidak ditemukan — pastikan filter_rows() dijalankan dulu.")
        orig_idx = df_hasil["_orig_idx"].values
        df_nama.loc[orig_idx, self.COL_KOTA] = df_hasil["Kota/Kab Terkoreksi"].values
        df_nama.loc[orig_idx, self.COL_PROV] = df_hasil["Provinsi Terkoreksi"].values
        n_updated = len(orig_idx)
        n_skip    = len(df_nama) - n_updated
        nan_idx = (df_nama[self.COL_KOTA].astype(str).str.strip().replace(["", "nan", "None"], pd.NA).isna().sum())

        print(f"  OUTPUT DATA DETAIL")
        print(f"  Baris di-update                     : {n_updated:,}")
        print(f"  Baris tidak diubah (di-skip filter) : {n_skip:,}")
        print(f"  Baris kota/kab kosong setelah update : {nan_idx:,}")

        return df_nama
# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────────────────────────────────────
df_nama = df.uploaded_file
df_nama.columns = [clean_header(col) for col in df_nama.columns]
sarana = Perbaikan_Nama_Sarana()
df_nama = sarana.run(df_nama)

alamat   = Analisis_Ketepatan_Alamat()
df_hasil = alamat.run(df_nama)
df_nama  = alamat.update_original(df_nama, df_hasil)
df_nama = df_nama.drop(columns=["_matches"])