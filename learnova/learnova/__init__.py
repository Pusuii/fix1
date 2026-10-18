"""Learnova - Learning Management System (Flask)."""
import hashlib
import logging
import os
import secrets

from flask import Flask, abort, g, redirect, render_template, request, send_from_directory, session, url_for

from . import auth, db, paths, settings, ui


def create_app(test_config=None):
    paths.apply_timezone()
    # Berkas statis ada di public/static: Vercel menyajikan public/** lewat CDN, Flask memakainya saat lokal.
    app = Flask(__name__, instance_path=paths.instance_path(),
                static_folder=os.path.join(paths.ROOT, "public", "static"),
                static_url_path="/static",
                template_folder=os.path.join(paths.ROOT, "templates"))
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.update(
        DATABASE=os.path.join(app.instance_path, "learnova.db"),
        UPLOAD_DIR=os.path.join(app.instance_path, "uploads"),
        MAX_CONTENT_LENGTH=60 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
        SEED_DEMO=os.environ.get("LEARNOVA_SEED_DEMO", "1") == "1",
        STARTED_AT=None,
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    # Kunci rahasia: dari env, atau (Vercel) diturunkan dari ID proyek agar sama di semua instance,
    # atau dibuat sekali lalu disimpan di instance/.
    key = os.environ.get("LEARNOVA_SECRET_KEY")
    if not key and paths.on_vercel():
        seed = os.environ.get("VERCEL_PROJECT_ID") or os.environ.get("VERCEL_GIT_REPO_ID")
        if seed:
            key = hashlib.sha256(("learnova|" + seed).encode()).hexdigest()
            logging.getLogger("learnova").warning(
                "LEARNOVA_SECRET_KEY belum diatur; memakai kunci turunan. Atur variabel ini di Vercel untuk produksi.")
    if not key:
        key_file = os.path.join(app.instance_path, "secret.key")
        try:
            if os.path.exists(key_file):
                with open(key_file) as fh:
                    key = fh.read().strip()
            if not key:
                key = secrets.token_hex(32)
                with open(key_file, "w") as fh:
                    fh.write(key)
        except OSError:
            key = key or secrets.token_hex(32)
    app.config["SECRET_KEY"] = key

    from datetime import datetime
    app.config["STARTED_AT"] = datetime.now()

    db.init_db(app)
    with app.app_context():
        from .seed import ensure_base, seed_demo, seed_minimal
        ensure_base()
        if db.db_is_empty(app):
            if app.config["SEED_DEMO"]:
                seed_demo()
            else:
                seed_minimal()

    app.teardown_appcontext(db.close_db)
    ui.register(app)

    def page_url(n):
        args = request.args.to_dict()
        args["page"] = n
        view_args = dict(request.view_args or {})
        return url_for(request.endpoint, **{**view_args, **args})
    app.jinja_env.globals["page_url"] = page_url

    @app.before_request
    def _before():
        auth.load_user()
        auth.check_csrf()
        # Mode pemeliharaan: hanya admin & super admin yang boleh masuk.
        if request.endpoint in (None, "static", "auth.login", "auth.logout", "public.landing"):
            return None
        if settings.get_bool("maintenance_mode"):
            user = g.user
            if user is None or user["role_name"] not in (auth.ROLE_ADMIN, auth.ROLE_SUPER):
                return render_template("errors/maintenance.html"), 503
        return None

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    from .blueprints import public, auth_bp, common, student, lecturer, admin, superadmin
    app.register_blueprint(public.bp)
    app.register_blueprint(auth_bp.bp)
    app.register_blueprint(common.bp)
    app.register_blueprint(student.bp, url_prefix="/mahasiswa")
    app.register_blueprint(lecturer.bp, url_prefix="/dosen")
    app.register_blueprint(admin.bp, url_prefix="/admin")
    app.register_blueprint(superadmin.bp, url_prefix="/super")

    @app.route("/berkas/<path:filename>")
    @auth.login_required
    def download(filename):
        """Unduh berkas dengan pengecekan akses."""
        from .files import uploads_dir
        from .db import q
        user = g.user
        role = user["role_name"]
        allowed = role in (auth.ROLE_ADMIN, auth.ROLE_SUPER)
        if not allowed:
            mat = q("SELECT c.class_id, c.lecturer_id FROM materials m JOIN classes c ON c.class_id = m.class_id "
                    "WHERE m.file_url = ?", (filename,), one=True)
            sub = q("SELECT s.student_id, c.lecturer_id FROM submissions s "
                    "JOIN assignments a ON a.assignment_id = s.assignment_id "
                    "JOIN classes c ON c.class_id = a.class_id WHERE s.file_url = ?", (filename,), one=True)
            if mat:
                if role == auth.ROLE_DOSEN:
                    allowed = mat["lecturer_id"] == user["user_id"]
                else:
                    allowed = q("SELECT 1 FROM enrollments WHERE class_id = ? AND student_id = ? AND status='aktif'",
                                (mat["class_id"], user["user_id"]), one=True) is not None
            elif sub:
                allowed = sub["student_id"] == user["user_id"] or (
                    role == auth.ROLE_DOSEN and sub["lecturer_id"] == user["user_id"])
            else:
                abort(404)
        if not allowed:
            abort(403)
        return send_from_directory(uploads_dir(), filename, as_attachment=request.args.get("dl") == "1")

    @app.errorhandler(403)
    def _403(e):
        return render_template("errors/error.html", code=403, title="Akses ditolak",
                               message="Akun Anda tidak memiliki izin untuk membuka halaman ini."), 403

    @app.errorhandler(404)
    def _404(e):
        return render_template("errors/error.html", code=404, title="Halaman tidak ditemukan",
                               message="Alamat yang Anda buka tidak ada atau sudah dipindahkan."), 404

    @app.errorhandler(400)
    def _400(e):
        return render_template("errors/error.html", code=400, title="Permintaan tidak valid",
                               message=getattr(e, "description", "Permintaan tidak dapat diproses.")), 400

    @app.errorhandler(413)
    def _413(e):
        return render_template("errors/error.html", code=413, title="Berkas terlalu besar",
                               message="Ukuran unggahan melebihi batas yang diizinkan."), 413

    @app.errorhandler(500)
    def _500(e):
        return render_template("errors/error.html", code=500, title="Terjadi kesalahan",
                               message="Ada masalah di server. Coba lagi sebentar lagi."), 500

    return app
