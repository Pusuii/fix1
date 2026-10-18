"""Data dasar (role, permission, konfigurasi) dan data demo."""
import os
import random
from datetime import datetime, timedelta

from flask import current_app

from . import auth, settings
from .auth import ROLE_ADMIN, ROLE_DOSEN, ROLE_MAHASISWA, ROLE_SUPER, hash_password
from .db import get_db, q, run, scalar

DEMO_PASSWORD = "Learnova123"
ROLE_DEFS = [
    (ROLE_MAHASISWA, "Peserta pembelajaran yang mengikuti kelas"),
    (ROLE_DOSEN, "Pengajar yang mengelola kelas dan penilaian"),
    (ROLE_ADMIN, "Pengelola akun, data akademik, dan laporan"),
    (ROLE_SUPER, "Pengelola sistem, hak akses, dan konfigurasi"),
]


def ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def ensure_base():
    """Role, permission, role_permissions bawaan, dan konfigurasi default (idempoten)."""
    for name, desc in ROLE_DEFS:
        run("INSERT OR IGNORE INTO roles (role_name, description) VALUES (?,?)", (name, desc))
    roles = {r["role_name"]: r["role_id"] for r in q("SELECT * FROM roles")}
    for name, desc, _group, default_roles in auth.PERMISSIONS:
        exists = q("SELECT permission_id FROM permissions WHERE permission_name = ?", (name,), one=True)
        if exists is None:
            pid = run("INSERT INTO permissions (permission_name, description) VALUES (?,?)", (name, desc))
            for rname in default_roles:
                run("INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)",
                    (roles[rname], pid))
        else:
            run("UPDATE permissions SET description = ? WHERE permission_name = ?", (desc, name))
    settings.ensure_defaults()


_DEMO_HASH = None


def _demo_hash():
    """Hash password demo dihitung sekali (pbkdf2 sengaja lambat) dan dipakai semua akun demo."""
    global _DEMO_HASH
    if _DEMO_HASH is None:
        _DEMO_HASH = hash_password(DEMO_PASSWORD)
    return _DEMO_HASH


def _user(role, name, email, status="aktif", created=None):
    rid = scalar("SELECT role_id FROM roles WHERE role_name = ?", (role,))
    return run(
        "INSERT INTO users (role_id, full_name, email, password_hash, account_status, created_at) VALUES (?,?,?,?,?,?)",
        (rid, name, email, _demo_hash(), status, ts(created or datetime.now())),
    )


def seed_minimal():
    """Hanya satu akun super admin (untuk instalasi bersih)."""
    _user(ROLE_SUPER, "Super Admin", "superadmin@learnova.id")


def _write(subdir, filename, text):
    folder = os.path.join(current_app.config["UPLOAD_DIR"], subdir)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, filename), "w", encoding="utf-8") as fh:
        fh.write(text)
    return "%s/%s" % (subdir, filename)


def seed_demo():
    rnd = random.Random(7)
    now = datetime.now()
    D = lambda days=0, hours=0: now + timedelta(days=days, hours=hours)

    # ------------------------------------------------------------ akun
    sa = _user(ROLE_SUPER, "Rahmat Hidayat", "superadmin@learnova.id", created=D(-120))
    ad = _user(ROLE_ADMIN, "Sinta Maharani", "admin@learnova.id", created=D(-110))
    _user(ROLE_ADMIN, "Yoga Prakoso", "admin2@learnova.id", created=D(-90))
    d1 = _user(ROLE_DOSEN, "Dr. Budi Santoso, M.Kom.", "dosen@learnova.id", created=D(-100))
    d2 = _user(ROLE_DOSEN, "Dewi Lestari, M.T.", "dosen2@learnova.id", created=D(-100))
    _user(ROLE_DOSEN, "Hendra Wijaya, M.Sc.", "dosen3@learnova.id", status="pending", created=D(-1))
    names = ["Andi Pratama", "Bunga Citra Lestari", "Cahyo Nugroho", "Dian Permatasari", "Eko Saputra",
             "Fitri Handayani", "Gilang Ramadhan", "Hana Salsabila", "Indra Kusuma", "Jasmine Putri"]
    students = []
    for i, n in enumerate(names):
        email = "mahasiswa@learnova.id" if i == 0 else "mahasiswa%d@learnova.id" % (i + 1)
        students.append(_user(ROLE_MAHASISWA, n, email, created=D(-80 + i)))
    _user(ROLE_MAHASISWA, "Kevin Anggara", "mahasiswa11@learnova.id", status="pending", created=D(0, -3))
    _user(ROLE_MAHASISWA, "Lina Marlina", "mahasiswa12@learnova.id", status="nonaktif", created=D(-60))

    # ------------------------------------------------------------ mata kuliah & kelas
    courses = {}
    for code, name, desc in [
        ("IF301", "Pemrograman Web", "Dasar HTML, CSS, JavaScript, dan pengembangan aplikasi web sisi server."),
        ("IF205", "Basis Data", "Pemodelan data, SQL, normalisasi, dan transaksi."),
        ("IF402", "Kecerdasan Buatan", "Pencarian, representasi pengetahuan, dan pembelajaran mesin dasar."),
        ("MA101", "Statistika Dasar", "Statistika deskriptif, peluang, dan inferensi."),
    ]:
        courses[code] = run("INSERT INTO courses (course_name, course_code, description) VALUES (?,?,?)",
                            (name, code, desc))
    ay = "2026/2027"
    c1 = run("INSERT INTO classes (course_id, lecturer_id, class_name, semester, academic_year, status) VALUES (?,?,?,?,?,?)",
             (courses["IF301"], d1, "Pemrograman Web - Kelas A", "Ganjil", ay, "aktif"))
    c2 = run("INSERT INTO classes (course_id, lecturer_id, class_name, semester, academic_year, status) VALUES (?,?,?,?,?,?)",
             (courses["IF301"], d1, "Pemrograman Web - Kelas B", "Ganjil", ay, "aktif"))
    c3 = run("INSERT INTO classes (course_id, lecturer_id, class_name, semester, academic_year, status) VALUES (?,?,?,?,?,?)",
             (courses["IF205"], d2, "Basis Data - Kelas A", "Ganjil", ay, "aktif"))
    c4 = run("INSERT INTO classes (course_id, lecturer_id, class_name, semester, academic_year, status) VALUES (?,?,?,?,?,?)",
             (courses["IF402"], d1, "Kecerdasan Buatan - Kelas A", "Ganjil", ay, "aktif"))
    c5 = run("INSERT INTO classes (course_id, lecturer_id, class_name, semester, academic_year, status) VALUES (?,?,?,?,?,?)",
             (courses["MA101"], d2, "Statistika Dasar - Kelas A", "Genap", "2025/2026", "selesai"))

    enroll = {}

    def enrol(cid, sid, when=None):
        enroll[(cid, sid)] = run(
            "INSERT INTO enrollments (class_id, student_id, enrolled_at, status) VALUES (?,?,?,'aktif')",
            (cid, sid, ts(when or D(-30))))

    for s in students[:8]:
        enrol(c1, s)
    for s in students[5:10]:
        enrol(c2, s)
    for s in [students[0], students[1], students[2], students[3], students[4], students[8]]:
        enrol(c3, s)
    for s in [students[0], students[2], students[6], students[7]]:
        enrol(c4, s)
    for s in students[:4]:
        enrol(c5, s, D(-200))

    # ------------------------------------------------------------ materi
    def material(cid, title, desc, mtype, seq, prereq=None, url=None, body=None, days=-25):
        if body and not url:
            fname = "%08x_%s.md" % (rnd.getrandbits(32), title.lower().replace(" ", "-").replace("&", "dan"))
            url = _write("materials", fname, "# %s\n\n%s\n" % (title, body))
        return run(
            "INSERT INTO materials (class_id, title, description, file_url, material_type, sequence_no, prerequisite_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)", (cid, title, desc, url, mtype, seq, prereq, ts(D(days))))

    m1 = material(c1, "Pengantar Pengembangan Web", "Cara kerja web, klien-server, dan alat yang dipakai di kelas.", "dokumen", 1,
                  body="Web bekerja dengan model klien-server. Browser mengirim permintaan HTTP, server membalas dengan HTML, CSS, dan JavaScript.\n\n## Yang dipelajari\n- Alur permintaan dan respons HTTP\n- Struktur proyek web\n- Persiapan VS Code dan Git")
    m2 = material(c1, "HTML Semantik & Aksesibilitas", "Elemen semantik, form, dan dasar aksesibilitas.", "slide", 2, m1,
                  body="Gunakan elemen yang sesuai makna: header, nav, main, section, article, footer.\n\nSetiap input form harus punya label.")
    m3 = material(c1, "CSS Layout: Flexbox & Grid", "Menyusun tata letak responsif.", "dokumen", 3, m2,
                  body="Flexbox untuk satu dimensi, Grid untuk dua dimensi.\n\n```css\n.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }\n```")
    m4 = material(c1, "Dokumentasi MDN: JavaScript", "Bacaan wajib sebelum sesi JavaScript.", "tautan", 4, m3,
                  url="https://developer.mozilla.org/id/docs/Web/JavaScript")
    m5 = material(c1, "Manipulasi DOM & Event", "Mengubah halaman secara dinamis.", "video", 5, m4,
                  url="https://www.youtube.com/results?search_query=javascript+dom+tutorial")
    m6 = material(c1, "Backend dengan Flask", "Routing, template, dan database.", "dokumen", 6, m5,
                  body="Flask adalah micro-framework Python. Route memetakan URL ke fungsi, template Jinja2 membentuk HTML.")
    mm = [
        material(c3, "Model Relasional", "Tabel, kunci, dan relasi.", "dokumen", 1, body="Relasi = himpunan tupel. Primary key mengidentifikasi baris, foreign key menghubungkan tabel."),
        material(c3, "SQL Dasar", "SELECT, INSERT, UPDATE, DELETE.", "slide", 2, None, body="Mulai dengan SELECT ... FROM ... WHERE ..."),
        material(c3, "Normalisasi 1NF-3NF", "Menghilangkan redundansi data.", "dokumen", 3, None, body="1NF: nilai atomik. 2NF: tidak ada ketergantungan parsial. 3NF: tidak ada ketergantungan transitif."),
        material(c3, "Join & Subquery", "Menggabungkan data dari beberapa tabel.", "dokumen", 4, None, body="INNER JOIN, LEFT JOIN, dan subquery korelasi."),
        material(c4, "Agen Cerdas", "Konsep agen, lingkungan, dan rasionalitas.", "dokumen", 1, body="Agen merasakan lingkungan melalui sensor dan bertindak melalui aktuator."),
        material(c4, "Pencarian: BFS & DFS", "Strategi pencarian tanpa informasi.", "slide", 2, None, body="BFS melebar level demi level, DFS menyelam sedalam mungkin."),
        material(c4, "Regresi Linear", "Pengantar pembelajaran mesin terawasi.", "dokumen", 3, None, body="Meminimalkan galat kuadrat antara prediksi dan target."),
    ]
    # prasyarat berantai untuk kelas 3
    run("UPDATE materials SET prerequisite_id = ? WHERE material_id = ?", (mm[0], mm[1]))
    run("UPDATE materials SET prerequisite_id = ? WHERE material_id = ?", (mm[1], mm[2]))
    material(c2, "Pengantar Pengembangan Web", "Materi pengantar kelas B.", "dokumen", 1, body="Materi pengantar untuk kelas B.")

    def progress(cid, sid, mats):
        eid = enroll.get((cid, sid))
        for mid in mats:
            run("INSERT OR IGNORE INTO material_progress (enrollment_id, material_id, is_completed, completed_at) VALUES (?,?,1,?)",
                (eid, mid, ts(D(-rnd.randint(1, 20)))))

    chain = [m1, m2, m3, m4, m5, m6]
    for idx, s in enumerate(students[:8]):
        progress(c1, s, chain[:max(0, 6 - idx)] if idx != 0 else chain[:3])
    for s in [students[0], students[1], students[2]]:
        progress(c3, s, mm[:2])
    progress(c4, students[0], [x for x in [scalar("SELECT material_id FROM materials WHERE class_id=? AND sequence_no=1", (c4,))]])

    # ------------------------------------------------------------ sesi & absensi
    sessions = []
    topics = ["Pengantar & kontrak kuliah", "HTML semantik", "CSS dasar", "Flexbox & Grid", "JavaScript dasar", "DOM & Event"]
    for i, t in enumerate(topics):
        sid = run("INSERT INTO class_sessions (class_id, session_date, topic, attendance_open) VALUES (?,?,?,0)",
                  (c1, ts((D(-7 * (len(topics) - i))).replace(hour=8, minute=0, second=0)), t))
        sessions.append(sid)
        for s in students[:8]:
            r = rnd.random()
            if s == students[0]:
                status = "hadir"
            else:
                status = "hadir" if r < 0.78 else ("izin" if r < 0.86 else ("sakit" if r < 0.93 else "alpa"))
            run("INSERT INTO attendances (session_id, student_id, attendance_status, recorded_at) VALUES (?,?,?,?)",
                (sid, s, status, ts(D(-7 * (len(topics) - i)).replace(hour=8, minute=rnd.randint(1, 20)))))
    run("INSERT INTO class_sessions (class_id, session_date, topic, attendance_open) VALUES (?,?,?,1)",
        (c1, ts(now.replace(minute=0, second=0)), "Backend dengan Flask"))
    run("INSERT INTO class_sessions (class_id, session_date, topic, attendance_open) VALUES (?,?,?,0)",
        (c1, ts(D(7).replace(hour=8, minute=0, second=0)), "Basis data untuk aplikasi web"))
    for i in range(3):
        sid = run("INSERT INTO class_sessions (class_id, session_date, topic, attendance_open) VALUES (?,?,?,0)",
                  (c3, ts(D(-14 + i * 7).replace(hour=10, minute=0, second=0)), ["Model relasional", "SQL dasar", "Normalisasi"][i]))
        for s in [students[0], students[1], students[2], students[3]]:
            run("INSERT INTO attendances (session_id, student_id, attendance_status, recorded_at) VALUES (?,?,?,?)",
                (sid, s, "hadir" if rnd.random() < 0.85 else "izin", ts(D(-14 + i * 7).replace(hour=10, minute=3))))

    # ------------------------------------------------------------ tugas
    a1 = run("INSERT INTO assignments (class_id, title, instructions, deadline, max_score, created_at) VALUES (?,?,?,?,?,?)",
             (c1, "Tugas 1 - Halaman Profil Responsif",
              "Buat halaman profil pribadi dengan HTML semantik dan CSS responsif. Kumpulkan dalam bentuk .zip yang berisi index.html dan style.css.",
              ts(D(-12)), 100, ts(D(-24))))
    a2 = run("INSERT INTO assignments (class_id, title, instructions, deadline, max_score, created_at) VALUES (?,?,?,?,?,?)",
             (c1, "Tugas 2 - Kalkulator JavaScript",
              "Bangun kalkulator sederhana dengan JavaScript murni. Sertakan laporan singkat (maks. 1 halaman) tentang desain event handler.",
              ts(D(-2)), 100, ts(D(-14))))
    a3 = run("INSERT INTO assignments (class_id, title, instructions, deadline, max_score, created_at) VALUES (?,?,?,?,?,?)",
             (c1, "Tugas 3 - Mini Aplikasi Flask",
              "Buat aplikasi catatan sederhana dengan Flask dan SQLite. Minimal ada fitur tambah, lihat, dan hapus catatan.",
              ts(D(6)), 100, ts(D(-3))))
    a4 = run("INSERT INTO assignments (class_id, title, instructions, deadline, max_score, created_at) VALUES (?,?,?,?,?,?)",
             (c3, "Tugas 1 - Perancangan ERD",
              "Rancang ERD untuk sistem perpustakaan kampus. Kumpulkan dalam bentuk PDF atau gambar.", ts(D(4)), 100, ts(D(-5))))
    feedbacks = ["Struktur HTML sudah rapi. Tingkatkan kontras warna.", "Bagus! Responsif di semua ukuran layar.",
                 "Logika sudah benar, tambahkan penanganan pembagian dengan nol.", "Perlu perbaikan pada aksesibilitas form."]
    for i, s in enumerate(students[:8]):
        if i == 6:  # satu mahasiswa belum mengumpulkan tugas 1
            continue
        f = _write("submissions", "%08x_tugas1_%s.txt" % (rnd.getrandbits(32), names[i].split()[0].lower()),
                   "Pengumpulan Tugas 1 oleh %s\nIsi: index.html, style.css\n" % names[i])
        score = rnd.choice([72, 80, 85, 88, 90, 95]) if i != 0 else 92
        run("INSERT INTO submissions (assignment_id, student_id, file_url, submitted_at, score, feedback, status) VALUES (?,?,?,?,?,?,?)",
            (a1, s, f, ts(D(-13, rnd.randint(-10, 0))), score, rnd.choice(feedbacks), "dinilai"))
    for i, s in enumerate(students[:8]):
        if i in (5, 7):
            continue
        f = _write("submissions", "%08x_tugas2_%s.txt" % (rnd.getrandbits(32), names[i].split()[0].lower()),
                   "Pengumpulan Tugas 2 oleh %s\n" % names[i])
        graded = i < 4
        run("INSERT INTO submissions (assignment_id, student_id, file_url, submitted_at, score, feedback, status) VALUES (?,?,?,?,?,?,?)",
            (a2, s, f, ts(D(-3, rnd.randint(-6, 0))), rnd.choice([70, 78, 84, 90]) if graded else None,
             rnd.choice(feedbacks) if graded else None, "dinilai" if graded else "dikumpulkan"))
    f = _write("submissions", "%08x_tugas3_andi.txt" % rnd.getrandbits(32), "Draft Tugas 3 oleh Andi Pratama\n")
    run("INSERT INTO submissions (assignment_id, student_id, file_url, submitted_at, status) VALUES (?,?,?,?,?)",
        (a3, students[1], f, ts(D(-1)), "dikumpulkan"))

    # ------------------------------------------------------------ kuis
    def quiz(cid, title, minutes, deadline, items):
        qid = run("INSERT INTO quizzes (class_id, title, duration_minutes, deadline, max_score) VALUES (?,?,?,?,100)",
                  (cid, title, minutes, ts(deadline)))
        qs = []
        for text, qtype, points, options in items:
            quid = run("INSERT INTO quiz_questions (quiz_id, question_text, question_type, points) VALUES (?,?,?,?)",
                       (qid, text, qtype, points))
            opt_ids = []
            for k, (otext, correct) in enumerate(options):
                opt_ids.append(run("INSERT INTO quiz_options (question_id, option_text, is_correct) VALUES (?,?,?)",
                                   (quid, otext, 1 if correct else 0)))
            qs.append((quid, qtype, points, opt_ids, [c for _, c in options]))
        return qid, qs

    z1, z1q = quiz(c1, "Kuis 1 - HTML & CSS", 20, D(5), [
        ("Elemen HTML mana yang paling tepat untuk menandai navigasi utama situs?", "pilihan_ganda", 20,
         [("<div class=\"menu\">", False), ("<nav>", True), ("<section>", False), ("<span>", False)]),
        ("Properti CSS untuk membuat tata letak dua dimensi (baris dan kolom) adalah...", "pilihan_ganda", 20,
         [("display: block", False), ("display: inline", False), ("display: grid", True), ("float: left", False)]),
        ("Atribut yang menghubungkan <label> dengan <input> adalah...", "pilihan_ganda", 20,
         [("name", False), ("for", True), ("id-ref", False), ("target", False)]),
        ("Satuan CSS yang relatif terhadap ukuran font elemen root adalah...", "pilihan_ganda", 20,
         [("px", False), ("em", False), ("rem", True), ("pt", False)]),
        ("Jelaskan dengan kata-kata Anda mengapa HTML semantik penting bagi aksesibilitas.", "esai", 20, []),
    ])
    z2, _ = quiz(c3, "Kuis Normalisasi", 15, D(8), [
        ("Bentuk normal pertama (1NF) mensyaratkan bahwa...", "pilihan_ganda", 50,
         [("Semua atribut bernilai atomik", True), ("Tidak ada ketergantungan transitif", False),
          ("Tabel memiliki foreign key", False)]),
        ("Ketergantungan transitif dihilangkan pada bentuk normal ke-...", "pilihan_ganda", 50,
         [("Pertama", False), ("Kedua", False), ("Ketiga", True)]),
    ])
    z0, z0q = quiz(c1, "Kuis 0 - Pemanasan (selesai)", 10, D(-9), [
        ("Protokol yang dipakai browser untuk meminta halaman web adalah...", "pilihan_ganda", 50,
         [("FTP", False), ("HTTP", True), ("SMTP", False)]),
        ("Ekstensi berkas halaman web statis biasanya...", "pilihan_ganda", 50,
         [(".html", True), (".exe", False), (".mp3", False)]),
    ])

    def attempt(qid, sid, qs, correct_ratio, when, no=1, minutes=12):
        aid = run("INSERT INTO quiz_attempts (quiz_id, student_id, started_at, submitted_at, attempt_no) VALUES (?,?,?,?,?)",
                  (qid, sid, ts(when), ts(when + timedelta(minutes=minutes)), no))
        for quid, qtype, points, opt_ids, flags in qs:
            if qtype == "pilihan_ganda":
                ok = rnd.random() < correct_ratio
                if ok:
                    pick = opt_ids[flags.index(True)]
                else:
                    wrong = [o for o, c in zip(opt_ids, flags) if not c]
                    pick = rnd.choice(wrong)
                run("INSERT INTO quiz_answers (attempt_id, question_id, selected_option_id, is_correct, score_awarded) VALUES (?,?,?,?,?)",
                    (aid, quid, pick, 1 if ok else 0, points if ok else 0))
            else:
                run("INSERT INTO quiz_answers (attempt_id, question_id, answer_text, score_awarded) VALUES (?,?,?,?)",
                    (aid, quid, "HTML semantik memberi makna pada struktur sehingga pembaca layar dapat menavigasi halaman dengan lebih mudah.", None))
        from .services import recompute_attempt
        recompute_attempt(aid)
        return aid

    for i, s in enumerate(students[:6]):
        attempt(z1, s, z1q, 0.85 - i * 0.07, D(-rnd.randint(0, 3), -rnd.randint(1, 5)))
    # nilai esai untuk beberapa percobaan (agar ada yang sudah selesai dinilai)
    for att in q("SELECT attempt_id FROM quiz_attempts WHERE quiz_id = ? ORDER BY attempt_id LIMIT 3", (z1,)):
        run("UPDATE quiz_answers SET score_awarded = ? WHERE attempt_id = ? AND score_awarded IS NULL",
            (rnd.choice([14, 16, 18, 20]), att["attempt_id"]))
        from .services import recompute_attempt
        recompute_attempt(att["attempt_id"])
    for s in students[:8]:
        attempt(z0, s, z0q, 0.9, D(-10, rnd.randint(0, 5)))

    # ------------------------------------------------------------ laporan kendala
    run("INSERT INTO user_reports (reporter_id, handled_by, subject, description, status, created_at, response) VALUES (?,?,?,?,?,?,?)",
        (students[2], ad, "Tidak bisa mengunggah tugas .zip", "Saat mengunggah file .zip berukuran 8 MB muncul pesan gagal. Mohon dicek.", "selesai", ts(D(-6)),
         "Batas unggahan sudah dinaikkan menjadi 10 MB. Silakan coba lagi."))
    run("INSERT INTO user_reports (reporter_id, handled_by, subject, description, status, created_at, response) VALUES (?,?,?,?,?,?,?)",
        (students[4], ad, "Absensi tidak tercatat", "Saya sudah menekan tombol hadir pada sesi 4 tetapi statusnya masih kosong.", "diproses", ts(D(-2)),
         "Sedang kami cek bersama dosen pengampu."))
    run("INSERT INTO user_reports (reporter_id, subject, description, status, created_at) VALUES (?,?,?,?,?)",
        (d2, "Mohon penambahan peserta kelas", "Dua mahasiswa pindahan belum terdaftar di Basis Data - Kelas A.", "baru", ts(D(0, -5))))
    run("INSERT INTO user_reports (reporter_id, subject, description, status, created_at) VALUES (?,?,?,?,?)",
        (students[7], "Link materi video tidak terbuka", "Materi 'Manipulasi DOM & Event' mengarah ke halaman kosong.", "baru", ts(D(0, -1))))

    # ------------------------------------------------------------ audit log (14 hari terakhir)
    actors = [sa, ad, d1, d2] + students
    for day in range(14, -1, -1):
        for _ in range(rnd.randint(4, 12)):
            actor = rnd.choice(actors)
            act = rnd.choice(["auth.login", "auth.login", "auth.login", "auth.logout", "material.create",
                              "assignment.grade", "attendance.toggle", "account.verify", "class.update"])
            when = D(-day).replace(hour=rnd.randint(7, 21), minute=rnd.randint(0, 59), second=rnd.randint(0, 59))
            if when > now:
                when = now - timedelta(minutes=rnd.randint(1, 90))
            run("INSERT INTO audit_logs (actor_user_id, action, target_type, target_id, details, created_at) VALUES (?,?,?,?,?,?)",
                (actor, act, "user" if act.startswith("auth") else None, actor if act.startswith("auth") else None,
                 "Data demo", ts(when)))
    run("UPDATE system_settings SET updated_by = ?, updated_at = ? WHERE setting_key = 'app_name'", (sa, ts(D(-30))))
    get_db().commit()
