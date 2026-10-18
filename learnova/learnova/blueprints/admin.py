"""Modul Admin: akun, verifikasi, mata kuliah & kelas, laporan, aktivitas platform."""
import os
import platform
import sys
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .. import accounts as acc
from .. import audit, services, settings
from ..auth import ROLE_ADMIN, ROLE_DOSEN, ROLE_MAHASISWA, perm_required
from ..db import now, q, run, scalar

bp = Blueprint("admin", __name__)

SEMESTERS = [("Ganjil", "Ganjil"), ("Genap", "Genap"), ("Pendek", "Pendek")]
CLASS_STATUS = [("aktif", "Aktif"), ("nonaktif", "Nonaktif"), ("selesai", "Selesai")]
REPORT_STATUS = [("baru", "Baru"), ("diproses", "Diproses"), ("selesai", "Selesai")]
MANAGED = (ROLE_MAHASISWA, ROLE_DOSEN)


@bp.before_request
def _guard():
    if g.user is None:
        return redirect(url_for("auth.login", next=request.path))
    if g.user["role_name"] != ROLE_ADMIN:
        abort(403)


# ------------------------------------------------------------------ beranda


@bp.route("/")
def dashboard():
    counts = services.platform_counts()
    pending = q("SELECT u.*, r.role_name FROM users u JOIN roles r ON r.role_id = u.role_id "
                "WHERE u.account_status = 'pending' ORDER BY u.created_at DESC LIMIT 6")
    reports = q("SELECT r.*, u.full_name FROM user_reports r JOIN users u ON u.user_id = r.reporter_id "
                "WHERE r.status != 'selesai' ORDER BY r.report_id DESC LIMIT 5")
    recent = q("SELECT l.*, u.full_name FROM audit_logs l LEFT JOIN users u ON u.user_id = l.actor_user_id "
               "WHERE l.action NOT IN ('auth.login','auth.logout') ORDER BY l.log_id DESC LIMIT 6")
    return render_template("admin/dashboard.html", counts=counts, pending=pending, reports=reports, recent=recent,
                           label=audit.label)


# ------------------------------------------------------------------ akun


def _target(uid):
    u = q("SELECT u.*, r.role_name FROM users u JOIN roles r ON r.role_id = u.role_id WHERE u.user_id = ?", (uid,), one=True)
    if u is None or u["role_name"] not in MANAGED:
        abort(404)
    return u


@bp.route("/akun")
@perm_required("account.manage")
def accounts():
    role = request.args.get("role", "")
    status = request.args.get("status", "")
    term = (request.args.get("q") or "").strip()
    sql = ("SELECT u.*, r.role_name FROM users u JOIN roles r ON r.role_id = u.role_id "
           "WHERE r.role_name IN ('mahasiswa','dosen')")
    args = []
    if role in MANAGED:
        sql += " AND r.role_name = ?"
        args.append(role)
    if status in [s[0] for s in accounts_statuses()]:
        sql += " AND u.account_status = ?"
        args.append(status)
    if term:
        sql += " AND (u.full_name LIKE ? OR u.email LIKE ?)"
        args += ["%" + term + "%"] * 2
    sql += " ORDER BY (u.account_status = 'pending') DESC, u.created_at DESC"
    rows, pager = services.paginate(sql, args, 12)
    return render_template("admin/accounts.html", rows=rows, pager=pager, role=role, status=status, term=term,
                           statuses=accounts_statuses(), pending=scalar("SELECT COUNT(*) FROM users WHERE account_status='pending'"))


def accounts_statuses():
    return acc.STATUSES


@bp.route("/akun/baru", methods=["GET", "POST"])
@perm_required("account.manage")
def account_new():
    errors, form = {}, {"status": "aktif", "role": "mahasiswa"}
    if request.method == "POST":
        clean, errors = acc.validate(request.form, None, MANAGED, True)
        form = clean
        if not errors:
            uid = acc.create(clean)
            audit.log("account.create", "user", uid, "%s (%s)" % (clean["email"], clean["role"]))
            flash("Akun dibuat.", "success")
            return redirect(url_for("admin.account_detail", uid=uid))
    return render_template("admin/account_form.html", user=None, form=form, errors=errors, roles=MANAGED,
                           statuses=accounts_statuses(), scope="admin")


@bp.route("/akun/<int:uid>")
@perm_required("account.manage")
def account_detail(uid):
    u = _target(uid)
    if u["role_name"] == ROLE_MAHASISWA:
        classes = q("SELECT c.class_id, c.class_name, c.status, e.enrolled_at FROM enrollments e JOIN classes c ON c.class_id = e.class_id "
                    "WHERE e.student_id = ? AND e.status = 'aktif' ORDER BY c.class_name", (uid,))
    else:
        classes = q("SELECT c.class_id, c.class_name, c.status, c.semester || ' ' || c.academic_year AS enrolled_at FROM classes c "
                    "WHERE c.lecturer_id = ? ORDER BY c.class_name", (uid,))
    logs = q("SELECT * FROM audit_logs WHERE actor_user_id = ? ORDER BY log_id DESC LIMIT 8", (uid,))
    return render_template("admin/account_detail.html", u=u, classes=classes, logs=logs, label=audit.label)


@bp.route("/akun/<int:uid>/ubah", methods=["GET", "POST"])
@perm_required("account.manage")
def account_edit(uid):
    u = _target(uid)
    errors = {}
    form = {"full_name": u["full_name"], "email": u["email"], "role": u["role_name"], "status": u["account_status"]}
    if request.method == "POST":
        clean, errors = acc.validate(request.form, u, MANAGED, False)
        form = clean
        if not errors:
            acc.update(u, clean)
            audit.log("account.update", "user", uid, "Admin mengubah data akun %s" % clean["email"])
            flash("Data akun diperbarui.", "success")
            return redirect(url_for("admin.account_detail", uid=uid))
    return render_template("admin/account_form.html", user=u, form=form, errors=errors, roles=MANAGED,
                           statuses=accounts_statuses(), scope="admin")


@bp.route("/akun/<int:uid>/status", methods=["POST"])
@perm_required("account.manage")
def account_status(uid):
    u = _target(uid)
    status = request.form.get("status")
    if status not in ("aktif", "nonaktif"):
        abort(400)
    run("UPDATE users SET account_status = ? WHERE user_id = ?", (status, uid))
    audit.log("account.status", "user", uid, "%s: %s -> %s" % (u["email"], u["account_status"], status))
    flash("Akun %s %s." % (u["full_name"], "diaktifkan" if status == "aktif" else "dinonaktifkan"), "success")
    return redirect(request.form.get("next") or url_for("admin.accounts"))


@bp.route("/akun/<int:uid>/verifikasi", methods=["POST"])
@perm_required("account.verify")
def account_verify(uid):
    u = _target(uid)
    decision = request.form.get("decision")
    if decision == "aktifkan":
        run("UPDATE users SET account_status = 'aktif' WHERE user_id = ?", (uid,))
        audit.log("account.verify", "user", uid, "Akun %s diaktifkan" % u["email"])
        flash("Akun %s diaktifkan. Pengguna sekarang bisa masuk." % u["full_name"], "success")
    elif decision == "tolak":
        reason = (request.form.get("reason") or "").strip()
        if len(reason) < 5:
            flash("Tulis alasan penolakan atau perbaikan data yang diminta (minimal 5 karakter).", "error")
            return redirect(url_for("admin.account_detail", uid=uid))
        run("UPDATE users SET account_status = 'ditolak' WHERE user_id = ?", (uid,))
        audit.log("account.reject", "user", uid, "Akun %s ditolak. Alasan: %s" % (u["email"], reason[:300]))
        flash("Akun %s ditolak." % u["full_name"], "success")
    else:
        abort(400)
    return redirect(request.form.get("next") or url_for("admin.account_detail", uid=uid))


# ------------------------------------------------------------------ mata kuliah


@bp.route("/mata-kuliah")
@perm_required("course.manage")
def courses():
    rows = q("SELECT co.*, (SELECT COUNT(*) FROM classes c WHERE c.course_id = co.course_id) AS classes "
             "FROM courses co ORDER BY co.course_name")
    return render_template("admin/courses.html", rows=rows)


def _course_form(course=None):
    errors, form = {}, {}
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in ("course_name", "course_code", "description")}
        form["course_code"] = form["course_code"].upper()
        if len(form["course_name"]) < 3:
            errors["course_name"] = "Nama mata kuliah minimal 3 karakter."
        if not form["course_code"] or len(form["course_code"]) > 12 or " " in form["course_code"]:
            errors["course_code"] = "Kode wajib diisi tanpa spasi (maksimal 12 karakter)."
        elif q("SELECT 1 FROM courses WHERE course_code = ? AND course_id != ?", (form["course_code"], course["course_id"] if course else 0), one=True):
            errors["course_code"] = "Kode ini sudah dipakai mata kuliah lain."
        if not errors:
            if course:
                run("UPDATE courses SET course_name=?, course_code=?, description=? WHERE course_id=?",
                    (form["course_name"], form["course_code"], form["description"], course["course_id"]))
                audit.log("course.update", "course", course["course_id"], form["course_code"])
                flash("Mata kuliah diperbarui.", "success")
            else:
                cid = run("INSERT INTO courses (course_name, course_code, description) VALUES (?,?,?)",
                          (form["course_name"], form["course_code"], form["description"]))
                audit.log("course.create", "course", cid, form["course_code"])
                flash("Mata kuliah ditambahkan.", "success")
            return redirect(url_for("admin.courses")), None, None
    elif course:
        form = {"course_name": course["course_name"], "course_code": course["course_code"], "description": course["description"] or ""}
    return None, form, errors


@bp.route("/mata-kuliah/baru", methods=["GET", "POST"])
@perm_required("course.manage")
def course_new():
    resp, form, errors = _course_form()
    if resp:
        return resp
    return render_template("admin/course_form.html", course=None, form=form, errors=errors)


@bp.route("/mata-kuliah/<int:cid>/ubah", methods=["GET", "POST"])
@perm_required("course.manage")
def course_edit(cid):
    course = q("SELECT * FROM courses WHERE course_id = ?", (cid,), one=True)
    if course is None:
        abort(404)
    resp, form, errors = _course_form(course)
    if resp:
        return resp
    return render_template("admin/course_form.html", course=course, form=form, errors=errors)


@bp.route("/mata-kuliah/<int:cid>/hapus", methods=["POST"])
@perm_required("course.manage")
def course_delete(cid):
    course = q("SELECT * FROM courses WHERE course_id = ?", (cid,), one=True)
    if course is None:
        abort(404)
    if scalar("SELECT COUNT(*) FROM classes WHERE course_id = ?", (cid,)):
        flash("Mata kuliah ini masih memiliki kelas, jadi tidak bisa dihapus. Nonaktifkan kelasnya bila sudah tidak dipakai.", "error")
        return redirect(url_for("admin.courses"))
    run("DELETE FROM courses WHERE course_id = ?", (cid,))
    audit.log("course.delete", "course", cid, course["course_code"])
    flash("Mata kuliah dihapus.", "success")
    return redirect(url_for("admin.courses"))


# ------------------------------------------------------------------ kelas


def _class_row(cid):
    c = services.class_row(cid)
    if c is None:
        abort(404)
    return c


@bp.route("/kelas")
@perm_required("course.manage")
def classes():
    status = request.args.get("status", "")
    term = (request.args.get("q") or "").strip()
    sql = ("SELECT c.*, co.course_name, co.course_code, u.full_name AS lecturer_name, "
           "(SELECT COUNT(*) FROM enrollments e WHERE e.class_id = c.class_id AND e.status = 'aktif') AS students "
           "FROM classes c JOIN courses co ON co.course_id = c.course_id JOIN users u ON u.user_id = c.lecturer_id WHERE 1=1")
    args = []
    if status in [s[0] for s in CLASS_STATUS]:
        sql += " AND c.status = ?"
        args.append(status)
    if term:
        sql += " AND (c.class_name LIKE ? OR co.course_name LIKE ? OR co.course_code LIKE ? OR u.full_name LIKE ?)"
        args += ["%" + term + "%"] * 4
    sql += " ORDER BY (c.status = 'aktif') DESC, co.course_name, c.class_name"
    rows, pager = services.paginate(sql, args, 12)
    return render_template("admin/classes.html", rows=rows, pager=pager, status=status, term=term, statuses=CLASS_STATUS)


def _class_form(cls=None):
    courses_ = q("SELECT course_id, course_code, course_name FROM courses ORDER BY course_name")
    lecturers = q("SELECT u.user_id, u.full_name FROM users u JOIN roles r ON r.role_id = u.role_id "
                  "WHERE r.role_name = 'dosen' AND u.account_status = 'aktif' ORDER BY u.full_name")
    errors, form = {}, {}
    if request.method == "POST":
        import re
        form = {k: (request.form.get(k) or "").strip() for k in ("course_id", "lecturer_id", "class_name", "semester", "academic_year", "status")}
        if not form["course_id"].isdigit() or not q("SELECT 1 FROM courses WHERE course_id = ?", (form["course_id"],), one=True):
            errors["course_id"] = "Pilih mata kuliah."
        valid_lect = {str(l["user_id"]) for l in lecturers}
        if cls:
            valid_lect.add(str(cls["lecturer_id"]))
        if form["lecturer_id"] not in valid_lect:
            errors["lecturer_id"] = "Pilih dosen pengampu yang akunnya aktif."
        if len(form["class_name"]) < 3:
            errors["class_name"] = "Nama kelas minimal 3 karakter."
        if form["semester"] not in [s[0] for s in SEMESTERS]:
            errors["semester"] = "Pilih semester."
        if not re.match(r"^\d{4}/\d{4}$", form["academic_year"]):
            errors["academic_year"] = "Gunakan format tahun ajaran, contoh 2026/2027."
        if form["status"] not in [s[0] for s in CLASS_STATUS]:
            errors["status"] = "Pilih status."
        if not errors:
            if cls:
                run("UPDATE classes SET course_id=?, lecturer_id=?, class_name=?, semester=?, academic_year=?, status=? WHERE class_id=?",
                    (form["course_id"], form["lecturer_id"], form["class_name"], form["semester"], form["academic_year"], form["status"], cls["class_id"]))
                audit.log("class.update", "class", cls["class_id"], form["class_name"])
                flash("Data kelas diperbarui.", "success")
                return redirect(url_for("admin.class_detail", cid=cls["class_id"])), None, None, None, None
            cid = run("INSERT INTO classes (course_id, lecturer_id, class_name, semester, academic_year, status) VALUES (?,?,?,?,?,?)",
                      (form["course_id"], form["lecturer_id"], form["class_name"], form["semester"], form["academic_year"], form["status"]))
            audit.log("class.create", "class", cid, form["class_name"])
            flash("Kelas dibuat.", "success")
            return redirect(url_for("admin.class_detail", cid=cid)), None, None, None, None
    elif cls:
        form = {k: str(cls[k]) for k in ("course_id", "lecturer_id", "class_name", "semester", "academic_year", "status")}
    else:
        form = {"course_id": "", "lecturer_id": "", "class_name": "", "semester": "Ganjil", "academic_year": "2026/2027", "status": "aktif"}
    return None, form, errors, courses_, lecturers


@bp.route("/kelas/baru", methods=["GET", "POST"])
@perm_required("course.manage")
def class_new():
    resp, form, errors, courses_, lecturers = _class_form()
    if resp:
        return resp
    return render_template("admin/class_form.html", cls=None, form=form, errors=errors, courses=courses_, lecturers=lecturers,
                           semesters=SEMESTERS, statuses=CLASS_STATUS)


@bp.route("/kelas/<int:cid>/ubah", methods=["GET", "POST"])
@perm_required("course.manage")
def class_edit(cid):
    cls = _class_row(cid)
    resp, form, errors, courses_, lecturers = _class_form(cls)
    if resp:
        return resp
    return render_template("admin/class_form.html", cls=cls, form=form, errors=errors, courses=courses_, lecturers=lecturers,
                           semesters=SEMESTERS, statuses=CLASS_STATUS)


@bp.route("/kelas/<int:cid>")
@perm_required("course.manage")
def class_detail(cid):
    cls = _class_row(cid)
    students = services.class_students(cid)
    candidates = q("SELECT u.user_id, u.full_name, u.email FROM users u JOIN roles r ON r.role_id = u.role_id "
                   "WHERE r.role_name = 'mahasiswa' AND u.account_status = 'aktif' AND u.user_id NOT IN "
                   "(SELECT student_id FROM enrollments WHERE class_id = ? AND status = 'aktif') ORDER BY u.full_name", (cid,))
    stats = {
        "materials": scalar("SELECT COUNT(*) FROM materials WHERE class_id = ?", (cid,)),
        "assignments": scalar("SELECT COUNT(*) FROM assignments WHERE class_id = ?", (cid,)),
        "quizzes": scalar("SELECT COUNT(*) FROM quizzes WHERE class_id = ?", (cid,)),
        "sessions": scalar("SELECT COUNT(*) FROM class_sessions WHERE class_id = ?", (cid,)),
    }
    return render_template("admin/class_detail.html", cls=cls, students=students, candidates=candidates, stats=stats,
                           statuses=CLASS_STATUS)


@bp.route("/kelas/<int:cid>/status", methods=["POST"])
@perm_required("course.manage")
def class_status(cid):
    cls = _class_row(cid)
    status = request.form.get("status")
    if status not in [s[0] for s in CLASS_STATUS]:
        abort(400)
    run("UPDATE classes SET status = ? WHERE class_id = ?", (status, cid))
    audit.log("class.update", "class", cid, "Status %s: %s -> %s" % (cls["class_name"], cls["status"], status))
    flash("Status kelas diubah menjadi %s." % status, "success")
    return redirect(request.form.get("next") or url_for("admin.class_detail", cid=cid))


@bp.route("/kelas/<int:cid>/peserta", methods=["POST"])
@perm_required("course.manage")
def class_enroll(cid):
    cls = _class_row(cid)
    sid = request.form.get("student_id", "")
    stu = q("SELECT u.* FROM users u JOIN roles r ON r.role_id = u.role_id WHERE u.user_id = ? AND r.role_name = 'mahasiswa'",
            (sid,), one=True) if sid.isdigit() else None
    if stu is None or stu["account_status"] != "aktif":
        flash("Pilih mahasiswa dengan akun aktif.", "error")
        return redirect(url_for("admin.class_detail", cid=cid))
    old = q("SELECT * FROM enrollments WHERE class_id = ? AND student_id = ?", (cid, stu["user_id"]), one=True)
    if old:
        run("UPDATE enrollments SET status='aktif', enrolled_at=? WHERE enrollment_id=?", (now(), old["enrollment_id"]))
    else:
        run("INSERT INTO enrollments (class_id, student_id, enrolled_at, status) VALUES (?,?,?,'aktif')", (cid, stu["user_id"], now()))
    audit.log("class.enroll", "class", cid, "%s ditambahkan ke %s" % (stu["full_name"], cls["class_name"]))
    flash("%s ditambahkan ke kelas." % stu["full_name"], "success")
    return redirect(url_for("admin.class_detail", cid=cid))


@bp.route("/kelas/<int:cid>/peserta/<int:eid>/keluar", methods=["POST"])
@perm_required("course.manage")
def class_unenroll(cid, eid):
    cls = _class_row(cid)
    enr = q("SELECT e.*, u.full_name FROM enrollments e JOIN users u ON u.user_id = e.student_id "
            "WHERE e.enrollment_id = ? AND e.class_id = ?", (eid, cid), one=True)
    if enr is None:
        abort(404)
    run("UPDATE enrollments SET status = 'keluar' WHERE enrollment_id = ?", (eid,))
    audit.log("class.unenroll", "class", cid, "%s dikeluarkan dari %s" % (enr["full_name"], cls["class_name"]))
    flash("%s dikeluarkan dari kelas. Riwayat nilainya tetap tersimpan." % enr["full_name"], "success")
    return redirect(url_for("admin.class_detail", cid=cid))


# ------------------------------------------------------------------ laporan


@bp.route("/laporan")
@perm_required("report.handle")
def reports():
    status = request.args.get("status", "")
    sql = ("SELECT r.*, u.full_name, ro.role_name, h.full_name AS handler FROM user_reports r JOIN users u ON u.user_id = r.reporter_id "
           "JOIN roles ro ON ro.role_id = u.role_id LEFT JOIN users h ON h.user_id = r.handled_by WHERE 1=1")
    args = []
    if status in [s[0] for s in REPORT_STATUS]:
        sql += " AND r.status = ?"
        args.append(status)
    sql += " ORDER BY (r.status = 'selesai'), r.report_id DESC"
    rows, pager = services.paginate(sql, args, 12)
    counts = {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) AS n FROM user_reports GROUP BY status")}
    return render_template("admin/reports.html", rows=rows, pager=pager, status=status, counts=counts, statuses=REPORT_STATUS)


@bp.route("/laporan/<int:rid>", methods=["GET", "POST"])
@perm_required("report.handle")
def report_detail(rid):
    r = q("SELECT r.*, u.full_name, u.email, ro.role_name, h.full_name AS handler FROM user_reports r "
          "JOIN users u ON u.user_id = r.reporter_id JOIN roles ro ON ro.role_id = u.role_id "
          "LEFT JOIN users h ON h.user_id = r.handled_by WHERE r.report_id = ?", (rid,), one=True)
    if r is None:
        abort(404)
    errors = {}
    if request.method == "POST":
        status = request.form.get("status")
        response = (request.form.get("response") or "").strip()[:1000]
        if status not in [s[0] for s in REPORT_STATUS]:
            errors["status"] = "Pilih status laporan."
        if status == "selesai" and len(response) < 5:
            errors["response"] = "Tulis tanggapan singkat sebelum menandai laporan selesai."
        if not errors:
            run("UPDATE user_reports SET status = ?, response = ?, handled_by = ? WHERE report_id = ?",
                (status, response or None, g.user["user_id"], rid))
            audit.log("report.update", "report", rid, "%s -> %s" % (r["subject"][:60], status))
            flash("Laporan diperbarui.", "success")
            return redirect(url_for("admin.report_detail", rid=rid))
    return render_template("admin/report_detail.html", r=r, errors=errors, statuses=REPORT_STATUS)


# ------------------------------------------------------------------ aktivitas platform


def _dir_size(path):
    total = 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


@bp.route("/aktivitas")
@perm_required("platform.monitor")
def activity():
    started = current_app.config["STARTED_AT"]
    uptime = datetime.now() - started
    hours, rem = divmod(int(uptime.total_seconds()), 3600)
    db_path = current_app.config["DATABASE"]
    health = {
        "db_ok": True,
        "db_size": human_size(os.path.getsize(db_path)) if os.path.exists(db_path) else "-",
        "upload_size": human_size(_dir_size(current_app.config["UPLOAD_DIR"])),
        "uptime": "%d jam %d menit" % (hours, rem // 60),
        "python": platform.python_version(),
        "maintenance": settings.get_bool("maintenance_mode"),
        "registration": settings.get_bool("registration_open"),
        "server_time": now(),
    }
    try:
        scalar("SELECT 1")
    except Exception:  # pragma: no cover
        health["db_ok"] = False
    series = {
        "login": services.daily_series("audit_logs.created_at", 14, "AND action = 'auth.login'"),
        "submission": services.daily_series("submissions.submitted_at", 14),
        "quiz": services.daily_series("quiz_attempts.started_at", 14),
    }
    logs = q("SELECT l.*, u.full_name FROM audit_logs l LEFT JOIN users u ON u.user_id = l.actor_user_id "
             "ORDER BY l.log_id DESC LIMIT 12")
    online = scalar("SELECT COUNT(DISTINCT actor_user_id) FROM audit_logs WHERE action = 'auth.login' AND created_at >= ?",
                    (datetime.now().strftime("%Y-%m-%d 00:00:00"),))
    return render_template("admin/activity.html", health=health, series=series, logs=logs, counts=services.platform_counts(),
                           label=audit.label, logins_today=online)
