from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import audit, auth
from ..db import now, q, run

bp = Blueprint("common", __name__)


@bp.route("/profil", methods=["GET", "POST"])
@auth.login_required
def profile():
    errors = {}
    if request.method == "POST":
        action = request.form.get("action")
        if action == "profile":
            name = (request.form.get("full_name") or "").strip()
            if len(name) < 3:
                errors["full_name"] = "Nama lengkap minimal 3 karakter."
            else:
                run("UPDATE users SET full_name = ? WHERE user_id = ?", (name, g.user["user_id"]))
                audit.log("account.update", "user", g.user["user_id"], "Mengubah nama profil")
                flash("Profil diperbarui.", "success")
                return redirect(url_for("common.profile"))
        elif action == "password":
            current = request.form.get("current") or ""
            new = request.form.get("new") or ""
            confirm = request.form.get("confirm") or ""
            if not auth.verify_password(g.user["password_hash"], current):
                errors["current"] = "Password saat ini salah."
            elif auth.password_problem(new):
                errors["new"] = auth.password_problem(new)
            elif new != confirm:
                errors["confirm"] = "Konfirmasi password tidak sama."
            else:
                run("UPDATE users SET password_hash = ? WHERE user_id = ?", (auth.hash_password(new), g.user["user_id"]))
                audit.log("account.password", "user", g.user["user_id"])
                flash("Password berhasil diganti.", "success")
                return redirect(url_for("common.profile"))
    role_perms = []
    if g.user["role_name"] != auth.ROLE_SUPER:
        role_perms = q("SELECT p.permission_name, p.description FROM role_permissions rp "
                       "JOIN permissions p ON p.permission_id = rp.permission_id WHERE rp.role_id = ? ORDER BY p.permission_name",
                       (g.user["role_id"],))
    return render_template("common/profile.html", errors=errors, role_perms=role_perms)


@bp.route("/bantuan", methods=["GET", "POST"])
@auth.login_required
def help():
    errors, form = {}, {}
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in ("subject", "description")}
        if len(form["subject"]) < 5:
            errors["subject"] = "Ringkasan masalah minimal 5 karakter."
        if len(form["description"]) < 15:
            errors["description"] = "Jelaskan masalahnya minimal 15 karakter agar mudah ditindaklanjuti."
        if not errors:
            rid = run("INSERT INTO user_reports (reporter_id, subject, description, status, created_at) VALUES (?,?,?,?,?)",
                      (g.user["user_id"], form["subject"][:120], form["description"][:2000], "baru", now()))
            audit.log("report.create", "report", rid, form["subject"][:80])
            flash("Laporan terkirim. Admin akan menanggapi dan statusnya bisa dipantau di halaman ini.", "success")
            return redirect(url_for("common.help"))
    reports = q("SELECT r.*, h.full_name AS handler FROM user_reports r LEFT JOIN users h ON h.user_id = r.handled_by "
                "WHERE r.reporter_id = ? ORDER BY r.report_id DESC", (g.user["user_id"],))
    return render_template("common/help.html", errors=errors, form=form, reports=reports)
