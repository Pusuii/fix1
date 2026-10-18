"""Logika bisnis yang dipakai bersama oleh beberapa peran."""
import math
from datetime import datetime, timedelta

from flask import abort, request

from . import settings
from .db import now, q, run, scalar
from .ui import parse_dt

# ------------------------------------------------------------------ paginasi


def paginate(sql, args=(), per_page=15):
    page = request.args.get("page", 1, type=int) or 1
    total = scalar("SELECT COUNT(*) FROM (%s)" % sql, args)
    pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, pages))
    rows = q("%s LIMIT ? OFFSET ?" % sql, tuple(args) + (per_page, (page - 1) * per_page))
    return rows, {"page": page, "pages": pages, "total": total, "per_page": per_page}


# ------------------------------------------------------------------ kelas


def class_row(class_id):
    return q(
        "SELECT c.*, co.course_name, co.course_code, u.full_name AS lecturer_name "
        "FROM classes c JOIN courses co ON co.course_id = c.course_id "
        "JOIN users u ON u.user_id = c.lecturer_id WHERE c.class_id = ?",
        (class_id,), one=True,
    )


def student_class(user_id, class_id):
    """Kelas + enrollment aktif untuk mahasiswa, atau 404/403."""
    cls = class_row(class_id)
    if cls is None:
        abort(404)
    enr = q(
        "SELECT * FROM enrollments WHERE class_id = ? AND student_id = ? AND status = 'aktif'",
        (class_id, user_id), one=True,
    )
    if enr is None:
        abort(403)
    return cls, enr


def lecturer_class(user_id, class_id):
    cls = class_row(class_id)
    if cls is None:
        abort(404)
    if cls["lecturer_id"] != user_id:
        abort(403)
    return cls


def class_active_or_abort(cls):
    return cls["status"] == "aktif"


def class_students(class_id):
    return q(
        "SELECT u.user_id, u.full_name, u.email, e.enrollment_id, e.enrolled_at "
        "FROM enrollments e JOIN users u ON u.user_id = e.student_id "
        "WHERE e.class_id = ? AND e.status = 'aktif' ORDER BY u.full_name",
        (class_id,),
    )


# ------------------------------------------------------------------ materi


def materials_for_student(enrollment_id, class_id):
    """Daftar materi + status selesai + terkunci (prasyarat belum terpenuhi)."""
    mats = q(
        "SELECT m.*, p.title AS prereq_title, "
        "COALESCE(mp.is_completed, 0) AS done, mp.completed_at, "
        "COALESCE(pp.is_completed, 0) AS prereq_done "
        "FROM materials m "
        "LEFT JOIN materials p ON p.material_id = m.prerequisite_id "
        "LEFT JOIN material_progress mp ON mp.material_id = m.material_id AND mp.enrollment_id = ? "
        "LEFT JOIN material_progress pp ON pp.material_id = m.prerequisite_id AND pp.enrollment_id = ? "
        "WHERE m.class_id = ? ORDER BY m.sequence_no, m.material_id",
        (enrollment_id, enrollment_id, class_id),
    )
    out = []
    for m in mats:
        d = dict(m)
        d["locked"] = bool(m["prerequisite_id"]) and not m["prereq_done"]
        out.append(d)
    return out


# ------------------------------------------------------------------ nilai & progres


def pct(score, max_score):
    try:
        if score is None or not max_score:
            return None
        return float(score) / float(max_score) * 100.0
    except (TypeError, ValueError):
        return None


def student_summary(student_id, class_id, enrollment_id):
    """Ringkasan belajar seorang mahasiswa di satu kelas."""
    mats_total = scalar("SELECT COUNT(*) FROM materials WHERE class_id = ?", (class_id,))
    mats_done = scalar(
        "SELECT COUNT(*) FROM material_progress mp JOIN materials m ON m.material_id = mp.material_id "
        "WHERE mp.enrollment_id = ? AND mp.is_completed = 1 AND m.class_id = ?",
        (enrollment_id, class_id),
    )
    assignments = q(
        "SELECT a.*, s.submission_id, s.score, s.feedback, s.status AS sub_status, s.submitted_at, s.file_url "
        "FROM assignments a LEFT JOIN submissions s "
        "ON s.assignment_id = a.assignment_id AND s.student_id = ? "
        "WHERE a.class_id = ? ORDER BY a.deadline",
        (student_id, class_id),
    )
    quizzes = q(
        "SELECT z.*, "
        "(SELECT MAX(score) FROM quiz_attempts t WHERE t.quiz_id = z.quiz_id AND t.student_id = ? AND t.submitted_at IS NOT NULL) AS best, "
        "(SELECT COUNT(*) FROM quiz_attempts t WHERE t.quiz_id = z.quiz_id AND t.student_id = ? AND t.submitted_at IS NOT NULL) AS attempts, "
        "(SELECT COUNT(*) FROM quiz_attempts t WHERE t.quiz_id = z.quiz_id AND t.student_id = ? AND t.submitted_at IS NOT NULL AND t.score IS NULL) AS pending "
        "FROM quizzes z WHERE z.class_id = ? ORDER BY z.deadline",
        (student_id, student_id, student_id, class_id),
    )
    sessions_total = scalar(
        "SELECT COUNT(*) FROM class_sessions WHERE class_id = ? AND (attendance_open = 1 OR session_date <= ?)",
        (class_id, now()),
    )
    att = {r["attendance_status"]: r["n"] for r in q(
        "SELECT a.attendance_status, COUNT(*) AS n FROM attendances a "
        "JOIN class_sessions s ON s.session_id = a.session_id "
        "WHERE s.class_id = ? AND a.student_id = ? GROUP BY a.attendance_status",
        (class_id, student_id),
    )}
    present = att.get("hadir", 0)
    att_pct = (present / sessions_total * 100.0) if sessions_total else None

    scores = []
    for a in assignments:
        if a["score"] is not None:
            scores.append(pct(a["score"], a["max_score"]))
    for z in quizzes:
        if z["best"] is not None:
            scores.append(pct(z["best"], z["max_score"]))
    scores = [s for s in scores if s is not None]
    avg = sum(scores) / len(scores) if scores else None
    mat_pct = (mats_done / mats_total * 100.0) if mats_total else 0.0
    return {
        "mats_total": mats_total, "mats_done": mats_done, "mat_pct": mat_pct,
        "assignments": assignments, "quizzes": quizzes,
        "sessions_total": sessions_total, "attendance": att, "present": present, "att_pct": att_pct,
        "avg": avg,
    }


# ------------------------------------------------------------------ kuis


def recompute_attempt(attempt_id, mark_submitted=False):
    """Hitung ulang skor percobaan kuis dari jawaban. Skor NULL bila esai belum dinilai."""
    attempt = q("SELECT * FROM quiz_attempts WHERE attempt_id = ?", (attempt_id,), one=True)
    quiz = q("SELECT * FROM quizzes WHERE quiz_id = ?", (attempt["quiz_id"],), one=True)
    total_points = scalar("SELECT SUM(points) FROM quiz_questions WHERE quiz_id = ?", (quiz["quiz_id"],))
    answers = q("SELECT * FROM quiz_answers WHERE attempt_id = ?", (attempt_id,))
    pending = any(a["score_awarded"] is None for a in answers)
    earned = sum(a["score_awarded"] or 0 for a in answers)
    score = None
    if not pending:
        score = round(earned / total_points * quiz["max_score"], 2) if total_points else 0.0
    submitted = now() if mark_submitted else attempt["submitted_at"]
    run("UPDATE quiz_attempts SET score = ?, submitted_at = ? WHERE attempt_id = ?", (score, submitted, attempt_id))
    return score


def attempt_deadline(attempt, quiz):
    started = parse_dt(attempt["started_at"])
    return started + timedelta(minutes=quiz["duration_minutes"])


def finalize_attempt(attempt, quiz, form, timed_out=False):
    """Simpan jawaban & nilai otomatis untuk pilihan ganda."""
    questions = q("SELECT * FROM quiz_questions WHERE quiz_id = ? ORDER BY question_id", (quiz["quiz_id"],))
    run("DELETE FROM quiz_answers WHERE attempt_id = ?", (attempt["attempt_id"],))
    for qu in questions:
        key = "q%d" % qu["question_id"]
        raw = "" if timed_out else (form.get(key) or "").strip()
        if qu["question_type"] == "pilihan_ganda":
            opt = None
            if raw.isdigit():
                opt = q("SELECT * FROM quiz_options WHERE option_id = ? AND question_id = ?",
                        (int(raw), qu["question_id"]), one=True)
            correct = 1 if (opt and opt["is_correct"]) else 0
            run("INSERT INTO quiz_answers (attempt_id, question_id, selected_option_id, is_correct, score_awarded) "
                "VALUES (?,?,?,?,?)",
                (attempt["attempt_id"], qu["question_id"], opt["option_id"] if opt else None,
                 correct, qu["points"] if correct else 0))
        else:
            pending = None if raw else 0
            run("INSERT INTO quiz_answers (attempt_id, question_id, answer_text, score_awarded) VALUES (?,?,?,?)",
                (attempt["attempt_id"], qu["question_id"], raw, pending))
    return recompute_attempt(attempt["attempt_id"], mark_submitted=True)


def open_attempt(student_id, quiz_id):
    return q("SELECT * FROM quiz_attempts WHERE quiz_id = ? AND student_id = ? AND submitted_at IS NULL "
             "ORDER BY attempt_id DESC", (quiz_id, student_id), one=True)


def max_attempts():
    return settings.get_int("quiz_max_attempts")


# ------------------------------------------------------------------ statistik


def platform_counts():
    return {
        "students": scalar("SELECT COUNT(*) FROM users u JOIN roles r ON r.role_id=u.role_id WHERE r.role_name='mahasiswa'"),
        "lecturers": scalar("SELECT COUNT(*) FROM users u JOIN roles r ON r.role_id=u.role_id WHERE r.role_name='dosen'"),
        "admins": scalar("SELECT COUNT(*) FROM users u JOIN roles r ON r.role_id=u.role_id WHERE r.role_name IN ('admin','super_admin')"),
        "active_users": scalar("SELECT COUNT(*) FROM users WHERE account_status='aktif'"),
        "pending": scalar("SELECT COUNT(*) FROM users WHERE account_status='pending'"),
        "courses": scalar("SELECT COUNT(*) FROM courses"),
        "classes": scalar("SELECT COUNT(*) FROM classes WHERE status='aktif'"),
        "enrollments": scalar("SELECT COUNT(*) FROM enrollments WHERE status='aktif'"),
        "materials": scalar("SELECT COUNT(*) FROM materials"),
        "assignments": scalar("SELECT COUNT(*) FROM assignments"),
        "submissions": scalar("SELECT COUNT(*) FROM submissions"),
        "quizzes": scalar("SELECT COUNT(*) FROM quizzes"),
        "attempts": scalar("SELECT COUNT(*) FROM quiz_attempts WHERE submitted_at IS NOT NULL"),
        "reports_open": scalar("SELECT COUNT(*) FROM user_reports WHERE status != 'selesai'"),
    }


def daily_series(sql_table_col, days=14, extra_where=""):
    """Jumlah baris per hari untuk N hari terakhir. sql_table_col: 'tabel.kolom_datetime'."""
    table, col = sql_table_col.split(".")
    start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = q("SELECT substr(%s,1,10) AS d, COUNT(*) AS n FROM %s WHERE substr(%s,1,10) >= ? %s GROUP BY d"
             % (col, table, col, extra_where), (start,))
    got = {r["d"]: r["n"] for r in rows}
    series = []
    for i in range(days):
        day = datetime.now() - timedelta(days=days - 1 - i)
        key = day.strftime("%Y-%m-%d")
        series.append({"date": key, "label": "%d/%d" % (day.day, day.month), "n": got.get(key, 0)})
    return series
