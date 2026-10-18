import re

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for

from .. import audit, auth, settings
from ..db import now, q, run

bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _safe_next(target):
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return None


@bp.route("/dashboard")
@auth.login_required
def dashboard():
    return redirect(url_for(auth.ROLE_HOME[g.user["role_name"]]))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("auth.dashboard"))
    error = None
    email = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        key = "%s|%s" % (email, request.remote_addr)
        wait = auth.throttled(key)
        if wait:
            error = "Terlalu banyak percobaan gagal. Coba lagi dalam %d menit." % max(1, (wait + 59) // 60)
        else:
            user = q("SELECT u.*, r.role_name FROM users u JOIN roles r ON r.role_id = u.role_id WHERE u.email = ?",
                     (email,), one=True)
            if user is None or not auth.verify_password(user["password_hash"], password):
                auth.register_fail(key)
                error = "Email atau password salah."
                audit.log("auth.login_failed", "user", user["user_id"] if user else None,
                          "Email: %s" % email[:80], actor_id=user["user_id"] if user else None)
            elif user["account_status"] == "pending":
                error = "Akun Anda masih menunggu verifikasi admin. Anda akan bisa masuk setelah akun diaktifkan."
            elif user["account_status"] == "ditolak":
                error = "Pendaftaran akun ini ditolak. Hubungi admin untuk informasi lebih lanjut."
            elif user["account_status"] != "aktif":
                error = "Akun ini nonaktif. Hubungi admin untuk mengaktifkannya kembali."
            elif settings.get_bool("maintenance_mode") and user["role_name"] not in (auth.ROLE_ADMIN, auth.ROLE_SUPER):
                error = "Platform sedang dalam pemeliharaan. Coba lagi beberapa saat lagi."
            else:
                auth.clear_fails(key)
                session.clear()
                session["uid"] = user["user_id"]
                session.permanent = True
                audit.log("auth.login", "user", user["user_id"], actor_id=user["user_id"])
                return redirect(_safe_next(request.args.get("next")) or url_for(auth.ROLE_HOME[user["role_name"]]))
    show_demo = bool(current_app.config.get("SEED_DEMO"))
    return render_template("public/login.html", error=error, email=email, show_demo=show_demo), (401 if error else 200)


@bp.route("/logout", methods=["POST"])
def logout():
    if g.user:
        audit.log("auth.logout", "user", g.user["user_id"])
    session.clear()
    flash("Anda sudah keluar.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/daftar", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("auth.dashboard"))
    if not settings.get_bool("registration_open"):
        return render_template("public/register_closed.html")
    errors, form = {}, {}
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in ("full_name", "email", "role")}
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        form["email"] = form["email"].lower()
        if len(form["full_name"]) < 3:
            errors["full_name"] = "Nama lengkap minimal 3 karakter."
        if not EMAIL_RE.match(form["email"]):
            errors["email"] = "Format email tidak valid."
        elif q("SELECT 1 FROM users WHERE email = ?", (form["email"],), one=True):
            errors["email"] = "Email ini sudah terdaftar."
        if form["role"] not in (auth.ROLE_MAHASISWA, auth.ROLE_DOSEN):
            errors["role"] = "Pilih peran Anda."
        problem = auth.password_problem(password)
        if problem:
            errors["password"] = problem
        elif password != confirm:
            errors["confirm"] = "Konfirmasi password tidak sama."
        if not errors:
            need = settings.get_bool("require_verification")
            rid = q("SELECT role_id FROM roles WHERE role_name = ?", (form["role"],), one=True)["role_id"]
            uid = run(
                "INSERT INTO users (role_id, full_name, email, password_hash, account_status, created_at) VALUES (?,?,?,?,?,?)",
                (rid, form["full_name"], form["email"], auth.hash_password(password),
                 "pending" if need else "aktif", now()))
            audit.log("auth.register", "user", uid, "Peran: %s" % form["role"], actor_id=uid)
            if need:
                flash("Pendaftaran terkirim. Admin akan memverifikasi akun Anda sebelum bisa digunakan.", "success")
            else:
                flash("Akun berhasil dibuat. Silakan masuk.", "success")
            return redirect(url_for("auth.login"))
    return render_template("public/register.html", errors=errors, form=form)
