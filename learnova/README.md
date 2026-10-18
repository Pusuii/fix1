# Learnova — Learning Management System

Platform LMS dengan empat peran (Mahasiswa, Dosen, Admin, Super Admin) yang mengikuti ERD (21 tabel),
activity diagram tiap role, dan use-case diagram yang Anda berikan.

## Menjalankan (VS Code)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Buka http://127.0.0.1:5000. Database SQLite dan data demo dibuat otomatis di folder `instance/` pada
jalankan pertama. Untuk mengulang dari data demo bersih: `python app.py --reset`.

Variabel lingkungan opsional: `HOST`, `PORT`, `FLASK_DEBUG=1`.

## Akun demo (password semua: `Learnova123`)

| Peran | Email |
|---|---|
| Super Admin | superadmin@learnova.id |
| Admin | admin@learnova.id, admin2@learnova.id |
| Dosen | dosen@learnova.id, dosen2@learnova.id, dosen3@learnova.id (menunggu verifikasi) |
| Mahasiswa | mahasiswa@learnova.id … mahasiswa10@learnova.id; mahasiswa11@ (menunggu verifikasi); mahasiswa12@ (nonaktif) |

## Deploy ke Vercel

Project ini siap di-deploy tanpa `vercel.json` (Flask zero-config: `app.py` mengekspos variabel `app`).

```bash
npm i -g vercel
vercel          # preview
vercel --prod   # produksi
```

Atau hubungkan repositori Git ke Vercel (Framework Preset: Flask / Other, tanpa Build Command).

Variabel lingkungan yang disarankan (Project Settings → Environment Variables):

| Variabel | Fungsi |
|---|---|
| `LEARNOVA_SECRET_KEY` | **Wajib untuk produksi.** String acak panjang untuk menandatangani sesi (mis. `python -c "import secrets;print(secrets.token_hex(32))"`). Jika kosong, kunci diturunkan dari ID proyek Vercel. |
| `LEARNOVA_SEED_DEMO` | `1` (default) isi data demo; `0` hanya buat satu akun super admin. |
| `LEARNOVA_TZ` | Zona waktu, default `Asia/Jakarta` di Vercel. |

**Keterbatasan penting (mode demo):** di Vercel hanya `/tmp` yang dapat ditulis dan isinya sementara. Database SQLite
dan berkas unggahan dibuat ulang (data demo) setiap instance baru dimulai, dan tiap instance punya salinan sendiri.
Cocok untuk demo/presentasi, **tidak untuk data nyata**. Untuk produksi, pindahkan database ke layanan terkelola
(mis. Postgres/Turso) dan unggahan ke penyimpanan objek (mis. Vercel Blob). Batas unggahan di Vercel 4 MB
(batas body request platform 4,5 MB). File statis ada di `public/static/`.

## Fitur utama

- **Mahasiswa**: kelas, materi (prasyarat berurutan), tugas (tenggat ditegakkan), kuis (timer, batas percobaan,
  pilihan ganda otomatis dinilai, esai menunggu dosen), absensi, progres & nilai, laporan kendala.
- **Dosen**: kelola kelas, materi, tugas & penilaian, kuis & penilaian esai, sesi absensi, laporan progres.
- **Admin**: verifikasi & kelola akun, mata kuliah, kelas & peserta, laporan kendala, monitoring aktivitas.
- **Super Admin**: akun admin, role & hak akses (17 permission), konfigurasi sistem, statistik, audit log.
- Keamanan: password pbkdf2, CSRF, batas percobaan login, whitelist & batas ukuran upload, unduhan berkas
  dengan kontrol akses, mode pemeliharaan.

## Struktur

```
app.py                 titik masuk (juga dikenali Vercel)
learnova/              paket aplikasi (blueprints/, schema.sql, seed.py, auth.py, services.py, ...)
templates/             tampilan (Jinja2)
public/static/         aset (CSS, JS, logo) - disajikan CDN di Vercel
tests/test_flows.py    uji alur: python -m unittest discover -s tests -v
```

## Catatan

- Dibangun dengan **Flask + SQLite (stdlib)** karena tidak memerlukan Node/npm dan mudah dijalankan di VS Code.
- Font (Bricolage Grotesque, Instrument Sans) dimuat dari Google Fonts dan butuh internet.
- Satu kolom tambahan di luar ERD: `user_reports.response` (tanggapan admin atas laporan).
