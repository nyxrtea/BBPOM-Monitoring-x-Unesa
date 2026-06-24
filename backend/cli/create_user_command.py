"""
backend/cli/create_user_command.py
─────────────────────────────────────────────
Custom Flask CLI command untuk membuat akun user secara manual oleh
admin — satu-satunya cara membuat akun baru sejak registrasi publik
(/register) ditutup (lihat auth_routes.py).

Cara pakai (folder project, venv aktif):

    flask create-user

Lalu isi prompt interaktif: nama depan, nama belakang, username,
email, dan password. Password tetap divalidasi kompleksitasnya
(panjang, huruf besar/kecil, angka, simbol) — sama seperti validasi
yang dulu dipakai di form registrasi publik.

Bisa juga dipakai non-interaktif dengan flag:

    flask create-user --first-name Andra --last-name Erlangga ^
        --username andra --email andra@pom.go.id --password "Rahasia123!"

(di Windows PowerShell pakai backtick ` di akhir baris untuk multi-line,
bukan ^ — atau tulis dalam satu baris saja)
"""

import click
from flask.cli import with_appcontext

from backend.database.db import db
from backend.models.user_model import User
from backend.routes.auth_routes import _create_user


@click.command('create-user')
@click.option('--first-name', prompt='Nama depan')
@click.option('--last-name',  prompt='Nama belakang')
@click.option('--username',   prompt='Username')
@click.option('--email',      prompt='Email')
@click.option('--password',   prompt='Password', hide_input=True,
              confirmation_prompt=True)
@with_appcontext
def create_user_command(first_name, last_name, username, email, password):
    """Buat akun user baru (pengganti registrasi publik yang sudah ditutup)."""
    success, message = _create_user(
        first_name=first_name,
        last_name=last_name,
        username=username,
        email=email,
        password=password,
        confirm_password=password,  # sudah dikonfirmasi lewat confirmation_prompt
    )

    if success:
        click.secho(f"✓ {message}: {username} ({email})", fg='green')
    else:
        click.secho(f"✗ Gagal: {message}", fg='red')
