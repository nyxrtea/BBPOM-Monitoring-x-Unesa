from backend.database.db import db
from datetime import datetime


class JenisSaranaInstansi(db.Model):
    __tablename__ = "jenis_sarana_instansi"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # Correct jenis_sarana label for this facility
    jenis_sarana = db.Column(
        db.String(100),
        nullable=False,
        index=True,
        comment="Correct jenis_sarana label, e.g. Apotek / Rumah Sakit",
    )

    # Exact facility name to match against drug_distribution.tujuan_penyaluran
    nama_tujuan_penyaluran = db.Column(
        db.String(500),
        nullable=False,
        unique=True,
        index=True,
        comment="Facility name key — matched uppercase against tujuan_penyaluran",
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<JenisSaranaInstansi id={self.id} "
            f"jenis='{self.jenis_sarana}' "
            f"nama='{self.nama_tujuan_penyaluran[:40]}...'>"
        )

    # ── Class-level helpers ──────────────────────────────────

    @classmethod
    def build_lookup(cls) -> dict[str, str]:
        rows = cls.query.all()
        return {
            r.nama_tujuan_penyaluran.upper().strip(): r.jenis_sarana
            for r in rows
            if r.nama_tujuan_penyaluran
        }