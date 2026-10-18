"""Pencatatan audit log."""
from flask import g, has_request_context

from .db import now, run

# Label tampilan untuk kode aksi.
ACTION_LABELS = {
    "auth.login": "Login berhasil",
    "auth.login_failed": "Login gagal",
    "auth.logout": "Logout",
    "auth.register": "Pendaftaran akun",
    "account.create": "Membuat akun",
    "account.update": "Mengubah akun",
    "account.status": "Mengubah status akun",
    "account.verify": "Memverifikasi akun",
    "account.reject": "Menolak akun",
    "account.password": "Mengganti password",
    "course.create": "Membuat mata kuliah",
    "course.update": "Mengubah mata kuliah",
    "course.delete": "Menghapus mata kuliah",
    "class.create": "Membuat kelas",
    "class.update": "Mengubah kelas",
    "class.enroll": "Menambah peserta kelas",
    "class.unenroll": "Mengeluarkan peserta kelas",
    "material.create": "Menambah materi",
    "material.update": "Mengubah materi",
    "material.delete": "Menghapus materi",
    "assignment.create": "Membuat tugas",
    "assignment.update": "Mengubah tugas",
    "assignment.delete": "Menghapus tugas",
    "assignment.grade": "Menilai tugas",
    "quiz.create": "Membuat kuis",
    "quiz.update": "Mengubah kuis",
    "quiz.delete": "Menghapus kuis",
    "quiz.grade": "Menilai jawaban esai",
    "attendance.session": "Membuat sesi perkuliahan",
    "attendance.toggle": "Membuka/menutup absensi",
    "report.create": "Mengirim laporan kendala",
    "report.update": "Menangani laporan kendala",
    "role.permissions": "Mengubah hak akses role",
    "settings.update": "Mengubah konfigurasi sistem",
}


def label(action):
    return ACTION_LABELS.get(action, action)


def log(action, target_type=None, target_id=None, details=None, actor_id=None):
    if actor_id is None and has_request_context():
        user = getattr(g, "user", None)
        actor_id = user["user_id"] if user else None
    run(
        "INSERT INTO audit_logs (actor_user_id, action, target_type, target_id, details, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (actor_id, action, target_type, target_id, details, now()),
    )
