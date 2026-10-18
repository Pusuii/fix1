"""Validasi & penyimpanan akun (dipakai Admin dan Super Admin)."""
import re

from . import auth
from .db import now, q, run

STATUSES = [("aktif", "Aktif"), ("pending", "Menunggu verifikasi"), ("nonaktif", "Nonaktif"), ("ditolak", "Ditolak")]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate(form, user=None, roles=(), password_required=True):
    """Kembalikan (data_bersih, error). roles: nama role yang boleh dipilih."""
    clean = {k: (form.get(k) or "").strip() for k in ("full_name", "email", "role", "status")}
    clean["email"] = clean["email"].lower()
    clean["password"] = form.get("password") or ""
    errors = {}
    if len(clean["full_name"]) < 3:
        errors["full_name"] = "Nama lengkap minimal 3 karakter."
    if not EMAIL_RE.match(clean["email"]):
        errors["email"] = "Format email tidak valid."
    else:
        dup = q("SELECT user_id FROM users WHERE email = ? AND user_id != ?", (clean["email"], user["user_id"] if user else 0), one=True)
        if dup:
            errors["email"] = "Email ini sudah dipakai akun lain."
    if clean["role"] not in roles:
        errors["role"] = "Pilih peran yang valid."
    if clean["status"] not in [s[0] for s in STATUSES]:
        errors["status"] = "Pilih status akun."
    if clean["password"] or password_required:
        problem = auth.password_problem(clean["password"])
        if problem:
            errors["password"] = problem
    return clean, errors


def create(clean):
    rid = q("SELECT role_id FROM roles WHERE role_name = ?", (clean["role"],), one=True)["role_id"]
    return run(
        "INSERT INTO users (role_id, full_name, email, password_hash, account_status, created_at) VALUES (?,?,?,?,?,?)",
        (rid, clean["full_name"], clean["email"], auth.hash_password(clean["password"]), clean["status"], now()))


def update(user, clean):
    rid = q("SELECT role_id FROM roles WHERE role_name = ?", (clean["role"],), one=True)["role_id"]
    run("UPDATE users SET role_id = ?, full_name = ?, email = ?, account_status = ? WHERE user_id = ?",
        (rid, clean["full_name"], clean["email"], clean["status"], user["user_id"]))
    if clean["password"]:
        run("UPDATE users SET password_hash = ? WHERE user_id = ?", (auth.hash_password(clean["password"]), user["user_id"]))
