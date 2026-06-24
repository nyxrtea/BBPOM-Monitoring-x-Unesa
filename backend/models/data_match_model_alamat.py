import pandas as pd
from backend.database.db import db


class DataMatchModelAlamat(db.Model):
    __tablename__ = "data_match_model_alamat"

    id                   = db.Column(db.Integer, primary_key=True, autoincrement=True)
    tujuan_penyaluran    = db.Column(db.Text, nullable=False, index=True)
    alamat_tujuan        = db.Column(db.Text, nullable=False)
    nama_kota_kab_tujuan = db.Column(db.Text, nullable=False, index=True)
    nama_provinsi_tujuan = db.Column(db.Text, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<DataMatchModelAlamat id={self.id} "
            f"kota='{self.nama_kota_kab_tujuan}'>"
        )

    @classmethod
    def build_lookup_df(cls) -> pd.DataFrame:
        """
        Return all rows as a DataFrame with the 4 key columns.
        Returns an empty DataFrame if the table is empty or unavailable.

        Called once per upload in cleaning_service.py and passed into
        run_pipeline() → Automatisasi_Fill_Tujuan_Lokasi_Sarana.
        """
        try:
            rows = cls.query.with_entities(
                cls.tujuan_penyaluran,
                cls.alamat_tujuan,
                cls.nama_kota_kab_tujuan,
                cls.nama_provinsi_tujuan,
            ).all()

            if not rows:
                return pd.DataFrame(columns=[
                    "tujuan_penyaluran", "alamat_tujuan",
                    "nama_kota_kab_tujuan", "nama_provinsi_tujuan",
                ])

            return pd.DataFrame(rows, columns=[
                "tujuan_penyaluran", "alamat_tujuan",
                "nama_kota_kab_tujuan", "nama_provinsi_tujuan",
            ])

        except Exception as exc:
            print(f"[WARNING] DataMatchModelAlamat.build_lookup_df() gagal: {exc}")
            return pd.DataFrame(columns=[
                "tujuan_penyaluran", "alamat_tujuan",
                "nama_kota_kab_tujuan", "nama_provinsi_tujuan",
            ])
