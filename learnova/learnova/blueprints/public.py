from flask import Blueprint, render_template

bp = Blueprint("public", __name__)


@bp.route("/")
def landing():
    return render_template("public/landing.html")
