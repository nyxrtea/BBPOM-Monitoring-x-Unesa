"""add bayesian_cache, risk_cache, forecast_cache tables

Revision ID: 0003_cache
Revises: 0002_staging
Create Date: 2026-06-21

Menambahkan 3 tabel cache untuk menstabilkan hasil Bayesian Risk
Analysis, Risk-Based Inspection, dan Forecasting — supaya hasil tidak
berubah-ubah/dihitung ulang dari nol di setiap request, hanya
di-recompute saat row_count untuk filter combo terkait benar-benar
berubah (ada data baru diupload) atau saat tombol "Refresh Data" diklik.

CATATAN PENGGABUNGAN:
File ini menggabungkan dua migration terpisah yang sebelumnya dibawa
oleh "web_program_map-distribution_forecast_mba_reva.zip"
(a1b2c3d4e567_add_risk_cache.py dan b2c3d4e5f678_add_forecast_cache.py),
yang masing-masing punya down_revision menunjuk ke 'dab0278bea92' —
sebuah revision yang TIDAK ADA LAGI di project ini karena history
migration sudah di-reset ke 0001_initial_clean_schema.py dan
0002_add_staging_path_to_upload_sessions.py pada sesi sebelumnya.
Menjalankan 2 file asli itu apa adanya akan membuat Alembic gagal
mencari revisi induknya. Sebagai gantinya, migration baru ini dibuat
dari nol dengan down_revision = '0002_staging' (revisi terakhir yang
valid di project ini), DITAMBAH tabel bayesian_cache yang sebelumnya
belum punya migration sama sekali (model BayesianCache baru dibuat
sebagai bagian dari penggabungan ini, mengikuti pola RiskCache /
ForecastCache).

Aman dijalankan ulang (idempotent) — setiap CREATE TABLE dibungkus
pengecekan IF NOT EXISTS via Inspector.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


revision      = '0003_cache'
down_revision = '0002_staging'
branch_labels = None
depends_on    = None


def _table_exists(conn, name: str) -> bool:
    return name in Inspector.from_engine(conn).get_table_names()


def upgrade():
    conn = op.get_bind()

    # ── bayesian_cache ────────────────────────────────────────
    if not _table_exists(conn, 'bayesian_cache'):
        op.create_table(
            'bayesian_cache',
            sa.Column('id',           sa.Integer(),   nullable=False, autoincrement=True),
            sa.Column('filter_key',   sa.String(255), nullable=False),
            sa.Column('row_count',    sa.Integer(),   nullable=False, server_default='0'),
            sa.Column('summary_json', sa.Text(),      nullable=True),
            sa.Column('data_json',    sa.Text(),      nullable=True),
            sa.Column('computed_at',  sa.DateTime(),  nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('filter_key', name='uq_bayesian_cache_filter_key'),
        )
        op.create_index(
            'ix_bayesian_cache_filter_key', 'bayesian_cache', ['filter_key']
        )
        print("[migration 0003_cache] Created table bayesian_cache")
    else:
        print("[migration 0003_cache] bayesian_cache already exists — skipped")

    # ── risk_cache ────────────────────────────────────────────
    if not _table_exists(conn, 'risk_cache'):
        op.create_table(
            'risk_cache',
            sa.Column('id',           sa.Integer(),   nullable=False, autoincrement=True),
            sa.Column('filter_key',   sa.String(255), nullable=False),
            sa.Column('row_count',    sa.Integer(),   nullable=False, server_default='0'),
            sa.Column('summary_json', sa.Text(),      nullable=True),
            sa.Column('data_json',    sa.Text(),      nullable=True),
            sa.Column('computed_at',  sa.DateTime(),  nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('filter_key', name='uq_risk_cache_filter_key'),
        )
        op.create_index(
            'ix_risk_cache_filter_key', 'risk_cache', ['filter_key']
        )
        print("[migration 0003_cache] Created table risk_cache")
    else:
        print("[migration 0003_cache] risk_cache already exists — skipped")

    # ── forecast_cache ────────────────────────────────────────
    if not _table_exists(conn, 'forecast_cache'):
        op.create_table(
            'forecast_cache',
            sa.Column('id',          sa.Integer(),   nullable=False, autoincrement=True),
            sa.Column('filter_key',  sa.String(512), nullable=False),
            sa.Column('row_count',   sa.Integer(),   nullable=False, server_default='0'),
            sa.Column('result_json', sa.Text(),      nullable=True),
            sa.Column('computed_at', sa.DateTime(),  nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('filter_key', name='uq_forecast_cache_filter_key'),
        )
        op.create_index(
            'ix_forecast_cache_filter_key', 'forecast_cache', ['filter_key']
        )
        print("[migration 0003_cache] Created table forecast_cache")
    else:
        print("[migration 0003_cache] forecast_cache already exists — skipped")


def downgrade():
    conn = op.get_bind()

    if _table_exists(conn, 'forecast_cache'):
        op.drop_index('ix_forecast_cache_filter_key', 'forecast_cache')
        op.drop_table('forecast_cache')

    if _table_exists(conn, 'risk_cache'):
        op.drop_index('ix_risk_cache_filter_key', 'risk_cache')
        op.drop_table('risk_cache')

    if _table_exists(conn, 'bayesian_cache'):
        op.drop_index('ix_bayesian_cache_filter_key', 'bayesian_cache')
        op.drop_table('bayesian_cache')