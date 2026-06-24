"""initial clean schema (replaces all previous broken migration history)

Revision ID: 0001_clean
Revises:
Create Date: 2026-06-19

CATATAN PENTING:
─────────────────
History migration sebelumnya (3c15e9822ca8, 405181ba36d8, a0f4080059e8,
dst.) bercabang menjadi 3 root yang tidak terhubung + 1 revision
("ce0daa4e107f") yang dirujuk tapi filenya tidak ada. Karena database
masih testing/kosong, seluruh history lama di-reset dan diganti satu
migration awal yang bersih ini, sinkron 1:1 dengan model Python saat ini
(User, UploadSession, Distribution, JenisSaranaInstansi, DataMatchModelAlamat).

CARA PAKAI:
  1. Jalankan reset_database.sql terlebih dahulu (drop semua tabel lama
     + tabel alembic_version) di pgAdmin atau psql.
  2. Hapus SEMUA file lama di migrations/versions/ KECUALI file ini.
  3. Copy file ini ke migrations/versions/.
  4. Jalankan:  flask db upgrade
"""
from alembic import op
import sqlalchemy as sa


revision      = '0001_clean'
down_revision = None
branch_labels = None
depends_on    = None


def upgrade():

    # ── users ────────────────────────────────────────────────
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('first_name', sa.String(length=255), nullable=False),
        sa.Column('last_name',  sa.String(length=255), nullable=False),
        sa.Column('username',   sa.String(length=255), nullable=False),
        sa.Column('email',      sa.String(length=255), nullable=False),
        sa.Column('password',   sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_unique_constraint('uq_users_username', 'users', ['username'])
    op.create_unique_constraint('uq_users_email',    'users', ['email'])

    # ── upload_sessions ──────────────────────────────────────
    op.create_table(
        'upload_sessions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('session_uuid', sa.String(length=64), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False,
                   server_default='processing'),
        sa.Column('rows_before', sa.Integer(), server_default='0'),
        sa.Column('rows_after', sa.Integer(), server_default='0'),
        sa.Column('duplicates_removed', sa.Integer(), server_default='0'),
        sa.Column('empty_city_columns', sa.Integer(), server_default='0'),
        sa.Column('renamed_columns', sa.Integer(), server_default='0'),
        sa.Column('jenis_sarana_count', sa.Integer(), server_default='0'),
        sa.Column('kategori_jenis_obat', sa.String(length=100), nullable=True),
        sa.Column('export_path', sa.String(length=512), nullable=True),
        sa.Column('process_log', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    )
    op.create_index('ix_upload_sessions_session_uuid', 'upload_sessions',
                     ['session_uuid'], unique=True)

    # ── jenis_sarana_instansi ────────────────────────────────
    op.create_table(
        'jenis_sarana_instansi',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('jenis_sarana', sa.String(length=100), nullable=False),
        sa.Column('nama_tujuan_penyaluran', sa.String(length=500), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_jenis_sarana_instansi_jenis_sarana',
                     'jenis_sarana_instansi', ['jenis_sarana'])
    op.create_index('ix_jenis_sarana_instansi_nama_tujuan_penyaluran',
                     'jenis_sarana_instansi', ['nama_tujuan_penyaluran'], unique=True)

    # ── data_match_model_alamat ──────────────────────────────
    op.create_table(
        'data_match_model_alamat',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tujuan_penyaluran', sa.Text(), nullable=False),
        sa.Column('alamat_tujuan', sa.Text(), nullable=False),
        sa.Column('nama_kota_kab_tujuan', sa.Text(), nullable=False),
        sa.Column('nama_provinsi_tujuan', sa.Text(), nullable=False),
    )
    op.create_index('ix_data_match_model_alamat_tujuan_penyaluran',
                     'data_match_model_alamat', ['tujuan_penyaluran'])
    op.create_index('ix_data_match_model_alamat_nama_kota_kab_tujuan',
                     'data_match_model_alamat', ['nama_kota_kab_tujuan'])

    # ── drug_distribution ─────────────────────────────────────
    op.create_table(
        'drug_distribution',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('upload_session_id', sa.Integer(), nullable=True),
        sa.Column('jumlah', sa.Float(), nullable=True),
        sa.Column('tanggal_penyaluran', sa.String(length=255), nullable=True),
        sa.Column('tanggal_kedaluwarsa', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('nama_zat_aktif', sa.Text(), nullable=True),
        sa.Column('nama_obat_jadi', sa.Text(), nullable=True),
        sa.Column('produsen_obat_jadi', sa.Text(), nullable=True),
        sa.Column('nama_pbf', sa.Text(), nullable=True),
        sa.Column('provinsi', sa.Text(), nullable=True),
        sa.Column('kabupaten_kota', sa.Text(), nullable=True),
        sa.Column('jenis_transaksi', sa.Text(), nullable=True),
        sa.Column('batch', sa.Text(), nullable=True),
        sa.Column('satuan', sa.Text(), nullable=True),
        sa.Column('keterangan', sa.Text(), nullable=True),
        sa.Column('jenis_sarana', sa.Text(), nullable=True),
        sa.Column('kategori_obat', sa.Text(), nullable=True),
        sa.Column('no_faktur', sa.Text(), nullable=True),
        sa.Column('tujuan_penyaluran', sa.Text(), nullable=True),
        sa.Column('alamat_tujuan', sa.Text(), nullable=True),
        sa.Column('nama_kota_kab_tujuan', sa.Text(), nullable=True),
        sa.Column('nama_provinsi_tujuan', sa.Text(), nullable=True),
        sa.Column('anomaly_label', sa.Integer(), server_default='1'),
        sa.Column('anomaly_reason', sa.Text(), server_default='Normal'),
        sa.ForeignKeyConstraint(
            ['upload_session_id'], ['upload_sessions.id'],
            ondelete='SET NULL', name='fk_drug_distribution_upload_session_id'
        ),
    )
    op.create_index('ix_drug_distribution_upload_session_id',
                     'drug_distribution', ['upload_session_id'])
    op.create_index('ix_drug_distribution_nama_obat_jadi',
                     'drug_distribution', ['nama_obat_jadi'])
    op.create_index('ix_drug_distribution_provinsi',
                     'drug_distribution', ['provinsi'])
    op.create_index('ix_drug_distribution_jenis_sarana',
                     'drug_distribution', ['jenis_sarana'])


def downgrade():
    op.drop_table('drug_distribution')
    op.drop_table('data_match_model_alamat')
    op.drop_table('jenis_sarana_instansi')
    op.drop_table('upload_sessions')
    op.drop_table('users')
