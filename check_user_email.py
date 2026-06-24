"""
check_user_email.py
─────────────────────────────────────────────
Script diagnostic untuk mengecek LANGSUNG apakah email yang Anda
coba di form lupa password benar-benar match dengan yang ada di
tabel users — tanpa lewat Flask/browser sama sekali.

Jalankan di folder project (sejajar dengan app.py), venv aktif:

    python check_user_email.py
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sqlalchemy import create_engine, text

db_url = os.environ.get(
    'DATABASE_URL',
    'postgresql://postgres:postgres@localhost/bpom_db'
)
print(f"Connecting to: {db_url.split('@')[-1] if '@' in db_url else db_url}\n")

engine = create_engine(db_url)

with engine.connect() as conn:
    print("=" * 60)
    print("SEMUA EMAIL YANG TERDAFTAR DI TABEL users:")
    print("=" * 60)
    result = conn.execute(text("SELECT id, username, email FROM users ORDER BY id"))
    rows = result.fetchall()

    if not rows:
        print("❌ TABEL users KOSONG — belum ada akun sama sekali!")
        print("   Jalankan: flask create-user")
    else:
        for row in rows:
            # Tampilkan email dengan tanda kutip supaya spasi tersembunyi
            # di awal/akhir bisa terlihat jelas.
            print(f"  id={row[0]:<4} username={row[1]!r:<25} email={row[2]!r}")

    print()
    target = input("Masukkan email yang Anda coba di form lupa password: ").strip()
    print()
    print("=" * 60)
    print(f"Mencari email persis: {target!r}")
    print("=" * 60)

    result2 = conn.execute(
        text("SELECT id, username, email FROM users WHERE email = :email"),
        {"email": target}
    )
    match = result2.fetchone()

    if match:
        print(f"✅ DITEMUKAN: id={match[0]}, username={match[1]}, email={match[2]!r}")
    else:
        print(f"❌ TIDAK DITEMUKAN exact match untuk: {target!r}")
        print()
        print("Mencoba pencarian case-insensitive...")
        result3 = conn.execute(
            text("SELECT id, username, email FROM users WHERE LOWER(email) = LOWER(:email)"),
            {"email": target}
        )
        match2 = result3.fetchone()
        if match2:
            print(f"⚠️  DITEMUKAN tapi beda casing!")
            print(f"   Di database : {match2[2]!r}")
            print(f"   Yang dicoba : {target!r}")
        else:
            print("❌ Benar-benar tidak ada, bahkan dengan case-insensitive.")
