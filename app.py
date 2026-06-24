# =========================
# app.py
# =========================

import os

from flask import Flask
from flask import redirect
from flask import session

from flask_migrate import Migrate

# ── Load .env SEBELUM apapun yang membaca os.environ ──────────
# Pakai python-dotenv jika terinstall; jika belum, fallback ke
# parser .env manual sederhana supaya app tetap bisa jalan tanpa
# perlu install library tambahan.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    _env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(_env_path):
        with open(_env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip())

from backend.database.db import db
from backend.models.user_model              import User
from backend.models.distribution_model_main import Distribution
from backend.models.upload_session_model_main    import UploadSession
from backend.models.jenis_sarana_instansi_model  import JenisSaranaInstansi
from backend.models.data_match_model_alamat      import DataMatchModelAlamat
from backend.models.bayesian_cache_model         import BayesianCache
from backend.models.risk_cache_model             import RiskCache
from backend.models.forecast_cache_model         import ForecastCache
from backend.models.password_reset_otp_model     import PasswordResetOTP  # ← BARU: OTP lupa password

from backend.routes.auth_routes      import auth_bp
from backend.routes.dashboard_routes import dashboard_bp
from backend.routes.upload_routes    import upload_bp
from backend.routes.analytics_routes import analytics_bp
from backend.routes.anomaly_route    import anomaly_bp
from backend.routes.bayesian_routes  import bayesian_bp
from backend.routes.map_routes       import map_bp
from backend.ml.forecasting.forecasting_route import forecast_bp
from backend.ml.risk.risk_route               import risk_bp

app = Flask(__name__)

# =========================
# CONFIG
# =========================

# PERBAIKAN KEAMANAN: SECRET_KEY dan kredensial database SEBELUMNYA
# hardcoded langsung di source code ('SECRET123', password DB plain
# di connection string). Kalau file ini pernah masuk git history,
# kredensial itu sudah "bocor" permanen. Sekarang dibaca dari .env —
# isi nilai sebenarnya di file .env (lihat .env.example), JANGAN
# commit .env ke git.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-only-fallback-change-me')

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'postgresql://postgres:postgres@localhost/bpom_db'  # fallback dev lokal
)

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Batas ukuran upload — mencegah file raksasa membebani server
# tanpa peringatan sama sekali.
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024   # 50 MB

# Nonaktifkan autoflush agar bulk_save_objects tidak konflik
# dengan session state yang belum commit
app.config['SQLALCHEMY_COMMIT_ON_TEARDOWN'] = False

# =========================
# INIT DATABASE
# =========================

db.init_app(app)

migrate = Migrate(app, db)

# =========================
# REGISTER BLUEPRINT
# =========================

app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(upload_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(anomaly_bp)
app.register_blueprint(bayesian_bp)
app.register_blueprint(map_bp)
app.register_blueprint(forecast_bp)
app.register_blueprint(risk_bp)


# =========================
# CLI COMMANDS
# =========================
# 'flask create-user' — satu-satunya cara membuat akun baru sejak
# registrasi publik (/register) ditutup demi keamanan data distribusi
# obat. Lihat backend/cli/create_user_command.py.
from backend.cli.create_user_command import create_user_command
app.cli.add_command(create_user_command)


# =========================
# HOME
# =========================

@app.route('/')
def home():

    if 'user_id' in session:

        return redirect('/dashboard')

    return redirect('/login')

# =========================
# RUN
# =========================

if __name__ == '__main__':
    app.run(debug=True)