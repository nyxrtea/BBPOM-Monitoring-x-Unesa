"""
backend/models/password_reset_otp_model.py
─────────────────────────────────────────────
Menyimpan kode OTP (One-Time Password) untuk alur reset password
via email. Setiap baris berlaku untuk SATU email + SATU kode, dengan
masa berlaku terbatas (default 5 menit) dan pembatasan jumlah
percobaan verifikasi yang salah.

Alur penggunaan (lihat auth_routes.py):
  1. POST /forgot-password/send-otp   → buat baris baru, kirim email
  2. POST /forgot-password/verify-otp → cek kode & expiry & attempts
  3. POST /forgot-password/reset      → verifikasi ulang, lalu update
                                          User.password, hapus baris OTP
"""

import random
import string
from datetime import datetime, timedelta

from backend.database.db import db


OTP_LENGTH          = 6
OTP_EXPIRY_MINUTES   = 5
OTP_MAX_ATTEMPTS     = 5     # percobaan verifikasi salah sebelum kode di-invalidate
OTP_RESEND_COOLDOWN  = 60    # detik, jarak minimum antar pengiriman ulang


class PasswordResetOTP(db.Model):
    __tablename__ = "password_reset_otp"

    id          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email       = db.Column(db.String(255), nullable=False, index=True)
    otp_code    = db.Column(db.String(6),   nullable=False)

    attempts    = db.Column(db.Integer, default=0, nullable=False)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)

    created_at  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at  = db.Column(db.DateTime, nullable=False)

    # ── Helpers ──────────────────────────────────────────────
    @staticmethod
    def generate_code() -> str:
        """6 digit numerik, contoh: '042371'."""
        return "".join(random.choices(string.digits, k=OTP_LENGTH))

    @classmethod
    def create_for(cls, email: str) -> "PasswordResetOTP":
        """
        Buat baris OTP baru untuk email ini. Baris OTP lama (belum
        terverifikasi) untuk email yang sama dihapus dulu — supaya
        tidak ada lebih dari satu kode aktif sekaligus per email.
        """
        cls.query.filter_by(email=email, is_verified=False).delete()

        otp = cls(
            email=email,
            otp_code=cls.generate_code(),
            attempts=0,
            is_verified=False,
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES),
        )
        db.session.add(otp)
        db.session.commit()
        return otp

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    @property
    def is_locked(self) -> bool:
        """True jika sudah melebihi batas percobaan salah."""
        return self.attempts >= OTP_MAX_ATTEMPTS

    def seconds_until_resend_allowed(self) -> int:
        """
        Berapa detik lagi sebelum boleh kirim ulang OTP untuk email
        yang sama. 0 berarti sudah boleh kirim ulang sekarang.
        """
        elapsed = (datetime.utcnow() - self.created_at).total_seconds()
        remaining = OTP_RESEND_COOLDOWN - elapsed
        return max(0, int(remaining))

    def __repr__(self) -> str:
        return f"<PasswordResetOTP email={self.email!r} verified={self.is_verified}>"
