"""Unggah & akses berkas."""
import os
import re
import secrets

from flask import current_app
from werkzeug.utils import secure_filename

from . import settings

ALLOWED_EXT = {
    "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "csv", "txt", "md", "zip", "rar", "7z",
    "png", "jpg", "jpeg", "gif", "webp", "mp4", "webm", "mp3", "py", "js", "java", "ipynb", "sql",
}


class UploadError(Exception):
    pass


def uploads_dir():
    return current_app.config["UPLOAD_DIR"]


def is_external(url):
    return bool(url) and re.match(r"^https?://", url, re.I) is not None


def display_name(file_url):
    """Nama berkas asli (tanpa awalan acak)."""
    if not file_url:
        return ""
    if is_external(file_url):
        return file_url
    base = os.path.basename(file_url)
    return re.sub(r"^[0-9a-f]{8}_", "", base)


def save_upload(storage, subdir):
    """Simpan FileStorage ke uploads/<subdir>/. Kembalikan path relatif."""
    if storage is None or not storage.filename:
        raise UploadError("Pilih berkas terlebih dahulu.")
    name = secure_filename(storage.filename)
    if not name or "." not in name:
        raise UploadError("Nama berkas tidak valid.")
    ext = name.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_EXT:
        raise UploadError("Jenis berkas .%s tidak diizinkan." % ext)
    limit = settings.get_int("max_upload_mb") * 1024 * 1024
    storage.stream.seek(0, os.SEEK_END)
    size = storage.stream.tell()
    storage.stream.seek(0)
    if size > limit:
        raise UploadError("Ukuran berkas melebihi batas %d MB." % settings.get_int("max_upload_mb"))
    if size == 0:
        raise UploadError("Berkas kosong.")
    folder = os.path.join(uploads_dir(), subdir)
    os.makedirs(folder, exist_ok=True)
    stored = "%s_%s" % (secrets.token_hex(4), name)
    storage.save(os.path.join(folder, stored))
    return "%s/%s" % (subdir, stored)


def delete_file(file_url):
    if not file_url or is_external(file_url):
        return
    path = os.path.normpath(os.path.join(uploads_dir(), file_url))
    if path.startswith(os.path.normpath(uploads_dir())) and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass
