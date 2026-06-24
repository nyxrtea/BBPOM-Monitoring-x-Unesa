from flask import Blueprint, render_template, session, redirect
from sqlalchemy import func
from backend.database.db import db
from backend.models.distribution_model import Distribution

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/dashboard')
def dashboard():

    # =========================
    # LOGIN VALIDATION
    # =========================

    if 'user_id' not in session:
        return redirect('/login')

    # =========================
    # TOTAL DISTRIBUSI
    # =========================

    total_distribusi = db.session.query(
        func.sum(Distribution.jumlah)
    ).scalar() or 0

    # =========================
    # TOTAL OBAT
    # =========================

    total_obat = db.session.query(
        func.count(func.distinct(Distribution.nama_obat_jadi))
    ).scalar() or 0

    # =========================
    # TOTAL KATEGORI
    # =========================

    total_kategori = db.session.query(
        func.count(func.distinct(Distribution.kategori_obat))
    ).scalar() or 0

    # =========================
    # TOTAL JENIS SARANA
    # =========================

    total_sarana = db.session.query(
        func.count(func.distinct(Distribution.jenis_sarana))
    ).scalar() or 0

    # =========================
    # TOTAL ANOMALY
    # =========================

    total_anomaly = Distribution.query.filter(
        Distribution.anomaly_label == -1
    ).count()

    # =========================
    # RECENT DATA
    # =========================

    recent_data = Distribution.query.order_by(
        Distribution.created_at.desc()
    ).limit(10).all()

    # =========================
    # TOP 07 OBAT TERBANYAK
    # =========================

    top_obat = db.session.query(
        Distribution.nama_obat_jadi,
        func.sum(Distribution.jumlah).label('total')
    ).filter(
        Distribution.nama_obat_jadi.isnot(None)
    ).group_by(
        Distribution.nama_obat_jadi
    ).order_by(
        func.sum(Distribution.jumlah).desc()
    ).limit(7).all()

    obat_labels = [item[0] for item in top_obat]
    obat_values = [float(item[1]) if item[1] else 0 for item in top_obat]

    return render_template(
        'dashboard.html',

        # KPI
        total_distribusi=total_distribusi,
        total_obat=total_obat,
        total_kategori=total_kategori,
        total_sarana=total_sarana,
        total_anomaly=total_anomaly,

        # Tabel
        recent_data=recent_data,

        # Chart Top 10 Obat
        obat_labels=obat_labels,
        obat_values=obat_values,
    )