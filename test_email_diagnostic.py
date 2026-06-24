"""
test_email_diagnostic.py
─────────────────────────────────────────────
Script diagnostic MANDIRI untuk mengetes pengiriman email OTP secara
langsung, TANPA lewat Flask/browser sama sekali. Jalankan ini di
terminal (folder project, venv aktif):

    python test_email_diagnostic.py

Ini akan menunjukkan PERSIS di langkah mana masalahnya: .env tidak
terbaca, kredensial salah, atau benar-benar terkirim (cek inbox
setelah ini selesai tanpa error).
"""

import os
import sys

print("=" * 60)
print("LANGKAH 1: Cek apakah .env terbaca")
print("=" * 60)

try:
    from dotenv import load_dotenv
    loaded = load_dotenv()
    print(f"python-dotenv terinstall, load_dotenv() return: {loaded}")
except ImportError:
    print("python-dotenv TIDAK terinstall — pakai fallback manual")
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    print(f"Mencari file .env di: {env_path}")
    if os.path.exists(env_path):
        print(".env DITEMUKAN, membaca manual...")
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip())
    else:
        print("❌ .env TIDAK DITEMUKAN di lokasi ini!")
        print("   Pastikan file .env ada SEJAJAR dengan app.py")
        sys.exit(1)

print()
print("=" * 60)
print("LANGKAH 2: Cek nilai MAIL_USERNAME dan MAIL_PASSWORD")
print("=" * 60)

username = os.environ.get("MAIL_USERNAME", "")
password = os.environ.get("MAIL_PASSWORD", "")
sender_name = os.environ.get("MAIL_SENDER_NAME", "BPOM Monitor")

print(f"MAIL_USERNAME    = {username!r}")
print(f"MAIL_PASSWORD    = {'*' * len(password)} (panjang: {len(password)} karakter)")
print(f"MAIL_SENDER_NAME = {sender_name!r}")

if not username:
    print("❌ MAIL_USERNAME KOSONG — cek isi file .env Anda!")
    sys.exit(1)
if not password:
    print("❌ MAIL_PASSWORD KOSONG — cek isi file .env Anda!")
    sys.exit(1)

if len(password.replace(' ', '')) != 16:
    print(f"⚠️  PERINGATAN: App Password Gmail seharusnya 16 karakter,")
    print(f"   tapi yang terbaca {len(password.replace(' ', ''))} karakter.")
    print(f"   Pastikan tidak ada tanda kutip atau spasi ekstra di .env")

print()
print("=" * 60)
print("LANGKAH 3: Coba kirim email TEST langsung")
print("=" * 60)

test_target = input("Masukkan email TUJUAN untuk tes (email Anda sendiri): ").strip()

if not test_target:
    print("❌ Email tujuan tidak boleh kosong.")
    sys.exit(1)

import smtplib
from email.mime.text import MIMEText

try:
    print(f"\nMencoba konek ke smtp.gmail.com:587 ...")
    server = smtplib.SMTP("smtp.gmail.com", 587, timeout=15)
    server.set_debuglevel(1)  # tampilkan detail percakapan SMTP

    print("Memulai TLS...")
    server.starttls()

    print(f"Login sebagai {username} ...")
    server.login(username, password)

    print("Login BERHASIL. Mengirim email test...")
    msg = MIMEText("Ini email test dari script diagnostic BPOM Monitor.")
    msg["Subject"] = "TEST — BPOM Monitor Email Diagnostic"
    msg["From"] = username
    msg["To"] = test_target

    server.sendmail(username, test_target, msg.as_string())
    server.quit()

    print()
    print("✅ EMAIL TERKIRIM TANPA ERROR.")
    print(f"   Cek inbox & folder Spam di: {test_target}")
    print("   Jika tetap tidak ada setelah 1-2 menit, kemungkinan")
    print("   ada delay di sisi Gmail, atau email tujuan memblokir")
    print("   pengirim ini.")

except smtplib.SMTPAuthenticationError as e:
    print()
    print("❌ GAGAL LOGIN (SMTPAuthenticationError)")
    print(f"   Detail: {e}")
    print()
    print("   Kemungkinan sebab:")
    print("   1. App Password salah/sudah di-revoke — buat App Password baru")
    print("      di https://myaccount.google.com/apppasswords")
    print("   2. 2-Step Verification belum aktif di akun Gmail ini")
    print("   3. MAIL_USERNAME bukan alamat Gmail yang sama dengan")
    print("      App Password yang dibuat")

except smtplib.SMTPException as e:
    print()
    print(f"❌ SMTP ERROR: {type(e).__name__}: {e}")

except Exception as e:
    print()
    print(f"❌ ERROR TAK TERDUGA: {type(e).__name__}: {e}")
