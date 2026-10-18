"""Filter, helper template, dan definisi navigasi."""
import json
import os
from datetime import datetime

from flask import g, request, url_for
from markupsafe import Markup, escape

from . import auth, settings
from .auth import ROLE_ADMIN, ROLE_DOSEN, ROLE_MAHASISWA, ROLE_SUPER, has_perm
from .files import display_name, is_external

_ICON_PATH = os.path.join(os.path.dirname(__file__), "icons.json")
with open(_ICON_PATH, encoding="utf-8") as _fh:
    ICONS = json.load(_fh)

MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
DAYS = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


def icon(name, size=18, cls=""):
    inner = ICONS.get(name, ICONS["circle-dot"])
    return Markup(
        '<svg class="ic %s" width="%d" height="%d" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">%s</svg>'
        % (escape(cls), size, size, inner)
    )


def parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def normalize_dt(value):
    """Input datetime-local -> 'YYYY-MM-DD HH:MM:SS' (atau None bila tidak valid)."""
    d = parse_dt((value or "").strip())
    return d.strftime("%Y-%m-%d %H:%M:%S") if d else None


def f_dt(value, with_time=True):
    d = parse_dt(value)
    if not d:
        return "-"
    out = "%d %s %d" % (d.day, MONTHS[d.month - 1], d.year)
    if with_time:
        out += ", %02d.%02d" % (d.hour, d.minute)
    return out


def f_date(value):
    return f_dt(value, with_time=False)


def f_day(value):
    d = parse_dt(value)
    if not d:
        return "-"
    return "%s, %d %s %d" % (DAYS[d.weekday()], d.day, MONTHS[d.month - 1], d.year)


def f_input(value):
    d = parse_dt(value)
    return d.strftime("%Y-%m-%dT%H:%M") if d else ""


def f_rel(value):
    """'3 hari lagi', '2 jam lalu', dst."""
    d = parse_dt(value)
    if not d:
        return "-"
    delta = (d - datetime.now()).total_seconds()
    future = delta > 0
    s = abs(delta)
    if s < 60:
        txt = "beberapa detik"
    elif s < 3600:
        txt = "%d menit" % (s // 60)
    elif s < 86400:
        txt = "%d jam" % (s // 3600)
    elif s < 86400 * 30:
        txt = "%d hari" % (s // 86400)
    else:
        txt = "%d bulan" % (s // (86400 * 30))
    return txt + (" lagi" if future else " lalu")


def f_num(value, digits=1):
    if value is None or value == "":
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v == int(v):
        return str(int(v))
    return ("%." + str(digits) + "f") % v


def f_initials(name):
    parts = [p for p in (name or "?").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def is_past(value):
    d = parse_dt(value)
    return bool(d and d < datetime.now())


STATUS = {
    # akun
    "aktif": ("Aktif", "ok"),
    "pending": ("Menunggu verifikasi", "warn"),
    "nonaktif": ("Nonaktif", "mute"),
    "ditolak": ("Ditolak", "bad"),
    "selesai": ("Selesai", "info"),
    # laporan
    "baru": ("Baru", "warn"),
    "diproses": ("Diproses", "info"),
    # tugas
    "dikumpulkan": ("Dikumpulkan", "info"),
    "dinilai": ("Dinilai", "ok"),
    "terlambat": ("Terlambat", "bad"),
    "belum": ("Belum dikumpulkan", "warn"),
    # absensi
    "hadir": ("Hadir", "ok"),
    "izin": ("Izin", "info"),
    "sakit": ("Sakit", "warn"),
    "alpa": ("Alpa", "bad"),
    "terbuka": ("Absensi dibuka", "ok"),
    "tertutup": ("Absensi ditutup", "mute"),
    "menunggu": ("Menunggu penilaian", "warn"),
}


def status_info(key):
    return STATUS.get(key, (str(key).replace("_", " ").title(), "mute"))


def badge(key, label=None):
    text, tone = status_info(key)
    return Markup('<span class="badge badge-%s">%s</span>' % (tone, escape(label or text)))


# ----------------------------------------------------------------- navigasi


def build_nav():
    """Menu samping sesuai role + hak akses yang aktif."""
    user = g.get("user")
    if not user:
        return []
    role = user["role_name"]
    groups = []

    def item(label, endpoint, ic, perm=None, prefix=None):
        if perm and not has_perm(perm):
            return None
        url = url_for(endpoint)
        return {"label": label, "url": url, "icon": ic, "prefix": prefix or url}

    def group(title, items):
        items = [i for i in items if i]
        if items:
            groups.append({"title": title, "items": items})

    if role == ROLE_MAHASISWA:
        group("Belajar", [
            item("Beranda", "student.dashboard", "dashboard"),
            item("Kelas saya", "student.classes", "book", "learn.access", "/mahasiswa/kelas"),
        ])
    elif role == ROLE_DOSEN:
        group("Mengajar", [
            item("Beranda", "lecturer.dashboard", "dashboard"),
            item("Kelas saya", "lecturer.classes", "book", "class.manage", "/dosen/kelas"),
        ])
    elif role == ROLE_ADMIN:
        group("Administrasi", [
            item("Beranda", "admin.dashboard", "dashboard"),
            item("Akun pengguna", "admin.accounts", "users", "account.manage", "/admin/akun"),
            item("Mata kuliah", "admin.courses", "books", "course.manage", "/admin/mata-kuliah"),
            item("Kelas", "admin.classes", "book", "course.manage", "/admin/kelas"),
            item("Laporan kendala", "admin.reports", "flag", "report.handle", "/admin/laporan"),
            item("Aktivitas platform", "admin.activity", "activity", "platform.monitor", "/admin/aktivitas"),
        ])
    elif role == ROLE_SUPER:
        group("Sistem", [
            item("Beranda", "superadmin.dashboard", "dashboard"),
            item("Akun admin", "superadmin.admins", "user-cog", "admin.manage", "/super/akun-admin"),
            item("Role & hak akses", "superadmin.permissions", "key", "role.manage", "/super/hak-akses"),
            item("Konfigurasi", "superadmin.settings_page", "settings", "settings.manage", "/super/konfigurasi"),
            item("Statistik", "superadmin.stats", "chart", "stats.view", "/super/statistik"),
            item("Audit log", "superadmin.audit_log", "scroll", "audit.view", "/super/audit"),
        ])
    group("Akun", [
        item("Profil", "common.profile", "user", None, "/profil"),
        item("Bantuan & laporan", "common.help", "help", None, "/bantuan"),
    ])
    return groups


def is_active_nav(prefix, home_urls):
    path = request.path
    if prefix in home_urls:
        return path == prefix
    return path.startswith(prefix)


def register(app):
    app.jinja_env.filters.update({
        "dt": f_dt, "dateid": f_date, "dayid": f_day, "dtinput": f_input, "rel": f_rel,
        "num": f_num, "initials": f_initials, "filename": display_name,
        "zip": lambda a, b: list(zip(a, b)),
    })
    app.jinja_env.globals.update({
        "icon": icon, "badge": badge, "status_info": status_info, "is_past": is_past,
        "is_external": is_external, "role_labels": auth.ROLE_LABELS, "now_dt": datetime.now,
        "csrf_input": auth.csrf_input, "csrf_token": auth.csrf_token,
    })

    @app.context_processor
    def inject():
        cfg = settings.all_settings()
        home_urls = set()
        nav = build_nav()
        for grp in nav:
            for it in grp["items"]:
                if it["label"] == "Beranda":
                    home_urls.add(it["url"])
        return {
            "cfg": cfg,
            "app_name": cfg.get("app_name", "Learnova"),
            "csrf_input": auth.csrf_input,
            "csrf_token": auth.csrf_token,
            "has_perm": has_perm,
            "nav": nav,
            "nav_active": lambda prefix: is_active_nav(prefix, home_urls),
            "me": g.get("user"),
        }
