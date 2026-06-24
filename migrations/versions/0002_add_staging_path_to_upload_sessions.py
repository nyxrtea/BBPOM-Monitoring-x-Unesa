"""add staging_path to upload_sessions, migrate old status values

Revision ID: 0002_staging
Revises: 0001_clean
Create Date: 2026-06-19

Bagian dari fix alur "jangan simpan ke DB sebelum user konfirmasi":
  - Tambah kolom staging_path (lokasi file parquet sementara hasil
    cleaning, sebelum baris dipindah ke drug_distribution).
  - Update nilai status lama ('ready_to_download', 'downloaded',
    'deleted') ke nilai baru ('ready_to_review', 'saved', 'discarded')
    supaya konsisten dengan UploadStatus enum yang baru.
"""
from alembic import op
import sqlalchemy as sa


revision      = '0002_staging'
down_revision = '0001_clean'
branch_labels = None
depends_on    = None


def upgrade():
    with op.batch_alter_table('upload_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('staging_path', sa.String(length=512), nullable=True))

    # Migrasi nilai status lama → baru (aman dijalankan walau tabel kosong)
    op.execute("""
        UPDATE upload_sessions
        SET status = CASE status
            WHEN 'ready_to_download' THEN 'ready_to_review'
            WHEN 'downloaded'        THEN 'ready_to_review'
            WHEN 'deleted'           THEN 'discarded'
            ELSE status
        END
    """)


def downgrade():
    op.execute("""
        UPDATE upload_sessions
        SET status = CASE status
            WHEN 'ready_to_review' THEN 'ready_to_download'
            WHEN 'saved'           THEN 'downloaded'
            WHEN 'discarded'       THEN 'deleted'
            ELSE status
        END
    """)
    with op.batch_alter_table('upload_sessions', schema=None) as batch_op:
        batch_op.drop_column('staging_path')
