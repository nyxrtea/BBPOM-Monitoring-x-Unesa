"""
backend/routes/auth_routes.py
────────────────────────────────────────────────────────────────
PERBAIKAN KEAMANAN (sebelum deploy)
────────────────────────────────────────────────────────────────
SEBELUMNYA: password disimpan dan dibandingkan sebagai PLAIN TEXT
(`password=password` langsung ke kolom DB, lalu dicocokkan lewat
`User.query.filter_by(username=username, password=password)`).
Ini berarti siapa pun yang punya akses baca ke database — termasuk
backup, dump SQL, atau kebocoran data — bisa langsung membaca semua
password user tanpa usaha sama sekali.

SEKARANG: password di-hash dengan `werkzeug.security` (PBKDF2-SHA256,
fungsi bawaan Flask, tidak perlu install dependency tambahan):
  - generate_password_hash(password)        → saat register & reset
  - check_password_hash(user.password, pw)  → saat login

Kolom `password` di tabel `users` TETAP `db.String(255)` — hash
PBKDF2 dari werkzeug panjangnya ±94 karakter, jadi tidak perlu
migration skema, cukup deploy ulang kode ini. Tapi karena format
data di kolom itu berubah dari "plain text" jadi "hash string",
SEMUA password lama yang sudah ada di database (kalau ada) tidak
akan bisa login lagi — lihat catatan migrasi data di bagian bawah
file ini.

Validasi kompleksitas password (panjang, huruf besar/kecil, angka,
simbol) TIDAK diubah — itu sudah baik dan dipertahankan persis sama.
"""

from flask import Blueprint
from flask import render_template
from flask import request
from flask import redirect
from flask import flash
from flask import session
from flask import jsonify

import re

from werkzeug.security import generate_password_hash, check_password_hash

from backend.database.db import db
from backend.models.user_model import User
from backend.models.password_reset_otp_model import (
    PasswordResetOTP,
    OTP_EXPIRY_MINUTES,
    OTP_MAX_ATTEMPTS,
)
from backend.services.email_service import send_otp_email, EmailConfigError


auth_bp = Blueprint(
    'auth',
    __name__
)


def _validate_password_complexity(password: str) -> str | None:
    """
    Cek kompleksitas password (sama persis dengan validasi lama).
    Return pesan error pertama yang gagal, atau None jika semua lolos.
    """
    if len(password) < 8:
        return 'Password minimal 8 karakter'
    if not re.search(r'[A-Z]', password):
        return 'Password wajib memiliki huruf besar'
    if not re.search(r'[a-z]', password):
        return 'Password wajib memiliki huruf kecil'
    if not re.search(r'[0-9]', password):
        return 'Password wajib memiliki angka'
    if not re.search(r'[\W_]', password):
        return 'Password wajib memiliki simbol'
    return None


# =========================
# REGISTER
# =========================

def _create_user(first_name, last_name, username, email, password, confirm_password):
    """
    Logic inti registrasi user — dipisah dari route HTTP supaya bisa
    dipakai ulang oleh CLI command 'flask create-user' (lihat
    backend/cli/create_user_command.py) tanpa duplikasi kode.

    Return: (success: bool, message: str)
    """
    existing_username = User.query.filter_by(username=username).first()
    if existing_username:
        return False, 'Username sudah digunakan'

    existing_email = User.query.filter_by(email=email).first()
    if existing_email:
        return False, 'Email sudah digunakan'

    if password != confirm_password:
        return False, 'Konfirmasi password tidak cocok'

    complexity_error = _validate_password_complexity(password)
    if complexity_error:
        return False, complexity_error

    new_user = User(
        first_name=first_name,
        last_name=last_name,
        username=username,
        email=email,
        password=generate_password_hash(password),
    )
    db.session.add(new_user)
    db.session.commit()

    return True, 'Registrasi berhasil'


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """
    KEPUTUSAN KEAMANAN: registrasi publik DITUTUP TOTAL.

    Aplikasi ini menyimpan data distribusi obat yang sensitif secara
    bisnis/regulasi — sehingga siapa pun di internet TIDAK BOLEH bisa
    membuat akun sendiri dan langsung mengakses dashboard. Akun baru
    sekarang hanya bisa dibuat oleh admin lewat command line:

        flask create-user

    (lihat backend/cli/create_user_command.py)

    Route ini dipertahankan (bukan dihapus) supaya tidak ada link rusak
    jika ada referensi /register di tempat lain, tapi sekarang selalu
    mengembalikan 403 — baik GET maupun POST.
    """
    return render_template('registration_closed.html'), 403


# =========================
# LOGIN
# =========================

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':

        username = request.form['username']

        password = request.form['password']

        # PERBAIKAN: tidak lagi mencocokkan password di WHERE clause
        # (filter_by(username=..., password=...) lama membandingkan
        # plain text langsung di query, yang tidak mungkin dilakukan
        # lagi sekarang karena kolom password berisi hash).
        #
        # Sekarang: ambil user berdasarkan username SAJA, lalu
        # verifikasi password lewat check_password_hash() yang
        # membandingkan hash secara aman (constant-time comparison),
        # sehingga juga lebih tahan timing attack dibanding
        # perbandingan string biasa.
        user = User.query.filter_by(
            username=username
        ).first()

        if not user or not check_password_hash(user.password, password):

            flash(
                'Username atau password salah',
                'danger'
            )

            return redirect('/login')

        session['user_id'] = user.id

        session['username'] = user.username

        return redirect('/dashboard')

    return render_template(
        'login.html'
    )

# =========================
# FORGOT PASSWORD
# =========================

@auth_bp.route('/forgot-password', methods=['GET'])
def forgot_password():
    """
    Hanya menampilkan halaman 3-panel (email → OTP → password baru).
    Logic-nya sekarang dipecah jadi 3 endpoint JSON terpisah di bawah,
    karena template forgot_password.html sudah memanggil masing-masing
    secara independen lewat fetch() — bukan satu form submit besar
    seperti versi lama.
    """
    return render_template('forgot_password.html')


@auth_bp.route('/forgot-password/send-otp', methods=['POST'])
def send_otp():
    """
    Body JSON: {"email": "..."}

    Generate kode OTP 6 digit, simpan ke tabel password_reset_otp
    dengan masa berlaku 5 menit, lalu kirim lewat Gmail SMTP.

    PERTIMBANGAN KEAMANAN: response untuk email yang TIDAK terdaftar
    sengaja dibuat SAMA dengan email yang terdaftar (sukses generik),
    supaya endpoint ini tidak bisa dipakai untuk mengecek/scan email
    mana saja yang punya akun di sistem ini (user enumeration).
    OTP tetap hanya benar-benar dibuat & dikirim untuk email yang
    valid; untuk email tak terdaftar, server diam-diam tidak melakukan
    apa-apa tapi tetap melapor "berhasil" ke client.
    """
    data  = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()

    if not email:
        return jsonify(success=False, message='Email wajib diisi.'), 400

    user = User.query.filter_by(email=email).first()

    if user:
        # ── Rate limit: cegah spam kirim ulang dalam jeda terlalu dekat ──
        existing = (
            PasswordResetOTP.query
            .filter_by(email=email, is_verified=False)
            .order_by(PasswordResetOTP.created_at.desc())
            .first()
        )
        if existing and not existing.is_expired:
            wait = existing.seconds_until_resend_allowed()
            if wait > 0:
                return jsonify(
                    success=False,
                    message=f'Tunggu {wait} detik sebelum meminta kode baru.'
                ), 429

        otp = PasswordResetOTP.create_for(email)

        try:
            send_otp_email(email, otp.otp_code, expiry_minutes=OTP_EXPIRY_MINUTES)
        except EmailConfigError as exc:
            # Kredensial Gmail belum diset di .env — ini error konfigurasi
            # server, bukan kesalahan user, jadi dilaporkan apa adanya.
            return jsonify(success=False, message=str(exc)), 500
        except Exception:
            return jsonify(
                success=False,
                message='Gagal mengirim email. Coba lagi beberapa saat.'
            ), 502

    # Response sukses generik — lihat catatan keamanan di docstring.
    return jsonify(
        success=True,
        message='Jika email terdaftar, kode verifikasi telah dikirim.'
    )


@auth_bp.route('/forgot-password/verify-otp', methods=['POST'])
def verify_otp():
    """
    Body JSON: {"email": "...", "otp": "123456"}

    Cek apakah kode OTP benar, belum kedaluwarsa, dan belum melebihi
    batas percobaan salah. TIDAK menghapus baris OTP di sini — baris
    baru dihapus/ditandai terverifikasi penuh saat /reset berhasil,
    supaya kode yang sama masih valid jika user reload halaman di
    antara verify-otp dan submit password baru.
    """
    data  = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    otp_input = (data.get('otp') or '').strip()

    if not email or not otp_input:
        return jsonify(success=False, message='Email dan kode OTP wajib diisi.'), 400

    otp = (
        PasswordResetOTP.query
        .filter_by(email=email, is_verified=False)
        .order_by(PasswordResetOTP.created_at.desc())
        .first()
    )

    if not otp:
        return jsonify(success=False, message='Kode OTP tidak ditemukan. Minta kode baru.'), 404

    if otp.is_expired:
        return jsonify(success=False, message='Kode OTP sudah kedaluwarsa. Minta kode baru.'), 410

    if otp.is_locked:
        return jsonify(
            success=False,
            message='Terlalu banyak percobaan salah. Minta kode baru.'
        ), 429

    if otp.otp_code != otp_input:
        otp.attempts += 1
        db.session.commit()
        sisa = OTP_MAX_ATTEMPTS - otp.attempts
        return jsonify(
            success=False,
            message=f'Kode OTP salah. Sisa percobaan: {max(0, sisa)}.'
        ), 400

    # Kode benar — tandai sudah lolos verifikasi (belum dihapus,
    # supaya tetap valid sampai langkah reset password selesai).
    otp.is_verified = True
    db.session.commit()

    return jsonify(success=True, message='Kode OTP terverifikasi.')


@auth_bp.route('/forgot-password/reset', methods=['POST'])
def reset_password_with_otp():
    """
    Body JSON: {"email": "...", "otp": "123456", "new_password": "..."}

    Verifikasi ULANG kode OTP (defense in depth — tidak cukup percaya
    pada state verify-otp sebelumnya, karena request ini independen
    dan bisa saja dipanggil langsung tanpa lewat verify-otp dulu).
    Setelah valid: validasi kompleksitas password baru, hash, simpan,
    lalu hapus baris OTP supaya tidak bisa dipakai ulang.
    """
    data         = request.get_json(silent=True) or {}
    email        = (data.get('email') or '').strip().lower()
    otp_input    = (data.get('otp') or '').strip()
    new_password = data.get('new_password') or ''

    if not email or not otp_input or not new_password:
        return jsonify(success=False, message='Data tidak lengkap.'), 400

    otp = (
        PasswordResetOTP.query
        .filter_by(email=email, otp_code=otp_input, is_verified=True)
        .order_by(PasswordResetOTP.created_at.desc())
        .first()
    )

    if not otp:
        return jsonify(
            success=False,
            message='Verifikasi OTP tidak valid. Ulangi proses dari awal.'
        ), 400

    if otp.is_expired:
        return jsonify(success=False, message='Sesi reset sudah kedaluwarsa. Ulangi dari awal.'), 410

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify(success=False, message='Akun tidak ditemukan.'), 404

    complexity_error = _validate_password_complexity(new_password)
    if complexity_error:
        return jsonify(success=False, message=complexity_error), 400

    user.password = generate_password_hash(new_password)

    # Hapus baris OTP ini (dan baris OTP lain yang masih nyangkut untuk
    # email yang sama) supaya tidak ada kode lama yang bisa dipakai ulang.
    PasswordResetOTP.query.filter_by(email=email).delete()

    db.session.commit()

    return jsonify(success=True, message='Password berhasil diubah.')

# =========================
# LOGOUT
# =========================

@auth_bp.route('/logout')
def logout():

    session.clear()

    return redirect('/login')