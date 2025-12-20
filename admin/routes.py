from flask import Blueprint, render_template, redirect, url_for, session, abort, request
from flask_login import login_user, current_user, login_required
from utils.decorators import admin_required
from models import User, Application, Profile, db
from sqlalchemy import func, desc, asc
from models import CreditBalance, CreditLedger
from sqlalchemy.exc import IntegrityError
from models import CreditBalance



admin_bp = Blueprint("admin", __name__, template_folder="templates")

def admin_adjust_credits(user_id: int, amount: int, reason: str, reference: str):
    """
    amount can be positive (add) or negative (remove)
    """
    if amount == 0:
        return

    try:
        # ensure balance row exists
        balance = CreditBalance.query.filter_by(user_id=user_id).with_for_update().first()
        if not balance:
            balance = CreditBalance(user_id=user_id)
            db.session.add(balance)
            db.session.flush()

        # prevent negative balance
        new_available = max(0, balance.available_credits + amount)

        # ledger entry
        db.session.add(
            CreditLedger(
                user_id=user_id,
                change_amount=amount,
                reason=reason,
                reference_id=reference,
            )
        )

        balance.available_credits = new_available
        balance.lifetime_granted += max(amount, 0)
        balance.lifetime_spent += abs(min(amount, 0))

        db.session.commit()

    except Exception:
        db.session.rollback()
        raise


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
	    "credits": func.coalesce(CreditBalance.available_credits, 0),
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
		    func.coalesce(CreditBalance.available_credits, 0).label("credits"),
	    )
	    .outerjoin(Application, Application.user_id == User.id)
	    .outerjoin(Profile, Profile.user_id == User.id)
	    .outerjoin(CreditBalance, CreditBalance.user_id == User.id)
	    .group_by(User.id, CreditBalance.available_credits)
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

@admin_bp.route("/user/<int:user_id>/deactivate", methods=["POST"])
@admin_required
def deactivate_user(user_id):
    profile = Profile.query.filter_by(user_id=user_id).first_or_404()

    reason = request.form.get("reason", "admin_paused")

    profile.is_active = False
    profile.deactivate_reason = reason

    db.session.commit()
    return redirect(url_for("admin.dashboard"))

@admin_bp.route("/user/<int:user_id>/activate", methods=["POST"])
@admin_required
def activate_user(user_id):
    profile = Profile.query.filter_by(user_id=user_id).first_or_404()

    profile.is_active = True
    profile.deactivate_reason = None

    db.session.commit()
    return redirect(url_for("admin.dashboard"))

@admin_bp.route("/user/<int:user_id>/add-credits", methods=["POST"])
@admin_required
def add_credits(user_id):
    amount = request.form.get("amount", type=int)

    if not amount or amount <= 0:
        abort(400)

    admin_adjust_credits(
        user_id=user_id,
        amount=amount,
        reason="admin_grant",
        reference=f"admin:{current_user.id}"
    )

    return redirect(url_for("admin.dashboard"))

@admin_bp.route("/user/<int:user_id>/remove-credits", methods=["POST"])
@admin_required
def remove_credits(user_id):
    amount = request.form.get("amount", type=int)

    if not amount or amount <= 0:
        abort(400)

    admin_adjust_credits(
        user_id=user_id,
        amount=-amount,
        reason="admin_revoke",
        reference=f"admin:{current_user.id}"
    )

    return redirect(url_for("admin.dashboard"))
