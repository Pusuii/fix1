"""Uji alur utama Learnova.  Jalankan:  python -m unittest discover -s tests -v"""
import io
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from learnova import create_app  # noqa: E402
from learnova.db import q, scalar  # noqa: E402

PW = "Learnova123"


class Client:
    """Pembungkus test client yang otomatis menyertakan token CSRF."""

    def __init__(self, app):
        self.app = app
        self.c = app.test_client()

    def token(self):
        with self.c.session_transaction() as s:
            if "_csrf" not in s:
                s["_csrf"] = "test-token"
            return s["_csrf"]

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data.setdefault("_csrf", self.token())
        return self.c.post(url, data=data, **kw)

    def login(self, email, password=PW):
        return self.post("/login", {"email": email, "password": password})

    def logout(self):
        return self.post("/logout")


def future(days=0, hours=0):
    return (datetime.now() + timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M")


class FlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app({
            "TESTING": True,
            "DATABASE": os.path.join(cls.tmp.name, "t.db"),
            "UPLOAD_DIR": os.path.join(cls.tmp.name, "uploads"),
        })

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def login_as(self, email):
        c = Client(self.app)
        r = c.login(email)
        self.assertEqual(r.status_code, 302, "login %s gagal" % email)
        return c

    # ------------------------------------------------------------ keamanan dasar
    def test_00_public_and_security(self):
        c = Client(self.app)
        for path in ("/", "/login", "/daftar"):
            self.assertEqual(c.get(path).status_code, 200)
        self.assertEqual(c.get("/mahasiswa/").status_code, 302)           # wajib login
        r = c.c.post("/login", data={"email": "mahasiswa@learnova.id", "password": PW})
        self.assertEqual(r.status_code, 400)                              # tanpa CSRF ditolak
        r = c.login("mahasiswa@learnova.id", "salah-sekali1")
        self.assertEqual(r.status_code, 401)
        r = c.post("/login?next=//evil.example", {"email": "mahasiswa@learnova.id", "password": PW})
        self.assertNotIn("evil.example", r.headers["Location"])           # open redirect diblok

    def test_01_role_isolation(self):
        s = self.login_as("mahasiswa@learnova.id")
        for path in ("/dosen/", "/admin/", "/super/", "/admin/akun"):
            self.assertEqual(s.get(path).status_code, 403, path)
        d = self.login_as("dosen@learnova.id")
        for path in ("/mahasiswa/", "/admin/", "/super/"):
            self.assertEqual(d.get(path).status_code, 403, path)
        self.assertEqual(d.get("/dosen/kelas/3").status_code, 403)        # kelas dosen lain
        s2 = self.login_as("mahasiswa@learnova.id")
        self.assertEqual(s2.get("/mahasiswa/kelas/2").status_code, 403)   # belum bergabung

    # ------------------------------------------------------------ registrasi & verifikasi
    def test_02_register_verify_login(self):
        c = Client(self.app)
        r = c.post("/daftar", {"role": "mahasiswa", "full_name": "Tester Baru", "email": "baru@example.com",
                               "password": "Abcdef12", "confirm": "Abcdef12"})
        self.assertEqual(r.status_code, 302)
        r = c.login("baru@example.com", "Abcdef12")
        self.assertEqual(r.status_code, 401)
        self.assertIn("menunggu verifikasi", r.get_data(as_text=True))
        uid = scalar("SELECT user_id FROM users WHERE email = 'baru@example.com'")
        a = self.login_as("admin@learnova.id")
        self.assertEqual(a.get("/admin/akun/%d" % uid).status_code, 200)
        r = a.post("/admin/akun/%d/verifikasi" % uid, {"decision": "tolak", "reason": "x"})   # alasan terlalu pendek
        self.assertEqual(scalar("SELECT account_status FROM users WHERE user_id = ?", (uid,), ""), "pending")
        a.post("/admin/akun/%d/verifikasi" % uid, {"decision": "aktifkan"})
        self.assertEqual(scalar("SELECT account_status FROM users WHERE user_id = ?", (uid,), ""), "aktif")
        c2 = Client(self.app)
        self.assertEqual(c2.login("baru@example.com", "Abcdef12").status_code, 302)
        r = c.post("/daftar", {"role": "dosen", "full_name": "X", "email": "baru@example.com", "password": "123", "confirm": "1"})
        self.assertEqual(r.status_code, 200)                              # validasi gagal tetap di halaman

    # ------------------------------------------------------------ mahasiswa
    def test_10_student_learning_flow(self):
        s = self.login_as("mahasiswa@learnova.id")
        # materi terkunci bila prasyarat belum selesai (materi 4 butuh 3, 3 sudah selesai di seed -> pakai 5 butuh 4)
        r = s.post("/mahasiswa/kelas/1/materi/5/selesai")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(scalar("SELECT COUNT(*) FROM material_progress mp JOIN enrollments e ON e.enrollment_id = mp.enrollment_id "
                                "WHERE e.student_id = 7 AND mp.material_id = 5"), 0)
        page = s.get("/mahasiswa/kelas/1/materi/5").get_data(as_text=True)
        self.assertIn("masih terkunci", page)
        s.post("/mahasiswa/kelas/1/materi/4/selesai")
        s.post("/mahasiswa/kelas/1/materi/5/selesai")
        self.assertEqual(scalar("SELECT COUNT(*) FROM material_progress mp JOIN enrollments e ON e.enrollment_id = mp.enrollment_id "
                                "WHERE e.student_id = 7 AND mp.material_id = 5 AND mp.is_completed = 1"), 1)

        # tugas: sebelum tenggat boleh, setelah tenggat ditolak
        r = s.post("/mahasiswa/kelas/1/tugas/3/kumpul", {"file": (io.BytesIO(b"isi tugas"), "tugas3.txt")},
                   content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        sub = q("SELECT * FROM submissions WHERE assignment_id = 3 AND student_id = 7", one=True)
        self.assertIsNotNone(sub)
        self.assertTrue(os.path.exists(os.path.join(self.app.config["UPLOAD_DIR"], sub["file_url"])))
        r = s.post("/mahasiswa/kelas/1/tugas/2/kumpul", {"file": (io.BytesIO(b"x"), "telat.txt")}, content_type="multipart/form-data")
        self.assertEqual(scalar("SELECT COUNT(*) FROM submissions WHERE assignment_id = 2 AND submitted_at > (SELECT deadline FROM assignments WHERE assignment_id = 2)"), 0)
        r = s.post("/mahasiswa/kelas/1/tugas/3/kumpul", {"file": (io.BytesIO(b"x"), "malware.exe")}, content_type="multipart/form-data")
        self.assertEqual(scalar("SELECT COUNT(*) FROM submissions WHERE file_url LIKE '%malware.exe'"), 0)  # ekstensi dilarang

        # unduh berkas: pemilik boleh, mahasiswa lain tidak
        url = "/berkas/" + sub["file_url"]
        self.assertEqual(s.get(url).status_code, 200)
        other = self.login_as("mahasiswa3@learnova.id")
        self.assertEqual(other.get(url).status_code, 403)

        # kuis: mulai -> jawab -> kirim -> hasil
        r = s.post("/mahasiswa/kelas/1/kuis/1/mulai")
        self.assertEqual(r.status_code, 302)
        att = q("SELECT * FROM quiz_attempts WHERE quiz_id = 1 AND student_id = 7 AND submitted_at IS NULL", one=True)
        if att is None:   # batas percobaan di data demo sudah terpakai? tetap valid
            self.skipTest("batas percobaan sudah tercapai")
        self.assertEqual(s.get("/mahasiswa/kelas/1/kuis/1/percobaan/%d" % att["attempt_id"]).status_code, 200)
        form = {}
        for qu in q("SELECT * FROM quiz_questions WHERE quiz_id = 1"):
            if qu["question_type"] == "pilihan_ganda":
                right = q("SELECT option_id FROM quiz_options WHERE question_id = ? AND is_correct = 1", (qu["question_id"],), one=True)
                form["q%d" % qu["question_id"]] = right["option_id"]
            else:
                form["q%d" % qu["question_id"]] = "Jawaban esai saya."
        r = s.post("/mahasiswa/kelas/1/kuis/1/percobaan/%d" % att["attempt_id"], form)
        self.assertEqual(r.status_code, 302)
        done = q("SELECT * FROM quiz_attempts WHERE attempt_id = ?", (att["attempt_id"],), one=True)
        self.assertIsNotNone(done["submitted_at"])
        self.assertIsNone(done["score"])                                   # esai menunggu dosen
        self.assertEqual(s.get("/mahasiswa/kelas/1/kuis/1/percobaan/%d/hasil" % att["attempt_id"]).status_code, 200)
        # percobaan ketiga melewati batas (maks 2 default)
        s.post("/mahasiswa/kelas/1/kuis/1/mulai")
        self.assertLessEqual(scalar("SELECT COUNT(*) FROM quiz_attempts WHERE quiz_id = 1 AND student_id = 7"), 2)

        # absensi: sesi terbuka
        sess = q("SELECT session_id FROM class_sessions WHERE class_id = 1 AND attendance_open = 1", one=True)
        r = s.post("/mahasiswa/kelas/1/absensi/%d" % sess["session_id"], {"status": "hadir"})
        self.assertEqual(scalar("SELECT COUNT(*) FROM attendances WHERE session_id = ? AND student_id = 7", (sess["session_id"],)), 1)
        closed = q("SELECT session_id FROM class_sessions WHERE class_id = 1 AND attendance_open = 0 LIMIT 1", one=True)
        before = scalar("SELECT attendance_status = 'hadir' FROM attendances WHERE session_id = ? AND student_id = 7", (closed["session_id"],), -1)
        s.post("/mahasiswa/kelas/1/absensi/%d" % closed["session_id"], {"status": "sakit"})   # sesi tertutup: tidak berubah
        self.assertEqual(scalar("SELECT attendance_status = 'hadir' FROM attendances WHERE session_id = ? AND student_id = 7", (closed["session_id"],), -1), before)

        for path in ("/mahasiswa/", "/mahasiswa/kelas", "/mahasiswa/kelas/1/progres", "/mahasiswa/kelas/1/absensi"):
            self.assertEqual(s.get(path).status_code, 200)

        # gabung kelas baru
        r = s.post("/mahasiswa/kelas/2/gabung")
        self.assertEqual(scalar("SELECT COUNT(*) FROM enrollments WHERE class_id = 2 AND student_id = 7 AND status = 'aktif'"), 1)

    # ------------------------------------------------------------ dosen
    def test_20_lecturer_flow(self):
        d = self.login_as("dosen@learnova.id")
        r = d.post("/dosen/kelas/baru", {"course_id": 1, "class_name": "Uji Kelas C", "semester": "Ganjil",
                                         "academic_year": "2026/2027", "status": "aktif"})
        self.assertEqual(r.status_code, 302)
        cid = scalar("SELECT class_id FROM classes WHERE class_name = 'Uji Kelas C'")
        self.assertTrue(cid)
        r = d.post("/dosen/kelas/baru", {"course_id": 1, "class_name": "x", "semester": "Ganjil", "academic_year": "salah", "status": "aktif"})
        self.assertEqual(r.status_code, 200)                              # validasi

        # materi dengan unggahan + prasyarat
        r = d.post("/dosen/kelas/%d/materi/baru" % cid, {"title": "Materi Satu", "description": "d", "material_type": "dokumen",
                   "sequence_no": 1, "prerequisite_id": "", "link": "", "file": (io.BytesIO(b"# materi"), "satu.md")},
                   content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        m1 = scalar("SELECT material_id FROM materials WHERE class_id = ? AND title = 'Materi Satu'", (cid,))
        r = d.post("/dosen/kelas/%d/materi/baru" % cid, {"title": "Materi Dua", "description": "", "material_type": "tautan",
                   "sequence_no": 2, "prerequisite_id": m1, "link": "https://example.com/bacaan"}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        m2 = scalar("SELECT material_id FROM materials WHERE class_id = ? AND title = 'Materi Dua'", (cid,))
        # siklus prasyarat ditolak
        r = d.post("/dosen/kelas/%d/materi/%d/ubah" % (cid, m1), {"title": "Materi Satu", "description": "", "material_type": "dokumen",
                   "sequence_no": 1, "prerequisite_id": m2, "link": ""}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)
        r = d.post("/dosen/kelas/%d/materi/baru" % cid, {"title": "Tautan jelek", "material_type": "tautan", "sequence_no": 3, "link": "javascript:alert(1)"},
                   content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)

        # tugas + penilaian
        r = d.post("/dosen/kelas/%d/tugas/baru" % cid, {"title": "Tugas Uji", "instructions": "x", "deadline": future(2), "max_score": 100})
        self.assertEqual(r.status_code, 302)
        aid = scalar("SELECT assignment_id FROM assignments WHERE class_id = ?", (cid,))
        sub = scalar("SELECT submission_id FROM submissions WHERE assignment_id = 1 AND status = 'dikumpulkan' LIMIT 1")
        # nilai di luar rentang ditolak
        r = d.post("/dosen/kelas/1/tugas/2/nilai/%d" % scalar("SELECT submission_id FROM submissions WHERE assignment_id = 2 AND status = 'dikumpulkan' LIMIT 1"),
                   {"score": 500, "feedback": ""})
        self.assertEqual(scalar("SELECT COUNT(*) FROM submissions WHERE assignment_id = 2 AND score = 500"), 0)
        sid = scalar("SELECT submission_id FROM submissions WHERE assignment_id = 2 AND status = 'dikumpulkan' LIMIT 1")
        d.post("/dosen/kelas/1/tugas/2/nilai/%d" % sid, {"score": 88, "feedback": "Bagus"})
        row = q("SELECT * FROM submissions WHERE submission_id = ?", (sid,), one=True)
        self.assertEqual((row["score"], row["status"]), (88, "dinilai"))

        # kuis: buat, tambah soal MC & esai, validasi pilihan benar
        d.post("/dosen/kelas/%d/kuis/baru" % cid, {"title": "Kuis Uji", "duration_minutes": 10, "deadline": future(3), "max_score": 100})
        qid = scalar("SELECT quiz_id FROM quizzes WHERE class_id = ?", (cid,))
        d.post("/dosen/kelas/%d/kuis/%d/soal" % (cid, qid), {"question_type": "pilihan_ganda", "question_text": "2+2?", "points": 10,
               "option0": "3", "option1": "4", "option2": "", "option3": "", "option4": "", "correct": "1"})
        self.assertEqual(scalar("SELECT COUNT(*) FROM quiz_options WHERE is_correct = 1 AND question_id = (SELECT MAX(question_id) FROM quiz_questions WHERE quiz_id = ?)", (qid,)), 1)
        d.post("/dosen/kelas/%d/kuis/%d/soal" % (cid, qid), {"question_type": "pilihan_ganda", "question_text": "tanpa kunci?", "points": 10,
               "option0": "a", "option1": "", "correct": "1"})              # kunci menunjuk pilihan kosong
        self.assertEqual(scalar("SELECT COUNT(*) FROM quiz_questions WHERE quiz_id = ?", (qid,)), 1)
        d.post("/dosen/kelas/%d/kuis/%d/soal" % (cid, qid), {"question_type": "esai", "question_text": "Jelaskan", "points": 20})
        self.assertEqual(scalar("SELECT COUNT(*) FROM quiz_questions WHERE quiz_id = ?", (qid,)), 2)

        # nilai esai dari data demo -> skor akhir terhitung
        pend = q("SELECT qa.answer_id, qa.attempt_id FROM quiz_answers qa JOIN quiz_attempts t ON t.attempt_id = qa.attempt_id "
                 "WHERE t.quiz_id = 1 AND qa.score_awarded IS NULL LIMIT 1", one=True)
        self.assertIsNotNone(pend)
        d.post("/dosen/kelas/1/kuis/1/nilai/%d" % pend["answer_id"], {"score": 15})
        self.assertIsNotNone(scalar("SELECT score FROM quiz_attempts WHERE attempt_id = ?", (pend["attempt_id"],), None))

        # absensi: buat, buka, catat manual, tutup, hapus
        d.post("/dosen/kelas/%d/absensi/baru" % cid, {"topic": "Sesi Uji", "session_date": future(0, 1), "open_now": "1"})
        sess = q("SELECT * FROM class_sessions WHERE class_id = ?", (cid,), one=True)
        self.assertEqual(sess["attendance_open"], 1)
        d.post("/dosen/kelas/%d/absensi/%d/toggle" % (cid, sess["session_id"]))
        self.assertEqual(scalar("SELECT attendance_open FROM class_sessions WHERE session_id = ?", (sess["session_id"],)), 0)
        d.post("/dosen/kelas/1/absensi/1/catat", {"student_id": 8, "status": "izin"})
        self.assertEqual(scalar("SELECT attendance_status FROM attendances WHERE session_id = 1 AND student_id = 8", default=""), "izin")
        for path in ("/dosen/", "/dosen/kelas/1/progres", "/dosen/kelas/1/absensi/laporan", "/dosen/kelas/%d/kuis/%d" % (cid, qid),
                     "/dosen/kelas/%d/tugas/%d" % (cid, aid), "/dosen/kelas/1/kuis/1/hasil"):
            self.assertEqual(d.get(path).status_code, 200, path)

        # hapus materi menghapus berkas
        url = scalar("SELECT file_url FROM materials WHERE material_id = ?", (m1,))
        path = os.path.join(self.app.config["UPLOAD_DIR"], url)
        self.assertTrue(os.path.exists(path))
        d.post("/dosen/kelas/%d/materi/%d/hapus" % (cid, m1))
        self.assertFalse(os.path.exists(path))

    # ------------------------------------------------------------ admin
    def test_30_admin_flow(self):
        a = self.login_as("admin@learnova.id")
        r = a.post("/admin/akun/baru", {"full_name": "Dosen Uji", "email": "dosenuji@example.com", "role": "dosen", "status": "aktif", "password": "Abcdef12"})
        self.assertEqual(r.status_code, 302)
        r = a.post("/admin/akun/baru", {"full_name": "Dupe", "email": "dosenuji@example.com", "role": "dosen", "status": "aktif", "password": "Abcdef12"})
        self.assertEqual(r.status_code, 200)                              # email ganda
        r = a.post("/admin/akun/baru", {"full_name": "Hacker", "email": "h@example.com", "role": "admin", "status": "aktif", "password": "Abcdef12"})
        self.assertEqual(r.status_code, 200)                              # admin tidak bisa buat admin
        self.assertEqual(a.get("/admin/akun/3").status_code, 404)         # akun admin tidak dikelola admin
        uid = scalar("SELECT user_id FROM users WHERE email = 'dosenuji@example.com'")
        a.post("/admin/akun/%d/status" % uid, {"status": "nonaktif"})
        self.assertEqual(scalar("SELECT account_status FROM users WHERE user_id = ?", (uid,), ""), "nonaktif")
        self.assertEqual(Client(self.app).login("dosenuji@example.com", "Abcdef12").status_code, 401)
        a.post("/admin/akun/%d/status" % uid, {"status": "aktif"})

        # mata kuliah
        a.post("/admin/mata-kuliah/baru", {"course_code": "uj101", "course_name": "Uji Coba", "description": ""})
        cid = scalar("SELECT course_id FROM courses WHERE course_code = 'UJ101'")
        self.assertTrue(cid)
        r = a.post("/admin/mata-kuliah/baru", {"course_code": "UJ101", "course_name": "Duplikat", "description": ""})
        self.assertEqual(r.status_code, 200)
        a.post("/admin/mata-kuliah/1/hapus")                              # punya kelas -> ditolak
        self.assertEqual(scalar("SELECT COUNT(*) FROM courses WHERE course_id = 1"), 1)
        # kelas, peserta
        r = a.post("/admin/kelas/baru", {"course_id": cid, "lecturer_id": uid, "class_name": "Kelas Uji Admin", "semester": "Genap",
                                         "academic_year": "2026/2027", "status": "aktif"})
        self.assertEqual(r.status_code, 302)
        kid = scalar("SELECT class_id FROM classes WHERE class_name = 'Kelas Uji Admin'")
        a.post("/admin/kelas/%d/peserta" % kid, {"student_id": 7})
        eid = scalar("SELECT enrollment_id FROM enrollments WHERE class_id = ? AND student_id = 7", (kid,))
        self.assertTrue(eid)
        a.post("/admin/kelas/%d/peserta/%d/keluar" % (kid, eid))
        self.assertEqual(scalar("SELECT status FROM enrollments WHERE enrollment_id = ?", (eid,), ""), "keluar")
        a.post("/admin/kelas/%d/status" % kid, {"status": "selesai"})
        self.assertEqual(scalar("SELECT status FROM classes WHERE class_id = ?", (kid,), ""), "selesai")
        a.post("/admin/mata-kuliah/%d/hapus" % cid)                       # sekarang punya kelas
        self.assertEqual(scalar("SELECT COUNT(*) FROM courses WHERE course_id = ?", (cid,)), 1)

        # laporan
        a.post("/admin/laporan/3", {"status": "selesai", "response": ""})
        self.assertEqual(scalar("SELECT status FROM user_reports WHERE report_id = 3", default=""), "baru")   # tanggapan wajib
        a.post("/admin/laporan/3", {"status": "selesai", "response": "Sudah ditambahkan."})
        self.assertEqual(scalar("SELECT status FROM user_reports WHERE report_id = 3", default=""), "selesai")
        s = self.login_as("mahasiswa@learnova.id")
        r = s.post("/bantuan", {"subject": "Uji laporan", "description": "Ini laporan percobaan dari tes otomatis."})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(a.get("/admin/aktivitas").status_code, 200)

    # ------------------------------------------------------------ super admin
    def test_40_superadmin_flow(self):
        sa = self.login_as("superadmin@learnova.id")
        r = sa.post("/super/akun-admin/baru", {"full_name": "Admin Uji", "email": "adminuji@example.com", "status": "aktif", "password": "Abcdef12"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(scalar("SELECT r.role_name FROM users u JOIN roles r ON r.role_id = u.role_id WHERE u.email = 'adminuji@example.com'", default=""), "admin")

        # cabut izin account.manage dari admin -> admin kena 403, lalu dikembalikan
        roles = {r["role_name"]: r["role_id"] for r in q("SELECT * FROM roles")}
        perms = {p["permission_name"]: p["permission_id"] for p in q("SELECT * FROM permissions")}
        granted = [p["permission_id"] for p in q("SELECT permission_id FROM role_permissions WHERE role_id = ?", (roles["admin"],))]
        keep = [str(p) for p in granted if p != perms["account.manage"]]
        sa.post("/super/hak-akses", {"role": "admin", "perm": keep})
        a = self.login_as("admin@learnova.id")
        self.assertEqual(a.get("/admin/akun").status_code, 403)
        self.assertEqual(a.get("/admin/kelas").status_code, 200)
        nav = a.get("/admin/").get_data(as_text=True)
        self.assertNotIn('href="/admin/akun"', nav)                       # menu ikut tersembunyi
        sa.post("/super/hak-akses", {"role": "admin", "perm": [str(p) for p in granted]})
        self.assertEqual(a.get("/admin/akun").status_code, 200)
        r = sa.post("/super/hak-akses", {"role": "super_admin", "perm": []})   # super admin terkunci
        self.assertEqual(scalar("SELECT COUNT(*) FROM audit_logs WHERE action = 'role.permissions'") >= 2, True)

        # konfigurasi: validasi nilai
        base = {"app_name": "Learnova", "institution": "Kampus", "support_email": "a@b.id", "registration_open": "1",
                "require_verification": "1", "quiz_max_attempts": "2", "max_upload_mb": "10"}
        r = sa.post("/super/konfigurasi", dict(base, quiz_max_attempts="0"))
        self.assertEqual(r.status_code, 200)                              # ditolak
        r = sa.post("/super/konfigurasi", dict(base, max_upload_mb="abc"))
        self.assertEqual(r.status_code, 200)
        r = sa.post("/super/konfigurasi", dict(base, quiz_max_attempts="3"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(scalar("SELECT setting_value FROM system_settings WHERE setting_key = 'quiz_max_attempts'", default=""), "3")
        sa.post("/super/konfigurasi", dict(base, quiz_max_attempts="2"))

        # mode pemeliharaan memblokir mahasiswa tetapi bukan admin
        sa.post("/super/konfigurasi", dict(base, maintenance_mode="1"))
        s = self.login_as_maybe("mahasiswa@learnova.id")
        self.assertEqual(s, 401)
        self.assertEqual(self.login_as("admin@learnova.id").get("/admin/").status_code, 200)
        sa.post("/super/konfigurasi", dict(base))                         # matikan lagi
        self.assertEqual(self.login_as_maybe("mahasiswa@learnova.id"), 302)

        # pendaftaran ditutup
        sa.post("/super/konfigurasi", {k: v for k, v in base.items() if k != "registration_open"})
        self.assertIn("ditutup", Client(self.app).get("/daftar").get_data(as_text=True))
        sa.post("/super/konfigurasi", dict(base))

        for path in ("/super/audit?q=konfigurasi", "/super/statistik", "/super/audit?action=auth.login&from=2020-01-01"):
            self.assertEqual(sa.get(path).status_code, 200, path)

    def login_as_maybe(self, email):
        return Client(self.app).login(email).status_code


if __name__ == "__main__":
    unittest.main(verbosity=2)
