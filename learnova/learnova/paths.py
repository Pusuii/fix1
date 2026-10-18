"""Lokasi penyimpanan data dan lingkungan penyebaran (lokal vs Vercel).

Di Vercel, folder proyek bersifat read-only dan hanya /tmp yang dapat ditulis. Data di /tmp bersifat
sementara (hilang saat instance baru dibuat), sehingga mode ini cocok untuk demo, bukan produksi.
"""
import os
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def on_vercel():
    return bool(os.environ.get("VERCEL"))


def _writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write-test")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def instance_path():
    """Folder data: LEARNOVA_INSTANCE_PATH > ./instance (jika bisa ditulis) > folder sementara sistem."""
    explicit = os.environ.get("LEARNOVA_INSTANCE_PATH")
    if explicit:
        return explicit
    default = os.path.join(ROOT, "instance")
    if not on_vercel() and _writable(default):
        return default
    return os.path.join(tempfile.gettempdir(), "learnova")


def apply_timezone():
    """Atur zona waktu proses. Di Vercel server berzona UTC, jadi default ke Asia/Jakarta (WIB).

    Bisa diubah lewat LEARNOVA_TZ (mis. Asia/Makassar). Di komputer lokal zona waktu sistem dipakai apa adanya.
    """
    tz = os.environ.get("LEARNOVA_TZ") or ("Asia/Jakarta" if on_vercel() else None)
    if tz and hasattr(time, "tzset"):
        os.environ["TZ"] = tz
        time.tzset()
