"""
backend/models/upload_session_model_main.py
──────────────────────────────────────────────
Tracks every CSV upload as a temporary "session".

PERUBAHAN PENTING (fix alur simpan-ke-database):
──────────────────────────────────────────────────
Sebelumnya: hasil cleaning langsung di-bulk_insert ke drug_distribution
saat pipeline berjalan (POST /upload/data-obat). Tombol "Simpan ke
Database" hanya men-UPDATE upload_session_id jadi NULL — bukan benar-
benar menyimpan, karena datanya sudah ada di tabel permanen sejak awal.
Akibatnya: klik "Kembali"/"Keluar" tanpa simpan tetap meninggalkan data
di drug_distribution sampai proses discard berjalan sukses.

Sekarang: hasil cleaning HANYA disimpan sebagai file sementara di disk
(lihat `staging_path`, format parquet) yang dirujuk oleh sesi ini.
Tidak ada baris yang masuk ke drug_distribution sampai user mengklik
"Simpan ke Database" dan mengonfirmasi — baru di titik itu baris
di-insert dari file staging ke tabel permanen dalam satu transaksi.

State machine:
  processing → ready_to_review → saved
                     ↓
              (user klik Kembali/Keluar, ATAU sesi expired)
                     ↓
                  discarded
"""

from backend.database.db import db
from datetime import datetime
import enum


class UploadStatus(str, enum.Enum):
    PROCESSING       = "processing"        # pipeline sedang berjalan
    READY_TO_REVIEW  = "ready_to_review"    # cleaning selesai, menunggu aksi user (BELUM masuk DB)
    SAVED            = "saved"              # user konfirmasi simpan → baris sudah masuk drug_distribution
    DISCARDED        = "discarded"          # user batal / kembali → file staging dihapus, tidak ada baris masuk DB


class UploadSession(db.Model):
    __tablename__ = "upload_sessions"

    id           = db.Column(db.Integer, primary_key=True, autoincrement=True)
    session_uuid = db.Column(
        db.String(64), unique=True, nullable=False, index=True
    )
    original_filename  = db.Column(db.String(255))
    status              = db.Column(
        db.String(32), default=UploadStatus.PROCESSING, nullable=False
    )

    # ── Cleaning stats (dihitung dari DataFrame staging, bukan DB) ──
    rows_before        = db.Column(db.Integer, default=0)
    rows_after         = db.Column(db.Integer, default=0)
    duplicates_removed = db.Column(db.Integer, default=0)
    empty_city_columns = db.Column(db.Integer, default=0)
    renamed_columns    = db.Column(db.Integer, default=0)
    jenis_sarana_count = db.Column(db.Integer, default=0)

    kategori_jenis_obat = db.Column(
        db.String(100),
        nullable=True,
        comment="Drug-type category label entered by user before upload (max 100 chars)",
    )

    # ── File staging & log ────────────────────────────────────
    # staging_path: file SEMENTARA hasil cleaning (parquet), BELUM masuk DB.
    #               Sumber data untuk tombol Download & tombol Simpan.
    # export_path:  file CSV final untuk Download (dibuat dari staging_path,
    #               tidak terkait status simpan/tidak — boleh diunduh kapan saja).
    staging_path = db.Column(db.String(512))
    export_path  = db.Column(db.String(512))
    process_log  = db.Column(db.Text)

    # ── Timestamps & ownership ───────────────────────────────
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at   = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    user_id      = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )