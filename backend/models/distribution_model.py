"""
backend/models/distribution_model.py
──────────────────────────────────────
Model SQLAlchemy untuk tabel drug_distribution.
"""

from backend.database.db import db
from sqlalchemy.sql import func


class Distribution(db.Model):

    __tablename__ = 'drug_distribution'

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # ── FK ke upload_sessions ──────────────────────────────────
    # Nullable: setelah user klik "Simpan ke Dashboard", field ini
    # di-set NULL sehingga data tetap ada tapi sesi dihapus.
    upload_session_id = db.Column(
        db.Integer,
        db.ForeignKey('upload_sessions.id', ondelete='SET NULL'),
        nullable=True,
        index=True
    )

    jumlah = db.Column(
        db.Float
    )

    tanggal_penyaluran = db.Column(
        db.String(255)
    )

    tanggal_kedaluwarsa = db.Column(
        db.String(255)
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now()
    )

    nama_zat_aktif = db.Column(
        db.Text
    )

    nama_obat_jadi = db.Column(
        db.Text,
        index=True
    )

    produsen_obat_jadi = db.Column(
        db.Text
    )

    nama_pbf = db.Column(
        db.Text
    )

    provinsi = db.Column(
        db.Text,
        index=True
    )

    kabupaten_kota = db.Column(
        db.Text
    )

    jenis_transaksi = db.Column(
        db.Text
    )

    batch = db.Column(
        db.Text
    )

    satuan = db.Column(
        db.Text
    )

    keterangan = db.Column(
        db.Text
    )

    jenis_sarana = db.Column(
        db.Text,
        index=True
    )

    kategori_obat = db.Column(
        db.Text
    )

    no_faktur = db.Column(
        db.Text
    )

    tujuan_penyaluran = db.Column(
        db.Text
    )

    alamat_tujuan = db.Column(
        db.Text
    )

    nama_kota_kab_tujuan = db.Column(
        db.Text
    )

    nama_provinsi_tujuan = db.Column(
        db.Text
    )

    anomaly_label = db.Column(
        db.Integer,
        default=1
    )

    anomaly_reason = db.Column(
        db.Text,
        default='Normal'
    )