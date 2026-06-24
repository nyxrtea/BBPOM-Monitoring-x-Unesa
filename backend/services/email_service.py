"""
backend/services/email_service.py
─────────────────────────────────────────────
Kirim email lewat Gmail SMTP (smtplib bawaan Python — tidak perlu
install dependency tambahan apapun).

Kredensial dibaca dari environment variable (.env), TIDAK pernah
di-hardcode di source code:
  MAIL_USERNAME      — alamat Gmail pengirim
  MAIL_PASSWORD      — Gmail App Password (16 digit, bukan password biasa)
  MAIL_SENDER_NAME    — nama yang muncul di inbox penerima

Cara membuat App Password: https://myaccount.google.com/apppasswords
(memerlukan 2-Step Verification aktif di akun Gmail tersebut).
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587


class EmailConfigError(Exception):
    """Dilempar saat MAIL_USERNAME / MAIL_PASSWORD belum diset di .env."""
    pass


def _get_credentials() -> tuple[str, str, str]:
    username = os.environ.get("MAIL_USERNAME", "").strip()
    password = os.environ.get("MAIL_PASSWORD", "").strip()
    sender_name = os.environ.get("MAIL_SENDER_NAME", "BPOM Monitoring").strip()

    if not username or not password:
        raise EmailConfigError(
            "MAIL_USERNAME / MAIL_PASSWORD belum diset. "
            "Cek file .env — lihat .env.example untuk format yang benar."
        )
    return username, password, sender_name


def send_email(to_email: str, subject: str, html_body: str, text_body: str = "") -> None:
    """
    Kirim satu email HTML lewat Gmail SMTP.

    Raises:
        EmailConfigError: kredensial belum diset.
        smtplib.SMTPException: gagal autentikasi/koneksi/kirim — biarkan
            menjalar ke pemanggil supaya bisa ditangani sebagai response
            error API, bukan ditelan diam-diam.
    """
    username, password, sender_name = _get_credentials()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{username}>"
    msg["To"] = to_email

    if text_body:
        msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT) as server:
        server.starttls()
        server.login(username, password)
        server.sendmail(username, to_email, msg.as_string())


def send_otp_email(to_email: str, otp_code: str, expiry_minutes: int = 5) -> None:
    """Kirim email berisi kode OTP untuk reset password."""
    subject = f"Kode Verifikasi BBPOM Monitoring: {otp_code}"

    html_body = f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:480px;margin:0 auto;
                background:#f6f8fb;padding:32px 24px">
      <div style="background:#fff;border-radius:16px;padding:32px 28px;
                  box-shadow:0 4px 16px rgba(0,0,0,.06)">
        <div style="text-align:center;margin-bottom:24px">
          <div style="width:52px;height:52px;border-radius:14px;
                      background:linear-gradient(135deg,#1a56db,#1e3a8a);
                      display:inline-flex;align-items:center;justify-content:center;
                      color:#fff;font-size:24px;font-weight:800">🛡</div>
        </div>
        <h2 style="text-align:center;color:#111827;font-size:18px;margin:0 0 8px">
          Kode Verifikasi Reset Password
        </h2>
        <p style="text-align:center;color:#6b7280;font-size:13.5px;margin:0 0 24px;line-height:1.6">
          Gunakan kode di bawah ini untuk melanjutkan proses reset password
          akun BPOM Monitoring Anda.
        </p>
        <div style="background:#eff6ff;border:1.5px dashed #93c5fd;border-radius:12px;
                    padding:18px;text-align:center;margin-bottom:24px">
          <span style="font-size:32px;font-weight:800;letter-spacing:8px;color:#1a56db">
            {otp_code}
          </span>
        </div>
        <p style="text-align:center;color:#9ca3af;font-size:12px;margin:0 0 4px">
          Kode ini berlaku selama <b>{expiry_minutes} menit</b>.
        </p>
        <p style="text-align:center;color:#9ca3af;font-size:11.5px;margin:16px 0 0;line-height:1.6">
          Jika Anda tidak meminta reset password, abaikan email ini.
          Akun Anda tetap aman.
        </p>
      </div>
      <p style="text-align:center;color:#9ca3af;font-size:11px;margin-top:18px">
        © BBPOM Monitoring Surabaya X Unesa — Drug Distribution System
      </p>
    </div>
    """

    text_body = (
        f"Kode Verifikasi BPOM Monitoring: {otp_code}\n\n"
        f"Kode ini berlaku selama {expiry_minutes} menit.\n"
        f"Jika Anda tidak meminta reset password, abaikan email ini."
    )

    send_email(to_email, subject, html_body, text_body)
