"""add password_reset_otp table

Revision ID: 0004_otp
Revises: 0003_cache
Create Date: 2026-06-22

Tabel untuk menyimpan kode OTP sementara saat alur "Lupa Password"
via email (lihat backend/models/password_reset_otp_model.py dan
endpoint baru di auth_routes.py: /forgot-password/send-otp,
/verify-otp, /reset).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


revision      = '0004_otp'
down_revision = '0003_cache'
branch_labels = None
depends_on    = None


def _table_exists(conn, name: str) -> bool:
    return name in Inspector.from_engine(conn).get_table_names()


def upgrade():
    conn = op.get_bind()

    if not _table_exists(conn, 'password_reset_otp'):
        op.create_table(
            'password_reset_otp',
            sa.Column('id',          sa.Integer(),    nullable=False, autoincrement=True),
            sa.Column('email',       sa.String(255),  nullable=False),
            sa.Column('otp_code',    sa.String(6),    nullable=False),
            sa.Column('attempts',    sa.Integer(),    nullable=False, server_default='0'),
            sa.Column('is_verified', sa.Boolean(),    nullable=False, server_default=sa.false()),
            sa.Column('created_at', sa.DateTime(),    nullable=False),
            sa.Column('expires_at', sa.DateTime(),    nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(
            'ix_password_reset_otp_email', 'password_reset_otp', ['email']
        )
        print("[migration 0004_otp] Created table password_reset_otp")
    else:
        print("[migration 0004_otp] password_reset_otp already exists — skipped")


def downgrade():
    conn = op.get_bind()
    if _table_exists(conn, 'password_reset_otp'):
        op.drop_index('ix_password_reset_otp_email', 'password_reset_otp')
        op.drop_table('password_reset_otp')
