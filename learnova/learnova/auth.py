"""Autentikasi, role, permission, dan CSRF."""
import secrets
import time
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

from .db import q

ROLE_MAHASISWA = "mahasiswa"
ROLE_DOSEN = "dosen"
ROLE_ADMIN = "admin"
ROLE_SUPER = "super_admin"

ROLE_LABELS = {
    ROLE_MAHASISWA: "Mahasiswa",
    ROLE_DOSEN: "Dosen",
    ROLE_ADMIN: "Admin",
    ROLE_SUPER: "Super Admin",
}

ROLE_HOME = {
    ROLE_MAHASISWA: "student.dashboard",
    ROLE_DOSEN: "lecturer.dashboard",
    ROLE_ADMIN: "admin.dashboard",
    ROLE_SUPER: "superadmin.dashboard",
}

# (nama, deskripsi, grup, role bawaan)
PERMISSIONS = [
    ("learn.access", "Mengakses kelas, materi, tugas, kuis, absensi, dan nilai", "Pembelajaran Mahasiswa", [ROLE_MAHASISWA]),
    ("class.manage", "Membuat dan mengelola kelas milik sendiri", "Pengelolaan Pembelajaran", [ROLE_DOSEN]),
    ("material.manage", "Mengunggah dan mengatur materi", "Pengelolaan Pembelajaran", [ROLE_DOSEN]),
    ("assignment.manage", "Membuat tugas dan memeriksa pengumpulan", "Pengelolaan Pembelajaran", [ROLE_DOSEN]),
    ("quiz.manage", "Membuat kuis dan menilai jawaban esai", "Pengelolaan Pembelajaran", [ROLE_DOSEN]),
    ("attendance.manage", "Mengelola sesi dan absensi", "Pengelolaan Pembelajaran", [ROLE_DOSEN]),
    ("progress.view", "Memantau progres dan nilai mahasiswa", "Pengelolaan Pembelajaran", [ROLE_DOSEN]),
    ("account.manage", "Mengelola akun mahasiswa dan dosen", "Administrasi", [ROLE_ADMIN]),
    ("account.verify", "Memverifikasi atau mengaktifkan akun", "Administrasi", [ROLE_ADMIN]),
    ("course.manage", "Mengelola data kelas dan mata kuliah", "Administrasi", [ROLE_ADMIN]),
    ("report.handle", "Menangani laporan dan kendala pengguna", "Administrasi", [ROLE_ADMIN]),
    ("platform.monitor", "Memantau aktivitas dan kondisi platform", "Administrasi", [ROLE_ADMIN]),
    ("admin.manage", "Mengelola akun admin", "Administrasi Sistem", []),
    ("role.manage", "Mengatur role dan hak akses", "Administrasi Sistem", []),
    ("settings.manage", "Mengelola konfigurasi sistem", "Administrasi Sistem", []),
    ("stats.view", "Melihat statistik dan aktivitas keseluruhan", "Administrasi Sistem", []),
    ("audit.view", "Melihat audit log", "Administrasi Sistem", []),
]

# ---------------------------------------------------------------- password


def hash_password(password):
    return generate_password_hash(password, method="pbkdf2:sha256")


def verify_password(hashed, password):
    return check_password_hash(hashed, password)


def password_problem(password):
    if len(password) < 8:
        return "Password minimal 8 karakter."
    if password.isdigit() or password.isalpha():
        return "Password harus berisi kombinasi huruf dan angka."
    return None


# ---------------------------------------------------------------- throttle
_FAILS = {}
MAX_FAILS = 5
LOCK_SECONDS = 300


def throttled(key):
    rec = _FAILS.get(key)
    if not rec:
        return 0
    count, first = rec
    if time.time() - first > LOCK_SECONDS:
        _FAILS.pop(key, None)
        return 0
    if count >= MAX_FAILS:
        return int(LOCK_SECONDS - (time.time() - first))
    return 0


def register_fail(key):
    rec = _FAILS.get(key)
    if not rec or time.time() - rec[1] > LOCK_SECONDS:
        _FAILS[key] = (1, time.time())
    else:
        _FAILS[key] = (rec[0] + 1, rec[1])


def clear_fails(key):
    _FAILS.pop(key, None)


# ---------------------------------------------------------------- current user


def load_user():
    g.user = None
    uid = session.get("uid")
    if uid:
        row = q(
            "SELECT u.*, r.role_name FROM users u JOIN roles r ON r.role_id = u.role_id WHERE u.user_id = ?",
            (uid,), one=True,
        )
        if row and row["account_status"] == "aktif":
            g.user = row
        else:
            session.pop("uid", None)


def user_perms():
    """Set permission milik user saat ini (di-cache per request, bukan per app context)."""
    cached = request.environ.get("learnova.perms")
    if cached is None:
        if g.get("user") is None:
            cached = set()
        else:
            rows = q(
                "SELECT p.permission_name FROM role_permissions rp "
                "JOIN permissions p ON p.permission_id = rp.permission_id WHERE rp.role_id = ?",
                (g.user["role_id"],),
            )
            cached = {r["permission_name"] for r in rows}
        request.environ["learnova.perms"] = cached
    return cached


def has_perm(name):
    if g.get("user") is None:
        return False
    if g.user["role_name"] == ROLE_SUPER:
        return True
    return name in user_perms()


# ---------------------------------------------------------------- decorators


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            flash("Silakan masuk terlebih dahulu.", "info")
            return redirect(url_for("auth.login", next=request.path))
        return view(*a, **kw)
    return wrapped


def role_required(*roles):
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if g.user is None:
                flash("Silakan masuk terlebih dahulu.", "info")
                return redirect(url_for("auth.login", next=request.path))
            if g.user["role_name"] not in roles:
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


def perm_required(*perms):
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if g.user is None:
                return redirect(url_for("auth.login", next=request.path))
            for p in perms:
                if not has_perm(p):
                    abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


# ---------------------------------------------------------------- CSRF


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(24)
    return session["_csrf"]


def csrf_input():
    return Markup('<input type="hidden" name="_csrf" value="%s">' % csrf_token())


def check_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, description="Token keamanan tidak valid atau sesi sudah berakhir. Muat ulang halaman lalu coba lagi.")
