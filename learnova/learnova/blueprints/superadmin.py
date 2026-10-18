"""Modul Super Admin: akun admin, role & hak akses, konfigurasi, statistik, audit log."""
from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import accounts as acc
from .. import audit, auth, services, settings
from ..auth import ROLE_ADMIN, ROLE_SUPER, perm_required
from ..db import now, q, run, scalar

bp = Blueprint("superadmin", __name__)


@bp.before_request
def _guard():
    if g.user is None:
        return redirect(url_for("auth.login", next=request.path))
    if g.user["role_name"] != ROLE_SUPER:
        abort(403)


# ------------------------------------------------------------------ beranda


@bp.route("/")
def dashboard():
    counts = services.platform_counts()
    series = {
        "login": services.daily_series("audit_logs.created_at", 14, "AND action = 'auth.login'"),
        "submission": services.daily_series("submissions.submitted_at", 14),
    }
    recent = q("SELECT l.*, u.full_name FROM audit_logs l LEFT JOIN users u ON u.user_id = l.actor_user_id "
               "WHERE l.action NOT IN ('auth.login','auth.logout') ORDER BY l.log_id DESC LIMIT 7")
    role_rows = q("SELECT r.role_name, r.description, COUNT(u.user_id) AS users, "
                  "(SELECT COUNT(*) FROM role_permissions rp WHERE rp.role_id = r.role_id) AS perms "
                  "FROM roles r LEFT JOIN users u ON u.role_id = r.role_id GROUP BY r.role_id ORDER BY r.role_id")
    return render_template("superadmin/dashboard.html", counts=counts, series=series, recent=recent, role_rows=role_rows,
                           label=audit.label, total_perms=scalar("SELECT COUNT(*) FROM permissions"))


# ------------------------------------------------------------------ akun admin


def _admin_target(uid):
    u = q("SELECT u.*, r.role_name FROM users u JOIN roles r ON r.role_id = u.role_id WHERE u.user_id = ?", (uid,), one=True)
    if u is None or u["role_name"] != ROLE_ADMIN:
        abort(404)
    return u


@bp.route("/akun-admin")
@perm_required("admin.manage")
def admins():
    term = (request.args.get("q") or "").strip()
    sql = ("SELECT u.*, r.role_name, (SELECT COUNT(*) FROM audit_logs l WHERE l.actor_user_id = u.user_id) AS actions, "
           "(SELECT MAX(created_at) FROM audit_logs l WHERE l.actor_user_id = u.user_id AND l.action = 'auth.login') AS last_login "
           "FROM users u JOIN roles r ON r.role_id = u.role_id WHERE r.role_name = 'admin'")
    args = []
    if term:
        sql += " AND (u.full_name LIKE ? OR u.email LIKE ?)"
        args += ["%" + term + "%"] * 2
    sql += " ORDER BY u.created_at DESC"
    rows = q(sql, args)
    return render_template("superadmin/admins.html", rows=rows, term=term)


@bp.route("/akun-admin/baru", methods=["GET", "POST"])
@perm_required("admin.manage")
def admin_new():
    errors, form = {}, {"status": "aktif", "role": ROLE_ADMIN}
    if request.method == "POST":
        data = request.form.to_dict()
        data["role"] = ROLE_ADMIN
        clean, errors = acc.validate(data, None, (ROLE_ADMIN,), True)
        form = clean
        if not errors:
            uid = acc.create(clean)
            audit.log("account.create", "user", uid, "Akun admin %s" % clean["email"])
            flash("Akun admin dibuat.", "success")
            return redirect(url_for("superadmin.admins"))
    return render_template("admin/account_form.html", user=None, form=form, errors=errors, roles=(ROLE_ADMIN,),
                           statuses=acc.STATUSES, scope="super")


@bp.route("/akun-admin/<int:uid>/ubah", methods=["GET", "POST"])
@perm_required("admin.manage")
def admin_edit(uid):
    u = _admin_target(uid)
    errors = {}
    form = {"full_name": u["full_name"], "email": u["email"], "role": ROLE_ADMIN, "status": u["account_status"]}
    if request.method == "POST":
        data = request.form.to_dict()
        data["role"] = ROLE_ADMIN
        clean, errors = acc.validate(data, u, (ROLE_ADMIN,), False)
        form = clean
        if not errors:
            acc.update(u, clean)
            audit.log("account.update", "user", uid, "Super admin mengubah akun admin %s" % clean["email"])
            flash("Akun admin diperbarui.", "success")
            return redirect(url_for("superadmin.admins"))
    return render_template("admin/account_form.html", user=u, form=form, errors=errors, roles=(ROLE_ADMIN,),
                           statuses=acc.STATUSES, scope="super")


@bp.route("/akun-admin/<int:uid>/status", methods=["POST"])
@perm_required("admin.manage")
def admin_status(uid):
    u = _admin_target(uid)
    status = request.form.get("status")
    if status not in ("aktif", "nonaktif"):
        abort(400)
    run("UPDATE users SET account_status = ? WHERE user_id = ?", (status, uid))
    audit.log("account.status", "user", uid, "Admin %s: %s -> %s" % (u["email"], u["account_status"], status))
    flash("Akun %s %s." % (u["full_name"], "diaktifkan" if status == "aktif" else "dinonaktifkan"), "success")
    return redirect(url_for("superadmin.admins"))


# ------------------------------------------------------------------ role & hak akses


@bp.route("/hak-akses", methods=["GET", "POST"])
@perm_required("role.manage")
def permissions():
    roles = q("SELECT r.*, (SELECT COUNT(*) FROM users u WHERE u.role_id = r.role_id) AS users FROM roles r ORDER BY r.role_id")
    selected_name = request.values.get("role") or "admin"
    role = next((r for r in roles if r["role_name"] == selected_name), None)
    if role is None:
        abort(404)
    all_perms = q("SELECT * FROM permissions ORDER BY permission_id")
    group_of = {p[0]: p[2] for p in auth.PERMISSIONS}
    groups = {}
    for p in all_perms:
        groups.setdefault(group_of.get(p["permission_name"], "Lainnya"), []).append(p)
    locked = role["role_name"] == ROLE_SUPER
    if request.method == "POST":
        if locked:
            flash("Hak akses Super Admin tidak dapat diubah agar sistem tidak terkunci.", "error")
            return redirect(url_for("superadmin.permissions", role=selected_name))
        wanted = {int(x) for x in request.form.getlist("perm") if x.isdigit()}
        valid = {p["permission_id"] for p in all_perms}
        wanted &= valid
        current = {r["permission_id"] for r in q("SELECT permission_id FROM role_permissions WHERE role_id = ?", (role["role_id"],))}
        added, removed = wanted - current, current - wanted
        for pid in added:
            run("INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)", (role["role_id"], pid))
        for pid in removed:
            run("DELETE FROM role_permissions WHERE role_id = ? AND permission_id = ?", (role["role_id"], pid))
        name = {p["permission_id"]: p["permission_name"] for p in all_perms}
        detail = "Role %s. Ditambah: %s. Dicabut: %s." % (
            role["role_name"], ", ".join(sorted(name[i] for i in added)) or "-", ", ".join(sorted(name[i] for i in removed)) or "-")
        audit.log("role.permissions", "role", role["role_id"], detail)
        flash("Perubahan hak akses disimpan (%d ditambah, %d dicabut)." % (len(added), len(removed)) if (added or removed)
              else "Tidak ada perubahan hak akses.", "success")
        return redirect(url_for("superadmin.permissions", role=selected_name))
    granted = {r["permission_id"] for r in q("SELECT permission_id FROM role_permissions WHERE role_id = ?", (role["role_id"],))}
    return render_template("superadmin/permissions.html", roles=roles, role=role, groups=groups, granted=granted, locked=locked)


# ------------------------------------------------------------------ konfigurasi


@bp.route("/konfigurasi", methods=["GET", "POST"])
@perm_required("settings.manage")
def settings_page():
    values = settings.all_settings()
    errors = []
    if request.method == "POST":
        clean, errors = settings.validate(request.form)
        if not errors:
            changed = []
            for key, val in clean.items():
                if values.get(key) != val:
                    changed.append("%s: %s -> %s" % (key, values.get(key), val))
                run("INSERT INTO system_settings (setting_key, setting_value, updated_by, updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                    (key, val, g.user["user_id"], now()))
            audit.log("settings.update", "settings", None, "; ".join(changed) if changed else "Tidak ada perubahan nilai")
            flash("Konfigurasi disimpan." if changed else "Tidak ada perubahan.", "success")
            return redirect(url_for("superadmin.settings_page"))
        values = {**values, **{k: (request.form.get(k) or "") for k in values}}
        for d in settings.DEFINITIONS:
            if d["type"] == "bool":
                values[d["key"]] = "1" if request.form.get(d["key"]) else "0"
    meta = {r["setting_key"]: r for r in q("SELECT s.*, u.full_name FROM system_settings s LEFT JOIN users u ON u.user_id = s.updated_by")}
    return render_template("superadmin/settings.html", defs=settings.DEFINITIONS, values=values, errors=errors, meta=meta)


# ------------------------------------------------------------------ statistik


@bp.route("/statistik")
@perm_required("stats.view")
def stats():
    counts = services.platform_counts()
    series = {
        "login": services.daily_series("audit_logs.created_at", 14, "AND action = 'auth.login'"),
        "submission": services.daily_series("submissions.submitted_at", 14),
        "quiz": services.daily_series("quiz_attempts.started_at", 14),
        "register": services.daily_series("users.created_at", 14),
    }
    status_rows = q("SELECT account_status, COUNT(*) AS n FROM users GROUP BY account_status")
    role_rows = q("SELECT r.role_name, COUNT(u.user_id) AS n FROM roles r LEFT JOIN users u ON u.role_id = r.role_id GROUP BY r.role_id ORDER BY r.role_id")
    class_rows = q("SELECT c.class_id, c.class_name, c.status, u.full_name AS lecturer, "
                   "(SELECT COUNT(*) FROM enrollments e WHERE e.class_id = c.class_id AND e.status = 'aktif') AS students, "
                   "(SELECT COUNT(*) FROM materials m WHERE m.class_id = c.class_id) AS materials, "
                   "(SELECT COUNT(*) FROM submissions s JOIN assignments a ON a.assignment_id = s.assignment_id WHERE a.class_id = c.class_id) AS submissions, "
                   "(SELECT COUNT(*) FROM quiz_attempts t JOIN quizzes z ON z.quiz_id = t.quiz_id WHERE z.class_id = c.class_id AND t.submitted_at IS NOT NULL) AS attempts "
                   "FROM classes c JOIN users u ON u.user_id = c.lecturer_id ORDER BY students DESC, c.class_name LIMIT 10")
    top_actions = q("SELECT action, COUNT(*) AS n FROM audit_logs WHERE action NOT LIKE 'auth.%' GROUP BY action ORDER BY n DESC LIMIT 6")
    return render_template("superadmin/stats.html", counts=counts, series=series, status_rows=status_rows, role_rows=role_rows,
                           class_rows=class_rows, top_actions=top_actions, label=audit.label)


# ------------------------------------------------------------------ audit log


@bp.route("/audit")
@perm_required("audit.view")
def audit_log():
    term = (request.args.get("q") or "").strip()
    action = request.args.get("action", "")
    actor = request.args.get("actor", "")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    sql = ("SELECT l.*, u.full_name, u.email, r.role_name FROM audit_logs l LEFT JOIN users u ON u.user_id = l.actor_user_id "
           "LEFT JOIN roles r ON r.role_id = u.role_id WHERE 1=1")
    args = []
    if action:
        sql += " AND l.action = ?"
        args.append(action)
    if actor.isdigit():
        sql += " AND l.actor_user_id = ?"
        args.append(int(actor))
    if date_from:
        sql += " AND substr(l.created_at,1,10) >= ?"
        args.append(date_from)
    if date_to:
        sql += " AND substr(l.created_at,1,10) <= ?"
        args.append(date_to)
    if term:
        sql += " AND (l.details LIKE ? OR u.full_name LIKE ? OR u.email LIKE ? OR l.target_type LIKE ?)"
        args += ["%" + term + "%"] * 4
    sql += " ORDER BY l.log_id DESC"
    rows, pager = services.paginate(sql, args, 20)
    actions = q("SELECT DISTINCT action FROM audit_logs ORDER BY action")
    actors = q("SELECT DISTINCT u.user_id, u.full_name FROM audit_logs l JOIN users u ON u.user_id = l.actor_user_id ORDER BY u.full_name")
    return render_template("superadmin/audit.html", rows=rows, pager=pager, actions=actions, actors=actors, label=audit.label,
                           f={"q": term, "action": action, "actor": actor, "from": date_from, "to": date_to})
