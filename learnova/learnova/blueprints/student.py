"""Modul Mahasiswa: kelas, materi, tugas, kuis, absensi, progres & nilai."""
from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import audit, auth, files, services, settings
from ..auth import ROLE_MAHASISWA, has_perm
from ..db import now, q, run, scalar
from ..ui import is_past, parse_dt

bp = Blueprint("student", __name__)


@bp.before_request
def _guard():
    if g.user is None:
        return redirect(url_for("auth.login", next=request.path))
    if g.user["role_name"] != ROLE_MAHASISWA or not has_perm("learn.access"):
        abort(403)


def _uid():
    return g.user["user_id"]


def _page(template, cls, tab, **kw):
    counts = {
        "materi": scalar("SELECT COUNT(*) FROM materials WHERE class_id = ?", (cls["class_id"],)),
        "tugas": scalar("SELECT COUNT(*) FROM assignments WHERE class_id = ?", (cls["class_id"],)),
        "kuis": scalar("SELECT COUNT(*) FROM quizzes WHERE class_id = ?", (cls["class_id"],)),
        "absensi": scalar("SELECT COUNT(*) FROM class_sessions WHERE class_id = ? AND attendance_open = 1", (cls["class_id"],)),
    }
    return render_template(template, cls=cls, tab=tab, counts=counts, base="student", **kw)


def _require_open(cls, back):
    if cls["status"] != "aktif":
        flash("Kelas ini sudah tidak aktif, jadi tidak bisa menerima perubahan.", "warning")
        return redirect(back)
    return None


# ------------------------------------------------------------------ beranda


@bp.route("/")
def dashboard():
    uid = _uid()
    rows = q(
        "SELECT c.*, co.course_name, co.course_code, u.full_name AS lecturer_name, e.enrollment_id "
        "FROM enrollments e JOIN classes c ON c.class_id = e.class_id "
        "JOIN courses co ON co.course_id = c.course_id JOIN users u ON u.user_id = c.lecturer_id "
        "WHERE e.student_id = ? AND e.status = 'aktif' AND c.status = 'aktif' ORDER BY co.course_name",
        (uid,))
    classes, todo, open_sessions = [], [], []
    att_total = att_present = 0
    max_att = services.max_attempts()
    for c in rows:
        s = services.student_summary(uid, c["class_id"], c["enrollment_id"])
        classes.append({"c": c, "s": s})
        att_total += s["sessions_total"]
        att_present += s["present"]
        for a in s["assignments"]:
            if a["submission_id"] is None and not is_past(a["deadline"]):
                todo.append({"kind": "tugas", "title": a["title"], "class": c["class_name"], "when": a["deadline"],
                             "url": url_for("student.assignment", cid=c["class_id"], aid=a["assignment_id"])})
        for z in s["quizzes"]:
            if not is_past(z["deadline"]) and z["attempts"] < max_att:
                todo.append({"kind": "kuis", "title": z["title"], "class": c["class_name"], "when": z["deadline"],
                             "url": url_for("student.quiz", cid=c["class_id"], qid=z["quiz_id"])})
        for sess in q("SELECT * FROM class_sessions WHERE class_id = ? AND attendance_open = 1", (c["class_id"],)):
            mine = q("SELECT 1 FROM attendances WHERE session_id = ? AND student_id = ?", (sess["session_id"], uid), one=True)
            if not mine:
                open_sessions.append({"sess": sess, "class": c})
    todo.sort(key=lambda x: x["when"])
    stats = {
        "classes": len(classes),
        "todo": len(todo),
        "att": round(att_present / att_total * 100) if att_total else None,
        "avg": None,
    }
    avgs = [x["s"]["avg"] for x in classes if x["s"]["avg"] is not None]
    if avgs:
        stats["avg"] = round(sum(avgs) / len(avgs), 1)
    return render_template("student/dashboard.html", classes=classes, todo=todo[:6], open_sessions=open_sessions, stats=stats)


# ------------------------------------------------------------------ kelas


@bp.route("/kelas")
def classes():
    uid = _uid()
    mine = q(
        "SELECT c.*, co.course_name, co.course_code, u.full_name AS lecturer_name, e.enrollment_id "
        "FROM enrollments e JOIN classes c ON c.class_id = e.class_id "
        "JOIN courses co ON co.course_id = c.course_id JOIN users u ON u.user_id = c.lecturer_id "
        "WHERE e.student_id = ? AND e.status = 'aktif' ORDER BY (c.status = 'aktif') DESC, co.course_name", (uid,))
    cards = [{"c": c, "s": services.student_summary(uid, c["class_id"], c["enrollment_id"])} for c in mine]
    available = q(
        "SELECT c.*, co.course_name, co.course_code, u.full_name AS lecturer_name, "
        "(SELECT COUNT(*) FROM enrollments WHERE class_id = c.class_id AND status = 'aktif') AS students "
        "FROM classes c JOIN courses co ON co.course_id = c.course_id JOIN users u ON u.user_id = c.lecturer_id "
        "WHERE c.status = 'aktif' AND c.class_id NOT IN (SELECT class_id FROM enrollments WHERE student_id = ? AND status = 'aktif') "
        "ORDER BY co.course_name, c.class_name", (uid,))
    return render_template("student/classes.html", cards=cards, available=available)


@bp.route("/kelas/<int:cid>/gabung", methods=["POST"])
def join(cid):
    cls = services.class_row(cid)
    if cls is None:
        abort(404)
    if cls["status"] != "aktif":
        flash("Kelas ini tidak menerima peserta baru.", "warning")
        return redirect(url_for("student.classes"))
    old = q("SELECT * FROM enrollments WHERE class_id = ? AND student_id = ?", (cid, _uid()), one=True)
    if old and old["status"] == "aktif":
        return redirect(url_for("student.class_home", cid=cid))
    if old:
        run("UPDATE enrollments SET status = 'aktif', enrolled_at = ? WHERE enrollment_id = ?", (now(), old["enrollment_id"]))
    else:
        run("INSERT INTO enrollments (class_id, student_id, enrolled_at, status) VALUES (?,?,?,'aktif')", (cid, _uid(), now()))
    audit.log("class.enroll", "class", cid, "Mahasiswa bergabung ke %s" % cls["class_name"])
    flash("Anda bergabung ke %s." % cls["class_name"], "success")
    return redirect(url_for("student.class_home", cid=cid))


@bp.route("/kelas/<int:cid>")
def class_home(cid):
    cls, enr = services.student_class(_uid(), cid)
    s = services.student_summary(_uid(), cid, enr["enrollment_id"])
    open_sessions = [x for x in q("SELECT * FROM class_sessions WHERE class_id = ? AND attendance_open = 1", (cid,))
                     if not q("SELECT 1 FROM attendances WHERE session_id = ? AND student_id = ?", (x["session_id"], _uid()), one=True)]
    pending_tasks = [a for a in s["assignments"] if a["submission_id"] is None and not is_past(a["deadline"])]
    max_att = services.max_attempts()
    avail_quiz = [z for z in s["quizzes"] if not is_past(z["deadline"]) and z["attempts"] < max_att]
    return _page("student/class_home.html", cls, "ringkasan", s=s, open_sessions=open_sessions,
                 pending_tasks=pending_tasks, avail_quiz=avail_quiz)


# ------------------------------------------------------------------ materi


@bp.route("/kelas/<int:cid>/materi")
def materials(cid):
    cls, enr = services.student_class(_uid(), cid)
    mats = services.materials_for_student(enr["enrollment_id"], cid)
    done = sum(1 for m in mats if m["done"])
    return _page("student/materials.html", cls, "materi", mats=mats, done=done)


@bp.route("/kelas/<int:cid>/materi/<int:mid>")
def material(cid, mid):
    cls, enr = services.student_class(_uid(), cid)
    mats = services.materials_for_student(enr["enrollment_id"], cid)
    m = next((x for x in mats if x["material_id"] == mid), None)
    if m is None:
        abort(404)
    return _page("student/material.html", cls, "materi", m=m)


@bp.route("/kelas/<int:cid>/materi/<int:mid>/selesai", methods=["POST"])
def material_done(cid, mid):
    cls, enr = services.student_class(_uid(), cid)
    back = url_for("student.material", cid=cid, mid=mid)
    stop = _require_open(cls, back)
    if stop:
        return stop
    mats = services.materials_for_student(enr["enrollment_id"], cid)
    m = next((x for x in mats if x["material_id"] == mid), None)
    if m is None:
        abort(404)
    if m["locked"]:
        flash("Selesaikan materi prasyarat terlebih dahulu.", "warning")
        return redirect(back)
    run("INSERT INTO material_progress (enrollment_id, material_id, is_completed, completed_at) VALUES (?,?,1,?) "
        "ON CONFLICT(enrollment_id, material_id) DO UPDATE SET is_completed = 1, completed_at = excluded.completed_at",
        (enr["enrollment_id"], mid, now()))
    flash("Materi ditandai selesai. Progres belajar diperbarui.", "success")
    nxt = next((x for x in mats if x["sequence_no"] > m["sequence_no"]), None)
    return redirect(url_for("student.material", cid=cid, mid=nxt["material_id"]) if nxt and nxt["prerequisite_id"] == mid else back)


# ------------------------------------------------------------------ tugas


def _assignment_state(a, sub):
    if sub:
        return sub["status"]
    return "terlambat" if is_past(a["deadline"]) else "belum"


@bp.route("/kelas/<int:cid>/tugas")
def assignments(cid):
    cls, enr = services.student_class(_uid(), cid)
    s = services.student_summary(_uid(), cid, enr["enrollment_id"])
    items = []
    for a in s["assignments"]:
        state = a["sub_status"] if a["submission_id"] else ("terlambat" if is_past(a["deadline"]) else "belum")
        items.append({"a": a, "state": state})
    return _page("student/assignments.html", cls, "tugas", items=items)


@bp.route("/kelas/<int:cid>/tugas/<int:aid>")
def assignment(cid, aid):
    cls, enr = services.student_class(_uid(), cid)
    a = q("SELECT * FROM assignments WHERE assignment_id = ? AND class_id = ?", (aid, cid), one=True)
    if a is None:
        abort(404)
    sub = q("SELECT * FROM submissions WHERE assignment_id = ? AND student_id = ?", (aid, _uid()), one=True)
    late = is_past(a["deadline"])
    state = _assignment_state(a, sub)
    can_submit = (not late) and cls["status"] == "aktif" and not (sub and sub["status"] == "dinilai")
    return _page("student/assignment.html", cls, "tugas", a=a, sub=sub, late=late, state=state, can_submit=can_submit,
                 max_mb=settings.get_int("max_upload_mb"))


@bp.route("/kelas/<int:cid>/tugas/<int:aid>/kumpul", methods=["POST"])
def submit_assignment(cid, aid):
    cls, enr = services.student_class(_uid(), cid)
    back = url_for("student.assignment", cid=cid, aid=aid)
    stop = _require_open(cls, back)
    if stop:
        return stop
    a = q("SELECT * FROM assignments WHERE assignment_id = ? AND class_id = ?", (aid, cid), one=True)
    if a is None:
        abort(404)
    if is_past(a["deadline"]):
        flash("Tenggat sudah lewat, pengumpulan ditutup.", "error")
        return redirect(back)
    sub = q("SELECT * FROM submissions WHERE assignment_id = ? AND student_id = ?", (aid, _uid()), one=True)
    if sub and sub["status"] == "dinilai":
        flash("Tugas ini sudah dinilai dan tidak bisa diganti.", "warning")
        return redirect(back)
    try:
        path = files.save_upload(request.files.get("file"), "submissions")
    except files.UploadError as exc:
        flash(str(exc), "error")
        return redirect(back)
    if sub:
        files.delete_file(sub["file_url"])
        run("UPDATE submissions SET file_url = ?, submitted_at = ?, status = 'dikumpulkan' WHERE submission_id = ?",
            (path, now(), sub["submission_id"]))
    else:
        run("INSERT INTO submissions (assignment_id, student_id, file_url, submitted_at, status) VALUES (?,?,?,?, 'dikumpulkan')",
            (aid, _uid(), path, now()))
    flash("Tugas terkirim. Status pengumpulan tersimpan.", "success")
    return redirect(back)


# ------------------------------------------------------------------ kuis


@bp.route("/kelas/<int:cid>/kuis")
def quizzes(cid):
    cls, enr = services.student_class(_uid(), cid)
    s = services.student_summary(_uid(), cid, enr["enrollment_id"])
    return _page("student/quizzes.html", cls, "kuis", items=s["quizzes"], max_att=services.max_attempts(),
                 open_attempts={z["quiz_id"]: services.open_attempt(_uid(), z["quiz_id"]) for z in s["quizzes"]})


def _quiz_or_404(cid, qid):
    quiz = q("SELECT * FROM quizzes WHERE quiz_id = ? AND class_id = ?", (qid, cid), one=True)
    if quiz is None:
        abort(404)
    return quiz


@bp.route("/kelas/<int:cid>/kuis/<int:qid>")
def quiz(cid, qid):
    cls, enr = services.student_class(_uid(), cid)
    quiz = _quiz_or_404(cid, qid)
    attempts = q("SELECT * FROM quiz_attempts WHERE quiz_id = ? AND student_id = ? ORDER BY attempt_no", (qid, _uid()))
    done = [a for a in attempts if a["submitted_at"]]
    open_att = services.open_attempt(_uid(), qid)
    n_questions = scalar("SELECT COUNT(*) FROM quiz_questions WHERE quiz_id = ?", (qid,))
    max_att = services.max_attempts()
    reason = None
    if cls["status"] != "aktif":
        reason = "Kelas sudah tidak aktif."
    elif n_questions == 0:
        reason = "Kuis ini belum memiliki soal."
    elif is_past(quiz["deadline"]) and not open_att:
        reason = "Tenggat kuis sudah lewat."
    elif len(done) >= max_att and not open_att:
        reason = "Batas percobaan (%d kali) sudah tercapai." % max_att
    return _page("student/quiz.html", cls, "kuis", quiz=quiz, attempts=attempts, open_att=open_att,
                 n_questions=n_questions, max_att=max_att, reason=reason,
                 total_points=scalar("SELECT SUM(points) FROM quiz_questions WHERE quiz_id = ?", (qid,)))


@bp.route("/kelas/<int:cid>/kuis/<int:qid>/mulai", methods=["POST"])
def quiz_start(cid, qid):
    cls, enr = services.student_class(_uid(), cid)
    quiz = _quiz_or_404(cid, qid)
    back = url_for("student.quiz", cid=cid, qid=qid)
    stop = _require_open(cls, back)
    if stop:
        return stop
    open_att = services.open_attempt(_uid(), qid)
    if open_att:
        return redirect(url_for("student.quiz_attempt", cid=cid, qid=qid, aid=open_att["attempt_id"]))
    if is_past(quiz["deadline"]):
        flash("Tenggat kuis sudah lewat.", "error")
        return redirect(back)
    used = scalar("SELECT COUNT(*) FROM quiz_attempts WHERE quiz_id = ? AND student_id = ?", (qid, _uid()))
    if used >= services.max_attempts():
        flash("Batas percobaan sudah tercapai.", "error")
        return redirect(back)
    if scalar("SELECT COUNT(*) FROM quiz_questions WHERE quiz_id = ?", (qid,)) == 0:
        flash("Kuis ini belum memiliki soal.", "error")
        return redirect(back)
    aid = run("INSERT INTO quiz_attempts (quiz_id, student_id, started_at, attempt_no) VALUES (?,?,?,?)",
              (qid, _uid(), now(), used + 1))
    return redirect(url_for("student.quiz_attempt", cid=cid, qid=qid, aid=aid))


@bp.route("/kelas/<int:cid>/kuis/<int:qid>/percobaan/<int:aid>", methods=["GET", "POST"])
def quiz_attempt(cid, qid, aid):
    cls, enr = services.student_class(_uid(), cid)
    quiz = _quiz_or_404(cid, qid)
    att = q("SELECT * FROM quiz_attempts WHERE attempt_id = ? AND quiz_id = ? AND student_id = ?", (aid, qid, _uid()), one=True)
    if att is None:
        abort(404)
    result_url = url_for("student.quiz_result", cid=cid, qid=qid, aid=aid)
    if att["submitted_at"]:
        return redirect(result_url)
    end = services.attempt_deadline(att, quiz)
    grace = 90  # detik toleransi jaringan
    if request.method == "POST":
        expired = (datetime.now() - end).total_seconds() > grace
        services.finalize_attempt(att, quiz, request.form, timed_out=expired)
        flash("Waktu pengerjaan sudah habis, jawaban tidak dapat disimpan." if expired else "Jawaban terkirim dan hasil kuis disimpan.",
              "warning" if expired else "success")
        return redirect(result_url)
    if datetime.now() >= end:
        services.finalize_attempt(att, quiz, {}, timed_out=True)
        flash("Waktu kuis habis sebelum jawaban dikirim.", "warning")
        return redirect(result_url)
    questions = q("SELECT * FROM quiz_questions WHERE quiz_id = ? ORDER BY question_id", (qid,))
    options = {}
    for o in q("SELECT o.* FROM quiz_options o JOIN quiz_questions z ON z.question_id = o.question_id "
               "WHERE z.quiz_id = ? ORDER BY o.option_id", (qid,)):
        options.setdefault(o["question_id"], []).append(o)
    return _page("student/quiz_take.html", cls, "kuis", quiz=quiz, att=att, questions=questions, options=options,
                 end_ts=int(end.timestamp()), now_ts=int(datetime.now().timestamp()))


@bp.route("/kelas/<int:cid>/kuis/<int:qid>/percobaan/<int:aid>/hasil")
def quiz_result(cid, qid, aid):
    cls, enr = services.student_class(_uid(), cid)
    quiz = _quiz_or_404(cid, qid)
    att = q("SELECT * FROM quiz_attempts WHERE attempt_id = ? AND quiz_id = ? AND student_id = ?", (aid, qid, _uid()), one=True)
    if att is None:
        abort(404)
    if not att["submitted_at"]:
        return redirect(url_for("student.quiz_attempt", cid=cid, qid=qid, aid=aid))
    questions = q("SELECT * FROM quiz_questions WHERE quiz_id = ? ORDER BY question_id", (qid,))
    answers = {a["question_id"]: a for a in q("SELECT * FROM quiz_answers WHERE attempt_id = ?", (aid,))}
    options = {}
    for o in q("SELECT o.* FROM quiz_options o JOIN quiz_questions z ON z.question_id = o.question_id "
               "WHERE z.quiz_id = ? ORDER BY o.option_id", (qid,)):
        options.setdefault(o["question_id"], []).append(o)
    pending = any(a["score_awarded"] is None for a in answers.values())
    return _page("student/quiz_result.html", cls, "kuis", quiz=quiz, att=att, questions=questions, answers=answers,
                 options=options, pending=pending, pct=services.pct(att["score"], quiz["max_score"]))


# ------------------------------------------------------------------ absensi


@bp.route("/kelas/<int:cid>/absensi")
def attendance(cid):
    cls, enr = services.student_class(_uid(), cid)
    rows = q("SELECT s.*, a.attendance_status, a.recorded_at FROM class_sessions s "
             "LEFT JOIN attendances a ON a.session_id = s.session_id AND a.student_id = ? "
             "WHERE s.class_id = ? ORDER BY s.session_date DESC", (_uid(), cid))
    sessions = []
    for r in rows:
        if r["attendance_status"]:
            state = r["attendance_status"]
        elif r["attendance_open"]:
            state = "terbuka"
        elif is_past(r["session_date"]):
            state = "alpa"
        else:
            state = "tertutup"
        sessions.append({"s": r, "state": state})
    s = services.student_summary(_uid(), cid, enr["enrollment_id"])
    return _page("student/attendance.html", cls, "absensi", sessions=sessions, summary=s)


@bp.route("/kelas/<int:cid>/absensi/<int:sid>", methods=["POST"])
def check_in(cid, sid):
    cls, enr = services.student_class(_uid(), cid)
    back = url_for("student.attendance", cid=cid)
    stop = _require_open(cls, back)
    if stop:
        return stop
    sess = q("SELECT * FROM class_sessions WHERE session_id = ? AND class_id = ?", (sid, cid), one=True)
    if sess is None:
        abort(404)
    if not sess["attendance_open"]:
        flash("Absensi untuk sesi ini belum dibuka atau sudah ditutup.", "warning")
        return redirect(back)
    status = request.form.get("status", "hadir")
    if status not in ("hadir", "izin", "sakit"):
        status = "hadir"
    run("INSERT INTO attendances (session_id, student_id, attendance_status, recorded_at) VALUES (?,?,?,?) "
        "ON CONFLICT(session_id, student_id) DO UPDATE SET attendance_status = excluded.attendance_status, recorded_at = excluded.recorded_at",
        (sid, _uid(), status, now()))
    flash("Kehadiran tersimpan: %s." % status, "success")
    return redirect(back)


# ------------------------------------------------------------------ progres & nilai


@bp.route("/kelas/<int:cid>/progres")
def progress(cid):
    cls, enr = services.student_class(_uid(), cid)
    s = services.student_summary(_uid(), cid, enr["enrollment_id"])
    mats = services.materials_for_student(enr["enrollment_id"], cid)
    return _page("student/progress.html", cls, "progres", s=s, mats=mats, pct=services.pct)
