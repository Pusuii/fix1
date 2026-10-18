"""Modul Dosen: kelas, materi, tugas, kuis, absensi, progres mahasiswa."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import audit, files, services, settings
from ..auth import ROLE_DOSEN, has_perm, perm_required
from ..db import now, q, run, scalar
from ..ui import is_past, normalize_dt

bp = Blueprint("lecturer", __name__)

SEMESTERS = [("Ganjil", "Ganjil"), ("Genap", "Genap"), ("Pendek", "Pendek")]
CLASS_STATUS = [("aktif", "Aktif"), ("nonaktif", "Nonaktif"), ("selesai", "Selesai")]
MATERIAL_TYPES = [("dokumen", "Dokumen"), ("slide", "Slide"), ("video", "Video"), ("tautan", "Tautan")]


@bp.before_request
def _guard():
    if g.user is None:
        return redirect(url_for("auth.login", next=request.path))
    if g.user["role_name"] != ROLE_DOSEN:
        abort(403)


def _uid():
    return g.user["user_id"]


def _cls(cid):
    return services.lecturer_class(_uid(), cid)


def _page(template, cls, tab, **kw):
    counts = {
        "materi": scalar("SELECT COUNT(*) FROM materials WHERE class_id = ?", (cls["class_id"],)),
        "tugas": scalar("SELECT COUNT(*) FROM assignments WHERE class_id = ?", (cls["class_id"],)),
        "kuis": scalar("SELECT COUNT(*) FROM quizzes WHERE class_id = ?", (cls["class_id"],)),
        "absensi": scalar("SELECT COUNT(*) FROM class_sessions WHERE class_id = ? AND attendance_open = 1", (cls["class_id"],)),
    }
    return render_template(template, cls=cls, tab=tab, counts=counts, base="lecturer", **kw)


def _to_float(raw):
    try:
        return float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ beranda


@bp.route("/")
def dashboard():
    uid = _uid()
    my = q("SELECT c.*, co.course_name, co.course_code, "
           "(SELECT COUNT(*) FROM enrollments e WHERE e.class_id = c.class_id AND e.status = 'aktif') AS students "
           "FROM classes c JOIN courses co ON co.course_id = c.course_id "
           "WHERE c.lecturer_id = ? ORDER BY (c.status = 'aktif') DESC, co.course_name", (uid,))
    active = [c for c in my if c["status"] == "aktif"]
    to_grade = q("SELECT s.submission_id, s.submitted_at, a.assignment_id, a.title, c.class_id, c.class_name, u.full_name "
                 "FROM submissions s JOIN assignments a ON a.assignment_id = s.assignment_id "
                 "JOIN classes c ON c.class_id = a.class_id JOIN users u ON u.user_id = s.student_id "
                 "WHERE c.lecturer_id = ? AND s.status = 'dikumpulkan' ORDER BY s.submitted_at LIMIT 50", (uid,))
    essay_pending = scalar(
        "SELECT COUNT(*) FROM quiz_answers qa JOIN quiz_attempts t ON t.attempt_id = qa.attempt_id "
        "JOIN quizzes z ON z.quiz_id = t.quiz_id JOIN classes c ON c.class_id = z.class_id "
        "WHERE c.lecturer_id = ? AND t.submitted_at IS NOT NULL AND qa.score_awarded IS NULL", (uid,))
    open_sessions = q("SELECT s.*, c.class_name, c.class_id FROM class_sessions s JOIN classes c ON c.class_id = s.class_id "
                      "WHERE c.lecturer_id = ? AND s.attendance_open = 1", (uid,))
    total_students = scalar("SELECT COUNT(DISTINCT e.student_id) FROM enrollments e JOIN classes c ON c.class_id = e.class_id "
                            "WHERE c.lecturer_id = ? AND e.status = 'aktif'", (uid,))
    return render_template("lecturer/dashboard.html", classes=my, active=active, to_grade=to_grade,
                           essay_pending=essay_pending, open_sessions=open_sessions, total_students=total_students)


# ------------------------------------------------------------------ kelas


@bp.route("/kelas")
@perm_required("class.manage")
def classes():
    rows = q("SELECT c.*, co.course_name, co.course_code, u.full_name AS lecturer_name, "
             "(SELECT COUNT(*) FROM enrollments e WHERE e.class_id = c.class_id AND e.status = 'aktif') AS students, "
             "(SELECT COUNT(*) FROM materials m WHERE m.class_id = c.class_id) AS materials, "
             "(SELECT COUNT(*) FROM assignments a WHERE a.class_id = c.class_id) AS assignments "
             "FROM classes c JOIN courses co ON co.course_id = c.course_id JOIN users u ON u.user_id = c.lecturer_id "
             "WHERE c.lecturer_id = ? ORDER BY (c.status = 'aktif') DESC, co.course_name, c.class_name", (_uid(),))
    return render_template("lecturer/classes.html", rows=rows)


def _class_form(cls=None):
    courses = q("SELECT course_id, course_code, course_name FROM courses ORDER BY course_name")
    errors, form = {}, {}
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in ("course_id", "class_name", "semester", "academic_year", "status")}
        if not form["course_id"].isdigit() or not q("SELECT 1 FROM courses WHERE course_id = ?", (form["course_id"],), one=True):
            errors["course_id"] = "Pilih mata kuliah."
        if len(form["class_name"]) < 3:
            errors["class_name"] = "Nama kelas minimal 3 karakter."
        if form["semester"] not in [s[0] for s in SEMESTERS]:
            errors["semester"] = "Pilih semester."
        import re
        if not re.match(r"^\d{4}/\d{4}$", form["academic_year"]):
            errors["academic_year"] = "Gunakan format tahun ajaran, contoh 2026/2027."
        if form["status"] not in [s[0] for s in CLASS_STATUS]:
            errors["status"] = "Pilih status."
        if not errors:
            if cls:
                run("UPDATE classes SET course_id=?, class_name=?, semester=?, academic_year=?, status=? WHERE class_id=?",
                    (form["course_id"], form["class_name"], form["semester"], form["academic_year"], form["status"], cls["class_id"]))
                audit.log("class.update", "class", cls["class_id"], form["class_name"])
                flash("Kelas diperbarui.", "success")
                return redirect(url_for("lecturer.class_home", cid=cls["class_id"])), None, None, None
            cid = run("INSERT INTO classes (course_id, lecturer_id, class_name, semester, academic_year, status) VALUES (?,?,?,?,?,?)",
                      (form["course_id"], _uid(), form["class_name"], form["semester"], form["academic_year"], form["status"]))
            audit.log("class.create", "class", cid, form["class_name"])
            flash("Kelas dibuat. Tambahkan materi pertama untuk memulai.", "success")
            return redirect(url_for("lecturer.class_home", cid=cid)), None, None, None
    elif cls:
        form = {k: str(cls[k]) for k in ("course_id", "class_name", "semester", "academic_year", "status")}
    else:
        form = {"semester": "Ganjil", "academic_year": "2026/2027", "status": "aktif", "course_id": "", "class_name": ""}
    return None, form, errors, courses


@bp.route("/kelas/baru", methods=["GET", "POST"])
@perm_required("class.manage")
def class_new():
    resp, form, errors, courses = _class_form()
    if resp:
        return resp
    return render_template("lecturer/class_form.html", cls=None, form=form, errors=errors, courses=courses,
                           semesters=SEMESTERS, statuses=CLASS_STATUS)


@bp.route("/kelas/<int:cid>/ubah", methods=["GET", "POST"])
@perm_required("class.manage")
def class_edit(cid):
    cls = _cls(cid)
    resp, form, errors, courses = _class_form(cls)
    if resp:
        return resp
    return render_template("lecturer/class_form.html", cls=cls, form=form, errors=errors, courses=courses,
                           semesters=SEMESTERS, statuses=CLASS_STATUS)


@bp.route("/kelas/<int:cid>")
@perm_required("class.manage")
def class_home(cid):
    cls = _cls(cid)
    students = services.class_students(cid)
    stats = {
        "students": len(students),
        "submissions_pending": scalar("SELECT COUNT(*) FROM submissions s JOIN assignments a ON a.assignment_id = s.assignment_id "
                                      "WHERE a.class_id = ? AND s.status = 'dikumpulkan'", (cid,)),
        "essay_pending": scalar("SELECT COUNT(*) FROM quiz_answers qa JOIN quiz_attempts t ON t.attempt_id = qa.attempt_id "
                                "JOIN quizzes z ON z.quiz_id = t.quiz_id WHERE z.class_id = ? AND t.submitted_at IS NOT NULL "
                                "AND qa.score_awarded IS NULL", (cid,)),
    }
    open_sessions = q("SELECT * FROM class_sessions WHERE class_id = ? AND attendance_open = 1", (cid,))
    return _page("lecturer/class_home.html", cls, "ringkasan", students=students, stats=stats, open_sessions=open_sessions)


# ------------------------------------------------------------------ materi


@bp.route("/kelas/<int:cid>/materi")
@perm_required("material.manage")
def materials(cid):
    cls = _cls(cid)
    mats = q("SELECT m.*, p.title AS prereq_title, "
             "(SELECT COUNT(*) FROM material_progress mp WHERE mp.material_id = m.material_id AND mp.is_completed = 1) AS completed "
             "FROM materials m LEFT JOIN materials p ON p.material_id = m.prerequisite_id "
             "WHERE m.class_id = ? ORDER BY m.sequence_no, m.material_id", (cid,))
    total = scalar("SELECT COUNT(*) FROM enrollments WHERE class_id = ? AND status = 'aktif'", (cid,))
    return _page("lecturer/materials.html", cls, "materi", mats=mats, total_students=total)


def _would_cycle(mid, prereq_id):
    seen = set()
    cur = prereq_id
    while cur and cur not in seen:
        if cur == mid:
            return True
        seen.add(cur)
        row = q("SELECT prerequisite_id FROM materials WHERE material_id = ?", (cur,), one=True)
        cur = row["prerequisite_id"] if row else None
    return False


def _material_form(cls, m=None):
    cid = cls["class_id"]
    others = q("SELECT material_id, title, sequence_no FROM materials WHERE class_id = ? AND material_id != ? "
               "ORDER BY sequence_no", (cid, m["material_id"] if m else 0))
    errors, form = {}, {}
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in ("title", "description", "material_type", "link", "sequence_no", "prerequisite_id")}
        if len(form["title"]) < 3:
            errors["title"] = "Judul materi minimal 3 karakter."
        if form["material_type"] not in [t[0] for t in MATERIAL_TYPES]:
            errors["material_type"] = "Pilih jenis materi."
        seq = form["sequence_no"]
        if not seq.isdigit() or int(seq) < 1:
            errors["sequence_no"] = "Urutan harus angka 1 atau lebih."
        prereq = None
        if form["prerequisite_id"]:
            if not form["prerequisite_id"].isdigit() or not q("SELECT 1 FROM materials WHERE material_id = ? AND class_id = ?",
                                                              (form["prerequisite_id"], cid), one=True):
                errors["prerequisite_id"] = "Materi prasyarat tidak valid."
            else:
                prereq = int(form["prerequisite_id"])
                if m and _would_cycle(m["material_id"], prereq):
                    errors["prerequisite_id"] = "Prasyarat ini membuat urutan saling mengunci."
        link = form["link"]
        if link and not files.is_external(link):
            errors["link"] = "Tautan harus diawali http:// atau https://"
        upload = request.files.get("file")
        has_upload = bool(upload and upload.filename)
        if form["material_type"] == "tautan" and not link and not has_upload and not (m and m["file_url"]):
            errors["link"] = "Isi tautan untuk materi berjenis tautan."
        new_url = m["file_url"] if m else None
        if not errors:
            try:
                if has_upload:
                    new_url = files.save_upload(upload, "materials")
                    if m:
                        files.delete_file(m["file_url"])
                elif link:
                    if m:
                        files.delete_file(m["file_url"])
                    new_url = link
                elif request.form.get("remove_file") and m:
                    files.delete_file(m["file_url"])
                    new_url = None
            except files.UploadError as exc:
                errors["file"] = str(exc)
        if not errors:
            if m:
                run("UPDATE materials SET title=?, description=?, file_url=?, material_type=?, sequence_no=?, prerequisite_id=? "
                    "WHERE material_id=?", (form["title"], form["description"], new_url, form["material_type"], int(seq), prereq, m["material_id"]))
                audit.log("material.update", "material", m["material_id"], form["title"])
                flash("Materi diperbarui.", "success")
            else:
                mid = run("INSERT INTO materials (class_id, title, description, file_url, material_type, sequence_no, prerequisite_id, created_at) "
                          "VALUES (?,?,?,?,?,?,?,?)", (cid, form["title"], form["description"], new_url, form["material_type"], int(seq), prereq, now()))
                audit.log("material.create", "material", mid, form["title"])
                flash("Materi disimpan.", "success")
            return redirect(url_for("lecturer.materials", cid=cid)), None, None, None
    elif m:
        form = {"title": m["title"], "description": m["description"] or "", "material_type": m["material_type"],
                "link": m["file_url"] if files.is_external(m["file_url"] or "") else "", "sequence_no": str(m["sequence_no"]),
                "prerequisite_id": str(m["prerequisite_id"] or "")}
    else:
        nxt = scalar("SELECT MAX(sequence_no) FROM materials WHERE class_id = ?", (cid,)) + 1
        form = {"title": "", "description": "", "material_type": "dokumen", "link": "", "sequence_no": str(nxt), "prerequisite_id": ""}
    return None, form, errors, others


@bp.route("/kelas/<int:cid>/materi/baru", methods=["GET", "POST"])
@perm_required("material.manage")
def material_new(cid):
    cls = _cls(cid)
    resp, form, errors, others = _material_form(cls)
    if resp:
        return resp
    return _page("lecturer/material_form.html", cls, "materi", m=None, form=form, errors=errors, others=others,
                 types=MATERIAL_TYPES, max_mb=settings.get_int("max_upload_mb"))


@bp.route("/kelas/<int:cid>/materi/<int:mid>/ubah", methods=["GET", "POST"])
@perm_required("material.manage")
def material_edit(cid, mid):
    cls = _cls(cid)
    m = q("SELECT * FROM materials WHERE material_id = ? AND class_id = ?", (mid, cid), one=True)
    if m is None:
        abort(404)
    resp, form, errors, others = _material_form(cls, m)
    if resp:
        return resp
    return _page("lecturer/material_form.html", cls, "materi", m=m, form=form, errors=errors, others=others,
                 types=MATERIAL_TYPES, max_mb=settings.get_int("max_upload_mb"))


@bp.route("/kelas/<int:cid>/materi/<int:mid>/hapus", methods=["POST"])
@perm_required("material.manage")
def material_delete(cid, mid):
    _cls(cid)
    m = q("SELECT * FROM materials WHERE material_id = ? AND class_id = ?", (mid, cid), one=True)
    if m is None:
        abort(404)
    files.delete_file(m["file_url"])
    run("DELETE FROM materials WHERE material_id = ?", (mid,))
    audit.log("material.delete", "material", mid, m["title"])
    flash("Materi dihapus.", "success")
    return redirect(url_for("lecturer.materials", cid=cid))


# ------------------------------------------------------------------ tugas


@bp.route("/kelas/<int:cid>/tugas")
@perm_required("assignment.manage")
def assignments(cid):
    cls = _cls(cid)
    rows = q("SELECT a.*, (SELECT COUNT(*) FROM submissions s WHERE s.assignment_id = a.assignment_id) AS submitted, "
             "(SELECT COUNT(*) FROM submissions s WHERE s.assignment_id = a.assignment_id AND s.status = 'dinilai') AS graded "
             "FROM assignments a WHERE a.class_id = ? ORDER BY a.deadline DESC", (cid,))
    total = scalar("SELECT COUNT(*) FROM enrollments WHERE class_id = ? AND status = 'aktif'", (cid,))
    return _page("lecturer/assignments.html", cls, "tugas", rows=rows, total=total)


def _assignment_form(cls, a=None):
    errors, form = {}, {}
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in ("title", "instructions", "deadline", "max_score")}
        if len(form["title"]) < 3:
            errors["title"] = "Judul tugas minimal 3 karakter."
        deadline = normalize_dt(form["deadline"])
        if not deadline:
            errors["deadline"] = "Isi tenggat pengumpulan."
        max_score = _to_float(form["max_score"])
        if max_score is None or max_score <= 0 or max_score > 1000:
            errors["max_score"] = "Nilai maksimum harus angka lebih dari 0."
        if not errors:
            if a:
                run("UPDATE assignments SET title=?, instructions=?, deadline=?, max_score=? WHERE assignment_id=?",
                    (form["title"], form["instructions"], deadline, max_score, a["assignment_id"]))
                audit.log("assignment.update", "assignment", a["assignment_id"], form["title"])
                flash("Tugas diperbarui.", "success")
                return redirect(url_for("lecturer.assignment", cid=cls["class_id"], aid=a["assignment_id"])), None, None
            aid = run("INSERT INTO assignments (class_id, title, instructions, deadline, max_score, created_at) VALUES (?,?,?,?,?,?)",
                      (cls["class_id"], form["title"], form["instructions"], deadline, max_score, now()))
            audit.log("assignment.create", "assignment", aid, form["title"])
            flash("Tugas dipublikasikan dan langsung terlihat oleh mahasiswa.", "success")
            return redirect(url_for("lecturer.assignment", cid=cls["class_id"], aid=aid)), None, None
    elif a:
        form = {"title": a["title"], "instructions": a["instructions"] or "", "deadline": a["deadline"], "max_score": "%g" % a["max_score"]}
    else:
        form = {"title": "", "instructions": "", "deadline": "", "max_score": "100"}
    return None, form, errors


@bp.route("/kelas/<int:cid>/tugas/baru", methods=["GET", "POST"])
@perm_required("assignment.manage")
def assignment_new(cid):
    cls = _cls(cid)
    resp, form, errors = _assignment_form(cls)
    if resp:
        return resp
    return _page("lecturer/assignment_form.html", cls, "tugas", a=None, form=form, errors=errors)


@bp.route("/kelas/<int:cid>/tugas/<int:aid>/ubah", methods=["GET", "POST"])
@perm_required("assignment.manage")
def assignment_edit(cid, aid):
    cls = _cls(cid)
    a = q("SELECT * FROM assignments WHERE assignment_id = ? AND class_id = ?", (aid, cid), one=True)
    if a is None:
        abort(404)
    resp, form, errors = _assignment_form(cls, a)
    if resp:
        return resp
    return _page("lecturer/assignment_form.html", cls, "tugas", a=a, form=form, errors=errors)


@bp.route("/kelas/<int:cid>/tugas/<int:aid>")
@perm_required("assignment.manage")
def assignment(cid, aid):
    cls = _cls(cid)
    a = q("SELECT * FROM assignments WHERE assignment_id = ? AND class_id = ?", (aid, cid), one=True)
    if a is None:
        abort(404)
    rows = q("SELECT u.user_id, u.full_name, u.email, s.submission_id, s.file_url, s.submitted_at, s.score, s.feedback, s.status "
             "FROM enrollments e JOIN users u ON u.user_id = e.student_id "
             "LEFT JOIN submissions s ON s.student_id = u.user_id AND s.assignment_id = ? "
             "WHERE e.class_id = ? AND e.status = 'aktif' ORDER BY (s.status = 'dikumpulkan') DESC, u.full_name", (aid, cid))
    late = is_past(a["deadline"])
    return _page("lecturer/assignment.html", cls, "tugas", a=a, rows=rows, late=late)


@bp.route("/kelas/<int:cid>/tugas/<int:aid>/nilai/<int:sid>", methods=["POST"])
@perm_required("assignment.manage")
def assignment_grade(cid, aid, sid):
    _cls(cid)
    a = q("SELECT * FROM assignments WHERE assignment_id = ? AND class_id = ?", (aid, cid), one=True)
    sub = q("SELECT * FROM submissions WHERE submission_id = ? AND assignment_id = ?", (sid, aid), one=True)
    if a is None or sub is None:
        abort(404)
    score = _to_float(request.form.get("score"))
    if score is None or score < 0 or score > a["max_score"]:
        flash("Nilai harus berupa angka antara 0 dan %g." % a["max_score"], "error")
        return redirect(url_for("lecturer.assignment", cid=cid, aid=aid) + "#s%d" % sid)
    feedback = (request.form.get("feedback") or "").strip()[:1000]
    run("UPDATE submissions SET score = ?, feedback = ?, status = 'dinilai' WHERE submission_id = ?", (score, feedback or None, sid))
    audit.log("assignment.grade", "submission", sid, "%s: %g/%g" % (a["title"], score, a["max_score"]))
    flash("Nilai tersimpan.", "success")
    return redirect(url_for("lecturer.assignment", cid=cid, aid=aid) + "#s%d" % sid)


@bp.route("/kelas/<int:cid>/tugas/<int:aid>/hapus", methods=["POST"])
@perm_required("assignment.manage")
def assignment_delete(cid, aid):
    _cls(cid)
    a = q("SELECT * FROM assignments WHERE assignment_id = ? AND class_id = ?", (aid, cid), one=True)
    if a is None:
        abort(404)
    for s in q("SELECT file_url FROM submissions WHERE assignment_id = ?", (aid,)):
        files.delete_file(s["file_url"])
    run("DELETE FROM assignments WHERE assignment_id = ?", (aid,))
    audit.log("assignment.delete", "assignment", aid, a["title"])
    flash("Tugas dan seluruh pengumpulannya dihapus.", "success")
    return redirect(url_for("lecturer.assignments", cid=cid))


# ------------------------------------------------------------------ kuis


@bp.route("/kelas/<int:cid>/kuis")
@perm_required("quiz.manage")
def quizzes(cid):
    cls = _cls(cid)
    rows = q("SELECT z.*, (SELECT COUNT(*) FROM quiz_questions k WHERE k.quiz_id = z.quiz_id) AS questions, "
             "(SELECT COUNT(*) FROM quiz_attempts t WHERE t.quiz_id = z.quiz_id AND t.submitted_at IS NOT NULL) AS attempts, "
             "(SELECT COUNT(*) FROM quiz_answers qa JOIN quiz_attempts t ON t.attempt_id = qa.attempt_id "
             " WHERE t.quiz_id = z.quiz_id AND t.submitted_at IS NOT NULL AND qa.score_awarded IS NULL) AS pending "
             "FROM quizzes z WHERE z.class_id = ? ORDER BY z.deadline DESC", (cid,))
    return _page("lecturer/quizzes.html", cls, "kuis", rows=rows)


def _quiz_form(cls, z=None):
    errors, form = {}, {}
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in ("title", "duration_minutes", "deadline", "max_score")}
        if len(form["title"]) < 3:
            errors["title"] = "Judul kuis minimal 3 karakter."
        if not form["duration_minutes"].isdigit() or not (1 <= int(form["duration_minutes"]) <= 300):
            errors["duration_minutes"] = "Durasi 1 sampai 300 menit."
        deadline = normalize_dt(form["deadline"])
        if not deadline:
            errors["deadline"] = "Isi tenggat kuis."
        max_score = _to_float(form["max_score"])
        if max_score is None or max_score <= 0 or max_score > 1000:
            errors["max_score"] = "Nilai maksimum harus angka lebih dari 0."
        if not errors:
            if z:
                run("UPDATE quizzes SET title=?, duration_minutes=?, deadline=?, max_score=? WHERE quiz_id=?",
                    (form["title"], int(form["duration_minutes"]), deadline, max_score, z["quiz_id"]))
                audit.log("quiz.update", "quiz", z["quiz_id"], form["title"])
                flash("Kuis diperbarui.", "success")
                return redirect(url_for("lecturer.quiz", cid=cls["class_id"], qid=z["quiz_id"])), None, None
            qid = run("INSERT INTO quizzes (class_id, title, duration_minutes, deadline, max_score) VALUES (?,?,?,?,?)",
                      (cls["class_id"], form["title"], int(form["duration_minutes"]), deadline, max_score))
            audit.log("quiz.create", "quiz", qid, form["title"])
            flash("Kuis dibuat. Tambahkan soal agar bisa dikerjakan mahasiswa.", "success")
            return redirect(url_for("lecturer.quiz", cid=cls["class_id"], qid=qid)), None, None
    elif z:
        form = {"title": z["title"], "duration_minutes": str(z["duration_minutes"]), "deadline": z["deadline"], "max_score": "%g" % z["max_score"]}
    else:
        form = {"title": "", "duration_minutes": "30", "deadline": "", "max_score": "100"}
    return None, form, errors


@bp.route("/kelas/<int:cid>/kuis/baru", methods=["GET", "POST"])
@perm_required("quiz.manage")
def quiz_new(cid):
    cls = _cls(cid)
    resp, form, errors = _quiz_form(cls)
    if resp:
        return resp
    return _page("lecturer/quiz_form.html", cls, "kuis", z=None, form=form, errors=errors)


@bp.route("/kelas/<int:cid>/kuis/<int:qid>/ubah", methods=["GET", "POST"])
@perm_required("quiz.manage")
def quiz_edit(cid, qid):
    cls = _cls(cid)
    z = q("SELECT * FROM quizzes WHERE quiz_id = ? AND class_id = ?", (qid, cid), one=True)
    if z is None:
        abort(404)
    resp, form, errors = _quiz_form(cls, z)
    if resp:
        return resp
    return _page("lecturer/quiz_form.html", cls, "kuis", z=z, form=form, errors=errors)


def _quiz(cid, qid):
    z = q("SELECT * FROM quizzes WHERE quiz_id = ? AND class_id = ?", (qid, cid), one=True)
    if z is None:
        abort(404)
    return z


@bp.route("/kelas/<int:cid>/kuis/<int:qid>")
@perm_required("quiz.manage")
def quiz(cid, qid):
    cls = _cls(cid)
    z = _quiz(cid, qid)
    questions = q("SELECT * FROM quiz_questions WHERE quiz_id = ? ORDER BY question_id", (qid,))
    options = {}
    for o in q("SELECT o.* FROM quiz_options o JOIN quiz_questions k ON k.question_id = o.question_id WHERE k.quiz_id = ? ORDER BY o.option_id", (qid,)):
        options.setdefault(o["question_id"], []).append(o)
    attempts = scalar("SELECT COUNT(*) FROM quiz_attempts WHERE quiz_id = ?", (qid,))
    return _page("lecturer/quiz.html", cls, "kuis", z=z, questions=questions, options=options, attempts=attempts,
                 total_points=sum(k["points"] for k in questions))


@bp.route("/kelas/<int:cid>/kuis/<int:qid>/soal", methods=["POST"])
@perm_required("quiz.manage")
def question_add(cid, qid):
    _cls(cid)
    _quiz(cid, qid)
    back = url_for("lecturer.quiz", cid=cid, qid=qid)
    text = (request.form.get("question_text") or "").strip()
    qtype = request.form.get("question_type")
    points = _to_float(request.form.get("points"))
    if len(text) < 3 or qtype not in ("pilihan_ganda", "esai") or points is None or points <= 0:
        flash("Lengkapi teks soal, jenis soal, dan poin (angka lebih dari 0).", "error")
        return redirect(back)
    opts = []
    if qtype == "pilihan_ganda":
        correct = request.form.get("correct", "")
        for i in range(5):
            t = (request.form.get("option%d" % i) or "").strip()
            if t:
                opts.append((t, str(i) == correct))
        if len(opts) < 2:
            flash("Soal pilihan ganda butuh minimal 2 pilihan jawaban.", "error")
            return redirect(back)
        if sum(1 for _, c in opts if c) != 1:
            flash("Pilih tepat satu jawaban yang benar (dan pastikan pilihan itu terisi).", "error")
            return redirect(back)
    quid = run("INSERT INTO quiz_questions (quiz_id, question_text, question_type, points) VALUES (?,?,?,?)", (qid, text, qtype, points))
    for t, c in opts:
        run("INSERT INTO quiz_options (question_id, option_text, is_correct) VALUES (?,?,?)", (quid, t, 1 if c else 0))
    audit.log("quiz.update", "quiz", qid, "Menambah soal")
    flash("Soal ditambahkan.", "success")
    return redirect(back)


@bp.route("/kelas/<int:cid>/kuis/<int:qid>/soal/<int:quid>/hapus", methods=["POST"])
@perm_required("quiz.manage")
def question_delete(cid, qid, quid):
    _cls(cid)
    _quiz(cid, qid)
    if not q("SELECT 1 FROM quiz_questions WHERE question_id = ? AND quiz_id = ?", (quid, qid), one=True):
        abort(404)
    run("DELETE FROM quiz_questions WHERE question_id = ?", (quid,))
    for att in q("SELECT attempt_id FROM quiz_attempts WHERE quiz_id = ? AND submitted_at IS NOT NULL", (qid,)):
        services.recompute_attempt(att["attempt_id"])
    audit.log("quiz.update", "quiz", qid, "Menghapus soal")
    flash("Soal dihapus. Nilai percobaan yang sudah ada dihitung ulang.", "success")
    return redirect(url_for("lecturer.quiz", cid=cid, qid=qid))


@bp.route("/kelas/<int:cid>/kuis/<int:qid>/hapus", methods=["POST"])
@perm_required("quiz.manage")
def quiz_delete(cid, qid):
    _cls(cid)
    z = _quiz(cid, qid)
    run("DELETE FROM quizzes WHERE quiz_id = ?", (qid,))
    audit.log("quiz.delete", "quiz", qid, z["title"])
    flash("Kuis dihapus.", "success")
    return redirect(url_for("lecturer.quizzes", cid=cid))


@bp.route("/kelas/<int:cid>/kuis/<int:qid>/hasil")
@perm_required("quiz.manage")
def quiz_results(cid, qid):
    cls = _cls(cid)
    z = _quiz(cid, qid)
    attempts = q("SELECT t.*, u.full_name, u.email FROM quiz_attempts t JOIN users u ON u.user_id = t.student_id "
                 "WHERE t.quiz_id = ? AND t.submitted_at IS NOT NULL ORDER BY u.full_name, t.attempt_no", (qid,))
    essays = q("SELECT qa.*, k.question_text, k.points, u.full_name, t.attempt_no FROM quiz_answers qa "
               "JOIN quiz_questions k ON k.question_id = qa.question_id JOIN quiz_attempts t ON t.attempt_id = qa.attempt_id "
               "JOIN users u ON u.user_id = t.student_id "
               "WHERE t.quiz_id = ? AND t.submitted_at IS NOT NULL AND k.question_type = 'esai' "
               "ORDER BY (qa.score_awarded IS NULL) DESC, qa.answer_id", (qid,))
    return _page("lecturer/quiz_results.html", cls, "kuis", z=z, attempts=attempts, essays=essays)


@bp.route("/kelas/<int:cid>/kuis/<int:qid>/nilai/<int:ans_id>", methods=["POST"])
@perm_required("quiz.manage")
def essay_grade(cid, qid, ans_id):
    _cls(cid)
    _quiz(cid, qid)
    ans = q("SELECT qa.*, k.points FROM quiz_answers qa JOIN quiz_questions k ON k.question_id = qa.question_id "
            "JOIN quiz_attempts t ON t.attempt_id = qa.attempt_id WHERE qa.answer_id = ? AND t.quiz_id = ?", (ans_id, qid), one=True)
    if ans is None:
        abort(404)
    back = url_for("lecturer.quiz_results", cid=cid, qid=qid) + "#grading"
    score = _to_float(request.form.get("score"))
    if score is None or score < 0 or score > ans["points"]:
        flash("Nilai harus antara 0 dan %g." % ans["points"], "error")
        return redirect(back)
    run("UPDATE quiz_answers SET score_awarded = ? WHERE answer_id = ?", (score, ans_id))
    final = services.recompute_attempt(ans["attempt_id"])
    audit.log("quiz.grade", "quiz_answer", ans_id, "Skor %g/%g" % (score, ans["points"]))
    flash("Nilai esai tersimpan." + (" Nilai akhir percobaan: %g." % final if final is not None else " Masih ada jawaban esai lain yang belum dinilai."), "success")
    return redirect(back)


# ------------------------------------------------------------------ absensi


@bp.route("/kelas/<int:cid>/absensi")
@perm_required("attendance.manage")
def attendance(cid):
    cls = _cls(cid)
    sessions = q("SELECT s.*, "
                 "(SELECT COUNT(*) FROM attendances a WHERE a.session_id = s.session_id AND a.attendance_status = 'hadir') AS present, "
                 "(SELECT COUNT(*) FROM attendances a WHERE a.session_id = s.session_id) AS recorded "
                 "FROM class_sessions s WHERE s.class_id = ? ORDER BY s.session_date DESC", (cid,))
    total = scalar("SELECT COUNT(*) FROM enrollments WHERE class_id = ? AND status = 'aktif'", (cid,))
    return _page("lecturer/attendance.html", cls, "absensi", sessions=sessions, total=total)


@bp.route("/kelas/<int:cid>/absensi/baru", methods=["POST"])
@perm_required("attendance.manage")
def session_new(cid):
    cls = _cls(cid)
    topic = (request.form.get("topic") or "").strip()
    when = normalize_dt(request.form.get("session_date"))
    if len(topic) < 3 or not when:
        flash("Isi topik sesi (minimal 3 karakter) dan tanggal sesi.", "error")
        return redirect(url_for("lecturer.attendance", cid=cid))
    open_now = 1 if request.form.get("open_now") else 0
    sid = run("INSERT INTO class_sessions (class_id, session_date, topic, attendance_open) VALUES (?,?,?,?)", (cid, when, topic, open_now))
    audit.log("attendance.session", "session", sid, topic)
    flash("Sesi perkuliahan dibuat%s." % (" dan absensi dibuka" if open_now else ""), "success")
    return redirect(url_for("lecturer.attendance", cid=cid))


def _session(cid, sid):
    s = q("SELECT * FROM class_sessions WHERE session_id = ? AND class_id = ?", (sid, cid), one=True)
    if s is None:
        abort(404)
    return s


@bp.route("/kelas/<int:cid>/absensi/<int:sid>/toggle", methods=["POST"])
@perm_required("attendance.manage")
def session_toggle(cid, sid):
    cls = _cls(cid)
    s = _session(cid, sid)
    if cls["status"] != "aktif":
        flash("Kelas sudah tidak aktif.", "warning")
        return redirect(url_for("lecturer.attendance", cid=cid))
    new = 0 if s["attendance_open"] else 1
    run("UPDATE class_sessions SET attendance_open = ? WHERE session_id = ?", (new, sid))
    audit.log("attendance.toggle", "session", sid, "Absensi %s: %s" % ("dibuka" if new else "ditutup", s["topic"]))
    flash("Absensi %s." % ("dibuka" if new else "ditutup"), "success")
    return redirect(request.form.get("next") or url_for("lecturer.attendance", cid=cid))


@bp.route("/kelas/<int:cid>/absensi/<int:sid>")
@perm_required("attendance.manage")
def session_detail(cid, sid):
    cls = _cls(cid)
    s = _session(cid, sid)
    rows = q("SELECT u.user_id, u.full_name, u.email, a.attendance_status, a.recorded_at FROM enrollments e "
             "JOIN users u ON u.user_id = e.student_id LEFT JOIN attendances a ON a.student_id = u.user_id AND a.session_id = ? "
             "WHERE e.class_id = ? AND e.status = 'aktif' ORDER BY u.full_name", (sid, cid))
    return _page("lecturer/session.html", cls, "absensi", s=s, rows=rows)


@bp.route("/kelas/<int:cid>/absensi/<int:sid>/catat", methods=["POST"])
@perm_required("attendance.manage")
def session_record(cid, sid):
    _cls(cid)
    s = _session(cid, sid)
    student_id = request.form.get("student_id", "")
    status = request.form.get("status")
    if status not in ("hadir", "izin", "sakit", "alpa") or not student_id.isdigit():
        abort(400)
    if not q("SELECT 1 FROM enrollments WHERE class_id = ? AND student_id = ? AND status = 'aktif'", (cid, student_id), one=True):
        abort(404)
    run("INSERT INTO attendances (session_id, student_id, attendance_status, recorded_at) VALUES (?,?,?,?) "
        "ON CONFLICT(session_id, student_id) DO UPDATE SET attendance_status = excluded.attendance_status, recorded_at = excluded.recorded_at",
        (sid, int(student_id), status, now()))
    flash("Kehadiran diperbarui.", "success")
    return redirect(url_for("lecturer.session_detail", cid=cid, sid=sid))


@bp.route("/kelas/<int:cid>/absensi/<int:sid>/hapus", methods=["POST"])
@perm_required("attendance.manage")
def session_delete(cid, sid):
    _cls(cid)
    s = _session(cid, sid)
    run("DELETE FROM class_sessions WHERE session_id = ?", (sid,))
    audit.log("attendance.session", "session", sid, "Menghapus sesi: %s" % s["topic"])
    flash("Sesi dihapus.", "success")
    return redirect(url_for("lecturer.attendance", cid=cid))


@bp.route("/kelas/<int:cid>/absensi/laporan")
@perm_required("attendance.manage")
def attendance_report(cid):
    cls = _cls(cid)
    sessions = q("SELECT * FROM class_sessions WHERE class_id = ? AND (attendance_open = 1 OR session_date <= ?) ORDER BY session_date",
                 (cid, now()))
    students = services.class_students(cid)
    recs = {}
    for r in q("SELECT a.session_id, a.student_id, a.attendance_status FROM attendances a "
               "JOIN class_sessions s ON s.session_id = a.session_id WHERE s.class_id = ?", (cid,)):
        recs[(r["session_id"], r["student_id"])] = r["attendance_status"]
    table = []
    for st in students:
        cells = [recs.get((s["session_id"], st["user_id"])) for s in sessions]
        present = sum(1 for c in cells if c == "hadir")
        table.append({"st": st, "cells": cells, "present": present,
                      "pct": (present / len(sessions) * 100) if sessions else None})
    return _page("lecturer/attendance_report.html", cls, "absensi", sessions=sessions, table=table)


# ------------------------------------------------------------------ progres


@bp.route("/kelas/<int:cid>/progres")
@perm_required("progress.view")
def progress(cid):
    cls = _cls(cid)
    rows = []
    for st in services.class_students(cid):
        s = services.student_summary(st["user_id"], cid, st["enrollment_id"])
        graded = [services.pct(a["score"], a["max_score"]) for a in s["assignments"] if a["score"] is not None]
        quizzes = [services.pct(z["best"], z["max_score"]) for z in s["quizzes"] if z["best"] is not None]
        rows.append({
            "st": st, "s": s,
            "assign_done": sum(1 for a in s["assignments"] if a["submission_id"]),
            "assign_avg": (sum(graded) / len(graded)) if graded else None,
            "quiz_avg": (sum(quizzes) / len(quizzes)) if quizzes else None,
        })
    n_assign = scalar("SELECT COUNT(*) FROM assignments WHERE class_id = ?", (cid,))
    avgs = [r["s"]["avg"] for r in rows if r["s"]["avg"] is not None]
    return _page("lecturer/progress.html", cls, "progres", rows=rows, n_assign=n_assign,
                 class_avg=(sum(avgs) / len(avgs)) if avgs else None)
