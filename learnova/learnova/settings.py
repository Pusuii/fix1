"""Konfigurasi sistem (tabel system_settings) beserta validasinya."""
from . import paths
from .db import now, q, run

# key -> definisi. type: text | int | bool
DEFINITIONS = [
    {"key": "app_name", "label": "Nama platform", "type": "text", "default": "Learnova",
     "help": "Tampil di navigasi dan judul halaman.", "max_len": 40},
    {"key": "institution", "label": "Nama institusi", "type": "text", "default": "Universitas Contoh Nusantara",
     "help": "Tampil di beranda dan footer.", "max_len": 80},
    {"key": "support_email", "label": "Email dukungan", "type": "text", "default": "bantuan@learnova.id",
     "help": "Alamat kontak yang ditampilkan kepada pengguna.", "max_len": 80},
    {"key": "registration_open", "label": "Pendaftaran akun dibuka", "type": "bool", "default": "1",
     "help": "Jika dimatikan, halaman daftar menampilkan pemberitahuan dan tidak menerima akun baru."},
    {"key": "require_verification", "label": "Akun baru wajib diverifikasi admin", "type": "bool", "default": "1",
     "help": "Jika dimatikan, akun mahasiswa/dosen baru langsung aktif."},
    {"key": "quiz_max_attempts", "label": "Batas percobaan kuis", "type": "int", "default": "2",
     "min": 1, "max": 10, "help": "Berapa kali mahasiswa boleh mengerjakan satu kuis."},
    {"key": "max_upload_mb", "label": "Ukuran maksimum unggahan (MB)", "type": "int",
     "default": "4" if paths.on_vercel() else "10",
     "min": 1, "max": 4 if paths.on_vercel() else 50,
     "help": "Berlaku untuk materi dan pengumpulan tugas."
             + (" Di Vercel dibatasi 4 MB (batas body request platform 4,5 MB)." if paths.on_vercel() else "")},
    {"key": "maintenance_mode", "label": "Mode pemeliharaan", "type": "bool", "default": "0",
     "help": "Jika aktif, hanya admin dan super admin yang dapat masuk."},
]
BY_KEY = {d["key"]: d for d in DEFINITIONS}


def ensure_defaults():
    for d in DEFINITIONS:
        run(
            "INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?,?,?)",
            (d["key"], d["default"], now()),
        )


def all_settings():
    values = {d["key"]: d["default"] for d in DEFINITIONS}
    for row in q("SELECT setting_key, setting_value FROM system_settings"):
        if row["setting_key"] in values:
            values[row["setting_key"]] = row["setting_value"]
    return values


def get(key):
    return all_settings().get(key, BY_KEY[key]["default"] if key in BY_KEY else None)


def get_int(key):
    try:
        return int(get(key))
    except (TypeError, ValueError):
        return int(BY_KEY[key]["default"])


def get_bool(key):
    return str(get(key)) == "1"


def validate(form):
    """Validasi input form. Kembalikan (nilai_bersih, daftar_error)."""
    clean, errors = {}, []
    for d in DEFINITIONS:
        key = d["key"]
        if d["type"] == "bool":
            clean[key] = "1" if form.get(key) in ("1", "on", "true") else "0"
        elif d["type"] == "int":
            raw = (form.get(key) or "").strip()
            try:
                val = int(raw)
            except ValueError:
                errors.append(f"{d['label']} harus berupa angka bulat.")
                continue
            if val < d["min"] or val > d["max"]:
                errors.append(f"{d['label']} harus antara {d['min']} dan {d['max']}.")
                continue
            clean[key] = str(val)
        else:
            raw = (form.get(key) or "").strip()
            if not raw:
                errors.append(f"{d['label']} tidak boleh kosong.")
                continue
            if len(raw) > d.get("max_len", 200):
                errors.append(f"{d['label']} maksimal {d.get('max_len', 200)} karakter.")
                continue
            if key == "support_email" and ("@" not in raw or " " in raw):
                errors.append("Email dukungan tidak valid.")
                continue
            clean[key] = raw
    return clean, errors
