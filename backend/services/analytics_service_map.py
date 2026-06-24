from backend.models.distribution_model_main import Distribution
from backend.database.db import db
from sqlalchemy import func


def get_total_distribusi():

    result = db.session.query(
        func.sum(Distribution.jumlah)
    ).scalar()

    return result or 0


def get_total_obat():

    result = db.session.query(
        func.count(
            func.distinct(
                Distribution.nama_obat_jadi
            )
        )
    ).scalar()

    return result or 0


def get_top_obat():

    result = db.session.query(

        Distribution.nama_obat_jadi,

        func.sum(
            Distribution.jumlah
        )

    ).group_by(

        Distribution.nama_obat_jadi

    ).order_by(

        func.sum(
            Distribution.jumlah
        ).desc()

    ).limit(10).all()

    return result


def get_top_provinsi():

    result = db.session.query(

        Distribution.provinsi,

        func.sum(
            Distribution.jumlah
        )

    ).group_by(

        Distribution.provinsi

    ).order_by(

        func.sum(
            Distribution.jumlah
        ).desc()

    ).limit(10).all()

    return result