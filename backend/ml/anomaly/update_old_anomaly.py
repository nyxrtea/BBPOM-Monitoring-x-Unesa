from app import app

from backend.database.db import db
from backend.models.distribution_model import Distribution


with app.app_context():

    data = Distribution.query.all()

    total_updated = 0

    for item in data:

        reasons = []

        # ==========================================
        # VALIDASI JUMLAH
        # ==========================================

        try:

            jumlah = float(item.jumlah)

        except:

            jumlah = 0

        # ==========================================
        # RULE 1
        # DISTRIBUSI BESAR
        # ==========================================

        if jumlah >= 1000000:

            reasons.append(
                'Distribusi sangat besar'
            )

        # ==========================================
        # RULE 2
        # JUMLAH INVALID
        # ==========================================

        if jumlah <= 0:

            reasons.append(
                'Jumlah distribusi tidak valid'
            )

        # ==========================================
        # RULE 3
        # ALAMAT KOSONG
        # ==========================================

        alamat = str(
            item.alamat_tujuan or ''
        ).strip()

        if alamat == '' or alamat.lower() == 'nan':

            reasons.append(
                'Alamat tujuan kosong'
            )

        # ==========================================
        # RULE 4
        # NAMA OBAT KOSONG
        # ==========================================

        obat = str(
            item.nama_obat_jadi or ''
        ).strip()

        if obat == '' or obat.lower() == 'nan':

            reasons.append(
                'Nama obat kosong'
            )

        # ==========================================
        # RULE 5
        # TUJUAN PENYALURAN KOSONG
        # ==========================================

        tujuan = str(
            item.tujuan_penyaluran or ''
        ).strip()

        if tujuan == '' or tujuan.lower() == 'nan':

            reasons.append(
                'Tujuan penyaluran kosong'
            )

        # ==========================================
        # RULE 6
        # KOTA TUJUAN KOSONG
        # ==========================================

        kota = str(
            item.nama_kota_kab_tujuan or ''
        ).strip()

        if kota == '' or kota.lower() == 'nan':

            reasons.append(
                'Kota tujuan kosong'
            )

        # ==========================================
        # UPDATE STATUS
        # ==========================================

        if len(reasons) > 0:

            item.anomaly_label = -1

            item.anomaly_reason = ', '.join(
                reasons
            )

        else:

            item.anomaly_label = 1

            item.anomaly_reason = 'Normal'

        total_updated += 1

    # ==========================================
    # COMMIT DATABASE
    # ==========================================

    db.session.commit()

    print(
        f'{total_updated} data anomaly berhasil diperbarui'
    )