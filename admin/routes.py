from flask import Blueprint, render_template, redirect, url_for, session, abort, request
from flask_login import login_user, current_user, login_required
from utils.decorators import admin_required
from models import User, Application, Profile, db
from sqlalchemy import func, desc, asc

admin_bp = Blueprint("admin", __name__, template_folder="templates")



@admin_bp.route("/")
@admin_required
def dashboard():

    # -----------------------------
    # pagination
    # -----------------------------
    page = request.args.get("page", 1, type=int)
    per_page = 25

    # -----------------------------
    # sorting
    # -----------------------------
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")

    sort_map = {
        "id": User.id,
        "email": User.email,
        "country": Profile.country,
        "applications": func.count(Application.id),
        "last_application": func.max(Application.created_at),
        "created_at": User.created_at,
    }

    sort_col = sort_map.get(sort, User.created_at)
    order_func = desc if direction == "desc" else asc

    # -----------------------------
    # base query
    # -----------------------------
    query = (
        db.session.query(
            User,
            func.count(Application.id).label("application_count"),
            func.max(Application.created_at).label("last_application"),
        )
        .outerjoin(Application, Application.user_id == User.id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .group_by(User.id)
    )

    # -----------------------------
    # ordering
    # -----------------------------
    query = query.order_by(order_func(sort_col))

    # -----------------------------
    # pagination
    # -----------------------------
    total_users = query.count()
    users = query.offset((page - 1) * per_page).limit(per_page).all()

    total_pages = (total_users + per_page - 1) // per_page

    # -----------------------------
    # stats
    # -----------------------------
    stats = {
        "users": User.query.count(),
        "applications": Application.query.count(),
        "active_users": User.query.join(Profile)
            .filter(Profile.application_mode == "auto")
            .count(),
        "errors_today": 0
    }

    return render_template(
        "admin/dashboard.html",
        users=users,
        stats=stats,
        sort=sort,
        direction=direction,
        page=page,
        total_pages=total_pages,
    )


@admin_bp.route("/impersonate/<int:user_id>")
@admin_required
def impersonate(user_id):
    # prevent nested impersonation
    if session.get("admin_id"):
        abort(403)

    user = User.query.get_or_404(user_id)

    # prevent impersonating another admin
    if user.is_admin:
        abort(403)

    session["admin_id"] = current_user.id
    login_user(user)

    return redirect(url_for("dashboard.dashboard_home"))


@admin_bp.route("/stop-impersonation")
@login_required
def stop_impersonation():
    admin_id = session.get("admin_id")
    if not admin_id:
        abort(403)

    admin = User.query.get_or_404(admin_id)
    session.pop("admin_id")
    login_user(admin)

    return redirect(url_for("admin.dashboard"))
