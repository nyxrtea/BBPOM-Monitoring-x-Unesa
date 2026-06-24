"""
backend/routes/upload_routes.py
────────────────────────────────────────────────────────────────
ROUTE MAP
─────────
GET  /upload                     → landing page (choose upload type)
GET  /upload/data-obat           → drug data upload form
POST /upload/data-obat           → run cleaning pipeline (STAGING ONLY, no DB insert)
GET  /upload/result/<uuid>       → cleaning result page (data masih di file staging)
GET  /upload/download/<uuid>     → stream cleaned CSV (TIDAK menyentuh DB)
POST /upload/discard/<uuid>      → hapus file staging, batalkan sesi, balik ke landing
POST /upload/save/<uuid>         → user KONFIRMASI simpan → baris di-insert dari
                                    staging ke drug_distribution dalam 1 transaksi

GET  /upload/jenis-sarana        → jenis sarana upload form
POST /upload/jenis-sarana        → parse + validate + upsert → render result
                                    (tabel referensi kecil, tetap langsung simpan —
                                     tidak termasuk aturan staging di atas)

──────────────────────────────────────────────────────────────────
PRINSIP UTAMA (lihat juga upload_session_model_main.py)
──────────────────────────────────────────────────────────────────
Hasil cleaning data distribusi obat TIDAK PERNAH disimpan ke tabel
drug_distribution sampai user mengklik "Simpan ke Database" dan
mengonfirmasi popup di halaman hasil. Selama proses review:

  • Data hasil cleaning hidup sebagai file pickle "staging" di disk
    (`UploadSession.staging_path`), direferensikan lewat session_uuid.
  • Tombol "Download Hasil Cleaning" membaca dari staging/export file
    — tidak pernah menyentuh tabel drug_distribution.
  • Tombol "Kembali" / "Keluar" menghapus file staging + record sesi.
    Tidak ada baris yang pernah masuk ke drug_distribution, sehingga
    tidak ada apa pun yang perlu di-rollback dari tabel permanen.
  • Tombol "Simpan ke Database" membaca staging_path, lalu
    bulk_save_objects ke drug_distribution dalam SATU transaksi.
    Begitu sukses, file staging dihapus dan status sesi → SAVED.
"""

import os
import uuid
from datetime import datetime
import traceback

import pandas as pd
from flask import (
    Blueprint, render_template, request, session,
    redirect, send_file, url_for, jsonify,
)
from sqlalchemy import text, func

from backend.database.db                        import db
from backend.models.distribution_model_main     import Distribution
from backend.models.upload_session_model_main   import UploadSession, UploadStatus
from backend.models.jenis_sarana_instansi_model import JenisSaranaInstansi
from backend.services.cleaning_service          import clean_dataframe, row_to_model_kwargs

try:
    from backend.ml.anomaly.anomaly_service import detect_anomaly
    _HAS_ANOMALY = True
except ImportError:
    _HAS_ANOMALY = False


upload_bp    = Blueprint("upload", __name__)
EXPORT_DIR   = "exports"
UPLOAD_DIR   = "uploads"
STAGING_DIR  = "staging"     # file pickle sementara, BELUM masuk DB
BATCH_SIZE   = 500

# Valid jenis_sarana labels (used for validation warnings)
_VALID_JENIS_SARANA = {
    "Apotek", "Rumah Sakit", "Puskesmas", "Klinik",
    "Instalasi Farmasi", "Balai Pengobatan", "PBF", "Lainnya",
}

# Preview columns for drug data result page
_PREVIEW_COLS = [
    ("Tujuan Penyaluran",   "tujuan_penyaluran",     35),
    ("Jenis Sarana",        "jenis_sarana",           None),
    ("Nama Obat",           "nama_obat_jadi",         30),
    ("Kategori Obat",       "kategori_obat",          None),
    ("Jumlah",              "jumlah",                 None),
    ("Satuan",              "satuan",                 None),
    ("Kota/Kab Tujuan",     "nama_kota_kab_tujuan",  None),
    ("Provinsi Tujuan",     "nama_provinsi_tujuan",  None),
    ("Tgl Penyaluran",      "tanggal_penyaluran",    None),
    ("Alamat Tujuan",       "alamat_tujuan",          40),
    ("Nama Zat Aktif",      "nama_zat_aktif",         30),
    ("No Faktur",           "no_faktur",              None),
    ("Tgl Kedaluwarsa",     "tanggal_kedaluwarsa",   None),
    ("Produsen Obat",       "produsen_obat_jadi",     30),
    ("Nama PBF",            "nama_pbf",               30),
    ("Provinsi Asal",       "provinsi",               None),
    ("Kota/Kab Asal",       "kabupaten_kota",         None),
    ("Jenis Transaksi",     "jenis_transaksi",        None),
    ("Batch",               "batch",                  None),
    ("Keterangan",          "keterangan",             40),
]

# Kolom yang dicek null/empty di halaman hasil — sekarang dihitung
# dari DataFrame staging, BUKAN query ke tabel drug_distribution,
# karena baris belum ada di sana sama sekali.
_NULL_COLS = [
    ("nama_kota_kab_tujuan", "Kota/Kab Tujuan"),
    ("nama_provinsi_tujuan", "Provinsi Tujuan"),
    ("tujuan_penyaluran",    "Tujuan Penyaluran"),
    ("alamat_tujuan",        "Alamat Tujuan"),
    ("nama_obat_jadi",       "Nama Obat Jadi"),
    ("nama_zat_aktif",       "Nama Zat Aktif"),
    ("jenis_sarana",         "Jenis Sarana"),
    ("no_faktur",            "No Faktur"),
    ("satuan",               "Satuan"),
]


# ─────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────
def _ensure_dirs():
    os.makedirs(EXPORT_DIR,  exist_ok=True)
    os.makedirs(UPLOAD_DIR,  exist_ok=True)
    os.makedirs(STAGING_DIR, exist_ok=True)


def _staging_path_for(sess_uuid: str) -> str:
    return os.path.join(STAGING_DIR, f"staging_{sess_uuid}.pkl")


def _load_staging_df(up_sess: UploadSession) -> pd.DataFrame | None:
    """Baca DataFrame hasil cleaning dari file staging. None kalau hilang/expired."""
    if not up_sess.staging_path or not os.path.exists(up_sess.staging_path):
        return None
    try:
        return pd.read_pickle(up_sess.staging_path)
    except Exception:
        return None


def _delete_session_completely(sess_uuid: str):
    """
    Hapus sesi + SEMUA file terkait (staging & export).
    Dipanggil saat user klik Kembali/Keluar/Batal, ATAU kalau pipeline
    gagal di tengah jalan. TIDAK PERNAH menyentuh drug_distribution,
    karena baris memang belum pernah masuk ke sana.
    """
    sess = UploadSession.query.filter_by(session_uuid=sess_uuid).first()
    if not sess:
        return
    for path in (sess.staging_path, sess.export_path):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    db.session.delete(sess)
    db.session.commit()


def _relabel_lainnya(cleaned_df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if "jenis_sarana" not in cleaned_df.columns or \
       "tujuan_penyaluran" not in cleaned_df.columns:
        return cleaned_df, 0
    mask = cleaned_df["jenis_sarana"] == "Lainnya"
    if not mask.any():
        return cleaned_df, 0
    lookup = JenisSaranaInstansi.build_lookup()
    if not lookup:
        return cleaned_df, 0
    df = cleaned_df.copy()
    mapped = (
        df.loc[mask, "tujuan_penyaluran"]
        .astype(str).str.upper().str.strip()
        .map(lookup)
    )
    matched = mapped.notna()
    df.loc[
        mask & matched.reindex(df.index, fill_value=False),
        "jenis_sarana"
    ] = mapped[matched].values
    return df, int(matched.sum())


def _build_preview(df: pd.DataFrame, n: int = 5) -> list[dict]:
    """Preview n baris pertama LANGSUNG dari DataFrame staging (bukan query DB)."""
    preview = []
    for _, row in df.head(n).iterrows():
        entry = {}
        for label, attr, maxlen in _PREVIEW_COLS:
            raw = row.get(attr) if attr in df.columns else None
            val = "-" if (raw is None or (isinstance(raw, float) and pd.isna(raw))) else str(raw)
            if val.lower() in ("none", "nan", ""):
                val = "-"
            if maxlen and len(val) > maxlen:
                val = val[:maxlen] + "…"
            entry[label] = val
        preview.append(entry)
    return preview


def _build_null_summary(df: pd.DataFrame) -> dict[str, int]:
    """Hitung kolom kosong/NaN dari DataFrame staging (bukan query DB)."""
    _empty = {"", "nan", "none", "null", "-"}
    null_counts = {}
    for col, label in _NULL_COLS:
        if col not in df.columns:
            continue
        n_null = int(
            df[col].isna().sum()
            + df[col].astype(str).str.strip().str.lower().isin(_empty).sum()
        )
        if n_null > 0:
            null_counts[label] = n_null
    return dict(sorted(null_counts.items(), key=lambda x: -x[1]))


def _parse_upload_file(file) -> pd.DataFrame:
    """Read CSV or Excel file into a DataFrame."""
    name = file.filename.lower()
    if name.endswith(".csv"):
        try:
            return pd.read_csv(file, encoding="utf-8-sig", low_memory=False)
        except UnicodeDecodeError:
            file.seek(0)
            return pd.read_csv(file, encoding="latin-1", low_memory=False)
    elif name.endswith(".xlsx"):
        return pd.read_excel(file, engine="openpyxl")
    elif name.endswith(".xls"):
        return pd.read_excel(file, engine="xlrd")
    raise ValueError(f"Format file tidak didukung: {file.filename}")


# ─────────────────────────────────────────────────────────────
# LANDING PAGE
# ─────────────────────────────────────────────────────────────
@upload_bp.route("/upload", methods=["GET"])
def landing():
    if "user_id" not in session:
        return redirect("/login")
    return render_template("upload.html")


# ─────────────────────────────────────────────────────────────
# DRUG DATA UPLOAD — GET (form)
# ─────────────────────────────────────────────────────────────
@upload_bp.route("/upload/data-obat", methods=["GET"])
def upload_data_obat():
    if "user_id" not in session:
        return redirect("/login")
    return render_template("upload_data_obat.html")


# ─────────────────────────────────────────────────────────────
# DRUG DATA UPLOAD — POST (pipeline → STAGING SAJA, tanpa insert DB)
# ─────────────────────────────────────────────────────────────
@upload_bp.route("/upload/data-obat", methods=["POST"])
def upload_data_obat_post():
    if "user_id" not in session:
        return redirect("/login")

    def _err(msg):
        return render_template("upload_data_obat.html", error=msg)

    # ── Validate inputs ──────────────────────────────────────
    kategori_jenis_obat = (
        request.form.get("kategori_jenis_obat") or ""
    ).strip()[:100]
    if not kategori_jenis_obat:
        return _err("Harap isi Jenis Kategori Obat sebelum mengupload file.")

    file = request.files.get("file")
    if not file or not file.filename:
        return _err("Pilih file CSV terlebih dahulu.")
    if not file.filename.lower().endswith(".csv"):
        return _err("Hanya file .csv yang didukung untuk data distribusi obat.")

    _ensure_dirs()
    raw_name = f"raw_{uuid.uuid4().hex}.csv"
    raw_path = os.path.join(UPLOAD_DIR, raw_name)
    file.save(raw_path)

    # ── Create session record (status: PROCESSING) ──────────
    sess_uuid = uuid.uuid4().hex
    up_sess = UploadSession(
        session_uuid        = sess_uuid,
        original_filename   = file.filename,
        status               = UploadStatus.PROCESSING,
        user_id              = session.get("user_id"),
        kategori_jenis_obat  = kategori_jenis_obat,
    )
    db.session.add(up_sess)
    db.session.commit()

    try:
        # ── Run cleaning pipeline (di memori, belum ke DB) ───
        before_df, cleaned_df, summary, process_log = clean_dataframe(
            raw_path,
            kategori_jenis_obat=kategori_jenis_obat,
        )

        # ── Re-label Lainnya rows from jenis_sarana_instansi ─
        cleaned_df, n_relabeled = _relabel_lainnya(cleaned_df)
        if n_relabeled:
            process_log += (
                f"\n  jenis_sarana_instansi lookup: "
                f"{n_relabeled:,} baris 'Lainnya' berhasil dilabeli ulang\n"
            )

        # ── Anomaly detection ────────────────────────────────
        if _HAS_ANOMALY:
            cleaned_df = detect_anomaly(cleaned_df)
        else:
            cleaned_df["anomaly_label"] = 1

        # ── Simpan ke STAGING (pickle) — BUKAN ke DB ─────────
        # Pakai pickle, bukan parquet, karena tidak butuh dependency
        # tambahan (pyarrow/fastparquet) yang mungkin belum terinstal
        # di environment ini. Pickle sudah built-in di pandas dan
        # tetap menyimpan tipe data (termasuk NaN/None) secara akurat.
        staging_path = _staging_path_for(sess_uuid)
        cleaned_df.to_pickle(staging_path)

        # ── Export CSV untuk tombol Download (boleh diunduh
        #    kapan saja, tidak terkait status simpan) ─────────
        export_name = f"clean_{sess_uuid}.csv"
        export_path = os.path.join(EXPORT_DIR, export_name)
        cleaned_df.to_csv(export_path, index=False, encoding="utf-8-sig")

        # ── Update session metadata — TIDAK ADA INSERT ke
        #    drug_distribution di titik ini ──────────────────
        up_sess.status              = UploadStatus.READY_TO_REVIEW
        up_sess.rows_before         = summary["rows_before"]
        up_sess.rows_after          = summary["rows_after"]
        up_sess.duplicates_removed  = summary["duplicates_removed"]
        up_sess.renamed_columns     = summary["renamed_columns"]
        up_sess.jenis_sarana_count  = summary["jenis_sarana_created"]
        up_sess.staging_path        = staging_path
        up_sess.export_path         = export_path
        up_sess.process_log         = process_log
        db.session.commit()

    except Exception as exc:
        traceback.print_exc()
        db.session.rollback()
        _delete_session_completely(sess_uuid)
        return _err(f"Terjadi kesalahan saat memproses: {exc}")
    finally:
        if os.path.exists(raw_path):
            os.remove(raw_path)

    return redirect(url_for("upload.result", sess_uuid=sess_uuid))


# ─────────────────────────────────────────────────────────────
# DRUG DATA — RESULT PAGE (baca dari staging, BUKAN dari DB)
# ─────────────────────────────────────────────────────────────
@upload_bp.route("/upload/result/<sess_uuid>")
def result(sess_uuid: str):
    if "user_id" not in session:
        return redirect("/login")

    up_sess = UploadSession.query.filter_by(
        session_uuid=sess_uuid
    ).first_or_404()

    if up_sess.status in (UploadStatus.DISCARDED, UploadStatus.SAVED):
        # Sesi sudah selesai diproses (disimpan atau dibatalkan) —
        # tidak ada lagi yang bisa direview ulang.
        return redirect(url_for("upload.landing"))

    df = _load_staging_df(up_sess)
    if df is None:
        # File staging hilang (expired/terhapus manual) — sesi tidak
        # bisa diteruskan, bersihkan record dan minta upload ulang.
        _delete_session_completely(sess_uuid)
        return redirect(url_for("upload.landing"))

    # ── Sarana distribution — dihitung dari DataFrame staging ─
    sarana_distribution = (
        df["jenis_sarana"].fillna("Lainnya").value_counts().to_dict()
        if "jenis_sarana" in df.columns else {}
    )

    # ── Preview & null summary — dari DataFrame staging ───────
    preview_data = _build_preview(df, n=5)
    null_summary = _build_null_summary(df)
    empty_city_columns = null_summary.get("Kota/Kab Tujuan", 0)

    summary = {
        "rows_before":          up_sess.rows_before,
        "rows_after":           up_sess.rows_after,
        "duplicates_removed":   up_sess.duplicates_removed,
        "empty_city_columns":   empty_city_columns,
        "renamed_columns":      up_sess.renamed_columns,
        "jenis_sarana_created": up_sess.jenis_sarana_count,
        "sarana_distribution":  sarana_distribution,
        "null_summary":         null_summary,
        "kategori_jenis_obat":  up_sess.kategori_jenis_obat or "",
        "preview_data":         preview_data,
    }

    return render_template(
        "result_upload_data_obat.html",
        up_sess     = up_sess,
        sess_uuid   = sess_uuid,
        summary     = summary,
        process_log = up_sess.process_log or "",
    )


# ─────────────────────────────────────────────────────────────
# DRUG DATA — DOWNLOAD (file saja, TIDAK menyentuh DB)
# ─────────────────────────────────────────────────────────────
@upload_bp.route("/upload/download/<sess_uuid>")
def download(sess_uuid: str):
    if "user_id" not in session:
        return redirect("/login")
    up_sess = UploadSession.query.filter_by(
        session_uuid=sess_uuid
    ).first_or_404()
    if not up_sess.export_path or not os.path.exists(up_sess.export_path):
        return "File tidak ditemukan atau sudah dihapus.", 404

    # Catatan: TIDAK ada perubahan status di sini. Download boleh
    # dilakukan berkali-kali dan tidak mempengaruhi apakah data
    # akhirnya disimpan ke database atau tidak — dua aksi ini lepas.
    return send_file(
        up_sess.export_path,
        as_attachment=True,
        download_name=f"cleaned_{up_sess.original_filename or 'data.csv'}",
        mimetype="text/csv",
    )


# ─────────────────────────────────────────────────────────────
# DRUG DATA — DISCARD (tombol "Kembali" / "Keluar", setelah confirm)
# ─────────────────────────────────────────────────────────────
@upload_bp.route("/upload/discard/<sess_uuid>", methods=["POST"])
def discard(sess_uuid: str):
    """
    Dipanggil via fetch() (AJAX) setelah user mengonfirmasi popup
    "Data belum disimpan dan akan dihapus. Apakah Anda yakin ingin
    kembali?". Karena baris belum pernah masuk ke drug_distribution,
    di sini kita hanya perlu menghapus file staging + record sesi.
    Tidak ada operasi DELETE ke tabel permanen yang diperlukan.

    Mengembalikan JSON (bukan redirect) supaya halaman bisa menampilkan
    notifikasi "Anda akan dikembalikan ke dashboard utama" sebentar di
    sisi client sebelum benar-benar pindah halaman.
    """
    _delete_session_completely(sess_uuid)
    resp = jsonify({"ok": True})
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ─────────────────────────────────────────────────────────────
# DRUG DATA — SAVE (tombol "Simpan ke Database", setelah confirm)
# ─────────────────────────────────────────────────────────────
@upload_bp.route("/upload/save/<sess_uuid>", methods=["POST"])
def save_permanent(sess_uuid: str):
    """
    Dipanggil via fetch() (AJAX) setelah user mengonfirmasi popup
    "Apakah Anda yakin ingin menyimpan data ini ke database?".
    Di sinilah, dan HANYA di sini, baris benar-benar pindah dari file
    staging ke drug_distribution.

    PERUBAHAN: endpoint ini sekarang mengembalikan JSON, BUKAN redirect.
    Sebelumnya pakai redirect biasa lewat <form method="POST"> — pada
    sebagian browser/proxy, request POST yang identik (sama URL + sama
    body kosong) bisa di-cache dan dikembalikan sebagai 304 Not Modified
    alih-alih benar-benar dieksekusi ulang, terutama saat tombol diklik
    lebih dari sekali atau saat ada cache layer di antara klien-server.
    Dengan JSON + fetch(), kita kontrol penuh: response selalu dibaca
    sebagai data, ditambah header no-store di bawah supaya tidak ada
    cache sama sekali pada endpoint yang mengubah data ini.
    """
    up_sess = UploadSession.query.filter_by(
        session_uuid=sess_uuid
    ).first()

    if up_sess is None:
        return jsonify({"ok": False, "message": "Sesi tidak ditemukan."}), 404

    if up_sess.status == UploadStatus.SAVED:
        # Sudah pernah disimpan sebelumnya (misal klik dobel) — idempotent,
        # tetap dianggap sukses karena datanya memang sudah tersimpan.
        resp = jsonify({
            "ok": True,
            "already_saved": True,
            "rows_saved": up_sess.rows_after or 0,
        })
        resp.headers["Cache-Control"] = "no-store"
        return resp

    df = _load_staging_df(up_sess)
    if df is None:
        resp = jsonify({
            "ok": False,
            "message": "File staging tidak ditemukan atau sudah kedaluwarsa. "
                        "Silakan upload ulang.",
        })
        resp.headers["Cache-Control"] = "no-store"
        return resp, 410

    try:
        records = []
        for _, row in df.iterrows():
            # upload_session_id diisi sementara untuk traceability,
            # lalu di-NULL-kan di langkah berikut supaya baris tetap
            # ada walau sesi ini nantinya dihapus.
            records.append(Distribution(**row_to_model_kwargs(row, up_sess.id)))
            if len(records) >= BATCH_SIZE:
                db.session.bulk_save_objects(records)
                db.session.commit()
                records.clear()
        if records:
            db.session.bulk_save_objects(records)
            db.session.commit()

        # Lepas FK supaya baris jadi independen dari sesi upload ini
        db.session.execute(
            text(
                "UPDATE drug_distribution "
                "SET upload_session_id = NULL "
                "WHERE upload_session_id = :sid"
            ),
            {"sid": up_sess.id},
        )

        # Hapus file staging & export — sudah tidak diperlukan lagi
        for path in (up_sess.staging_path, up_sess.export_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

        up_sess.status        = UploadStatus.SAVED
        up_sess.staging_path  = None
        db.session.commit()

    except Exception as exc:
        traceback.print_exc()
        db.session.rollback()
        resp = jsonify({"ok": False, "message": f"Gagal menyimpan ke database: {exc}"})
        resp.headers["Cache-Control"] = "no-store"
        return resp, 500

    resp = jsonify({
        "ok": True,
        "already_saved": False,
        "rows_saved": up_sess.rows_after or 0,
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ─────────────────────────────────────────────────────────────
# JENIS SARANA UPLOAD — GET (form)
# ─────────────────────────────────────────────────────────────
@upload_bp.route("/upload/jenis-sarana", methods=["GET"])
def upload_jenis_sarana():
    if "user_id" not in session:
        return redirect("/login")
    return render_template("upload_data_jenis_sarana.html")


# ─────────────────────────────────────────────────────────────
# JENIS SARANA UPLOAD — POST (validate + upsert + result)
# Catatan: tabel referensi kecil ini TIDAK termasuk aturan staging —
# tetap langsung di-upsert seperti semula, karena bukan data transaksi
# distribusi obat yang jadi fokus perbaikan ini.
# ─────────────────────────────────────────────────────────────
@upload_bp.route("/upload/jenis-sarana", methods=["POST"])
def upload_jenis_sarana_post():
    if "user_id" not in session:
        return redirect("/login")

    def _err(msg, col_validation=None, available_cols=None):
        return render_template(
            "upload_data_jenis_sarana.html",
            error=msg,
            col_validation=col_validation,
            available_cols=available_cols,
        )

    # ── Validate file ────────────────────────────────────────
    file = request.files.get("file")
    if not file or not file.filename:
        return _err("Pilih file terlebih dahulu.")

    fname = file.filename.lower()
    if not (fname.endswith(".csv") or fname.endswith(".xlsx") or fname.endswith(".xls")):
        return _err(
            "Format file tidak didukung. "
            "Gunakan .csv, .xlsx, atau .xls."
        )

    # ── Parse file ───────────────────────────────────────────
    try:
        df = _parse_upload_file(file)
    except Exception as exc:
        return _err(f"Gagal membaca file: {exc}")

    if df.empty:
        return _err("File kosong — tidak ada data yang dapat diproses.")

    # ── Validate required columns ────────────────────────────
    REQUIRED = {"jenis_sarana", "nama_tujuan_penyaluran"}
    col_validation = {
        "jenis_sarana":             "jenis_sarana" in df.columns,
        "nama_tujuan_penyaluran":   "nama_tujuan_penyaluran" in df.columns,
    }
    missing = REQUIRED - set(df.columns)
    if missing:
        return _err(
            f"Kolom wajib tidak ditemukan: {', '.join(sorted(missing))}",
            col_validation=col_validation,
            available_cols=list(df.columns),
        )

    # ── Count empty values for validation report ─────────────
    _empty = {"", "nan", "none", "null"}
    empty_jenis = int(
        df["jenis_sarana"].isna().sum()
        + df["jenis_sarana"].astype(str).str.strip().str.lower().isin(_empty).sum()
    )
    empty_nama = int(
        df["nama_tujuan_penyaluran"].isna().sum()
        + df["nama_tujuan_penyaluran"].astype(str).str.strip().str.lower().isin(_empty).sum()
    )

    # ── Fix sequence desync BEFORE any insert ────────────────
    try:
        db.session.execute(text(
            "SELECT setval("
            "  pg_get_serial_sequence('jenis_sarana_instansi', 'id'),"
            "  COALESCE((SELECT MAX(id) FROM jenis_sarana_instansi), 0) + 1,"
            "  false"
            ")"
        ))
        db.session.commit()
    except Exception:
        pass

    # ── Insert new rows / update changed rows ─────────────────
    rows_inserted = 0
    rows_updated  = 0
    rows_skipped  = 0

    existing_map: dict[str, JenisSaranaInstansi] = {
        r.nama_tujuan_penyaluran.upper(): r
        for r in JenisSaranaInstansi.query.all()
    }

    now = datetime.utcnow()

    for _, row in df.iterrows():
        js = str(row["jenis_sarana"]).strip()
        nm = str(row["nama_tujuan_penyaluran"]).strip()

        if not js or js.lower() in _empty or not nm or nm.lower() in _empty:
            rows_skipped += 1
            continue

        nm_key = nm.upper()

        if nm_key in existing_map:
            obj = existing_map[nm_key]
            if obj.jenis_sarana != js:
                obj.jenis_sarana = js
                obj.updated_at   = now
                rows_updated += 1
        else:
            new_obj = JenisSaranaInstansi(
                jenis_sarana           = js,
                nama_tujuan_penyaluran = nm,
                created_at             = now,
                updated_at             = now,
            )
            db.session.add(new_obj)
            existing_map[nm_key] = new_obj
            rows_inserted += 1

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return _err(f"Gagal menyimpan ke database: {exc}")

    # ── Build result payload ─────────────────────────────────
    sarana_dist = (
        df["jenis_sarana"]
        .astype(str).str.strip()
        .replace(list(_empty), pd.NA)
        .dropna()
        .value_counts()
        .to_dict()
    )

    preview_rows_df = (
        df[["nama_tujuan_penyaluran", "jenis_sarana"]]
        .head(5)
        .fillna("-")
        .astype(str)
        .replace(list(_empty), "-")
    )
    preview = preview_rows_df.to_dict(orient="records")

    total_in_db = JenisSaranaInstansi.query.count()

    result = {
        "filename":      file.filename,
        "rows_total":    len(df),
        "rows_inserted": rows_inserted,
        "rows_updated":  rows_updated,
        "rows_skipped":  rows_skipped,
        "total_in_db":   total_in_db,
        "sarana_dist":   sarana_dist,
        "preview":       preview,
        "validation": {
            "jenis_sarana":             col_validation["jenis_sarana"],
            "nama_tujuan_penyaluran":   col_validation["nama_tujuan_penyaluran"],
            "empty_jenis_sarana":       empty_jenis,
            "empty_nama":               empty_nama,
        },
    }

    return render_template("result_upload_jenis_sarana.html", result=result)


# ─────────────────────────────────────────────────────────────
# Legacy redirect: /upload → landing
# ─────────────────────────────────────────────────────────────
@upload_bp.route("/upload/upload")
def _legacy_upload():
    return redirect(url_for("upload.landing"))