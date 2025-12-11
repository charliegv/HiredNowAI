from flask import Blueprint, render_template, redirect, url_for, request, flash, send_file, redirect, jsonify
from flask_login import login_required, current_user
from models import db, Profile, PendingApplication, Application, Match, Job
from datetime import datetime
from sqlalchemy import desc
import hashlib
import requests
from io import BytesIO
from onboarding import require_onboarding_complete


dashboard = Blueprint('dashboard', __name__)


@dashboard.route("/")
@login_required
@require_onboarding_complete
def dashboard_home():

    profile = current_user.profile

    # Fetch recent applications
    activity = Application.query \
	    .filter(Application.user_id == current_user.id) \
	    .filter(Application.status != "manual_required") \
	    .order_by(Application.created_at.desc()) \
	    .all()

    manual_required = Application.query \
	    .filter_by(user_id=current_user.id, status="manual_required") \
	    .order_by(Application.created_at.desc()) \
	    .all()

    matches = (
	    db.session.query(
		    Match,
		    Job.title.label("job_title"),
		    Job.company.label("company"),
		    Job.city.label("city"),
		    Job.state.label("state"),
		    Job.country.label("country"),
		    Job.is_remote.label("remote_flag")
	    )
	    .join(Job, Match.job_id == Job.id)
	    .filter(Match.user_id == current_user.id)
	    .filter(
		    ~db.session.query(Application)
		    .filter(Application.user_id == current_user.id)
		    .filter(Application.job_id == Match.job_id)
		    .exists()
	    )
	    .order_by(desc(Match.score))
	    .limit(20)
	    .all()
    )

    total_sent = Application.query.filter(
	    Application.user_id == current_user.id,
	    Application.status.in_(["success", "manual_success"])
    ).count()

    match_count = Match.query.filter_by(user_id=current_user.id).count()

    stats = {
        "applications_sent": total_sent,
        "match_count": match_count,
        "profile_completion": 60
    }

    automation_running = profile.application_mode == "auto"

    return render_template(
        "dashboard.html",
        profile=profile,
        stats=stats,
        automation_running=automation_running,
        activity=activity,
        matches=matches,
	    manual_required=manual_required
    )

@dashboard.route("/application/<int:app_id>/manual-complete", methods=["POST"])
@login_required
def manual_application_complete(app_id):
    app = Application.query.get_or_404(app_id)

    if app.user_id != current_user.id:
        return {"success": False, "error": "Unauthorized"}, 403

    app.status = "manual_success"
    app.updated_at = datetime.utcnow()
    db.session.commit()

    return {"success": True}



@dashboard.route("/application/<int:app_id>/manual-start", methods=["POST"])
@login_required
def manual_start(app_id):
    app = Application.query.get_or_404(app_id)

    if app.user_id != current_user.id:
        return {"success": False, "error": "Unauthorized"}, 403

    app.manual_started = True
    db.session.commit()

    return {"success": True}



@dashboard.route("/pending-approvals", methods=["GET", "POST"])
@login_required
def pending_approvals():
    user_id = current_user.id

    # Fetch only pending items for this user
    pending = Application.query.filter_by(
        user_id=current_user.id,
        status="pending"
    ).all()

    return render_template(
        "pending_approvals.html",
        jobs=pending,
        profile=current_user.profile
    )


@dashboard.route("/pending-approvals/<int:item_id>/approve", methods=["POST"])
@login_required
def approve_pending_application(item_id):
    item = PendingApplication.query.get_or_404(item_id)
    item.status = "approved"
    db.session.commit()
    flash("Application approved. It will now be submitted automatically.", "success")
    return redirect(url_for("dashboard.pending_approvals"))


@dashboard.route("/pending-approvals/<int:item_id>/reject", methods=["POST"])
@login_required
def reject_application(item_id):
    item = PendingApplication.query.get_or_404(item_id)
    item.status = "rejected"
    db.session.commit()
    flash("Application rejected.", "error")
    return redirect(url_for("dashboard.pending_approvals"))

@dashboard.route("/application/<int:app_id>/approve", methods=["POST"])
@login_required
def approve_application(app_id):
    app = Application.query.get_or_404(app_id)

    # ensure user owns this item
    if app.user_id != current_user.id:
        flash("Unauthorized action.", "error")
        return redirect(url_for("dashboard.dashboard_home"))

    app.status = "approved"
    db.session.commit()

    flash("Application approved. HiredNow AI will now proceed.", "success")
    return redirect(url_for("dashboard.pending_approvals"))


@dashboard.route("/application/<int:app_id>/cancel", methods=["POST"])
@login_required
def cancel_application(app_id):
    app = Application.query.get_or_404(app_id)

    if app.user_id != current_user.id:
        flash("Unauthorized action.", "error")
        return redirect(url_for("dashboard.dashboard_home"))

    app.status = "cancelled"
    db.session.commit()

    flash("Application has been cancelled.", "success")
    return redirect(url_for("dashboard.dashboard_home"))

@dashboard.route("/application/<int:app_id>/retry", methods=["POST"])
@login_required
def retry_application(app_id):
    app = Application.query.get_or_404(app_id)

    if app.user_id != current_user.id:
        flash("Unauthorized action.", "error")
        return redirect(url_for("dashboard.dashboard_home"))

    app.status = "pending"
    app.error_log = None  # clear previous error
    db.session.commit()

    flash("Retry requested. HiredNow AI will attempt again soon.", "success")
    return redirect(url_for("dashboard.dashboard_home"))

@dashboard.route("/application/<int:app_id>/report", methods=["POST"])
@login_required
def report_application_issue(app_id):
    app = Application.query.get_or_404(app_id)

    if app.user_id != current_user.id:
        flash("Unauthorized action.", "error")
        return redirect(url_for("dashboard.dashboard_home"))

    # You can expand this to send Slack/email notifications
    print("Issue reported for application:", app_id)

    flash("Issue reported. We’ll investigate shortly.", "success")
    return redirect(url_for("dashboard.dashboard_home"))

@dashboard.route("/application/<int:app_id>/error")
@login_required
def view_error(app_id):
    app = Application.query.get_or_404(app_id)

    if app.user_id != current_user.id:
        flash("Unauthorized action.", "error")
        return redirect(url_for("dashboard.dashboard_home"))

    return render_template("application_error.html", app=app)


@dashboard.route("/application/<int:app_id>/cv")
@login_required
def view_cv_variant(app_id):
    app = Application.query.get_or_404(app_id)

    # Permission check
    if app.user_id != current_user.id:
        flash("Unauthorized action.", "error")
        return redirect(url_for("dashboard.dashboard_home"))

    # Ensure CV variant exists
    if not app.cv_variant_url:
        flash("No CV variant available for this application.", "error")
        return redirect(url_for("dashboard.dashboard_home"))

    # Download file from external storage
    try:
        response = requests.get(app.cv_variant_url, timeout=10)
        response.raise_for_status()
    except Exception:
        flash("Could not download CV variant.", "error")
        return redirect(url_for("dashboard.dashboard_home"))

    file_bytes = BytesIO(response.content)

    # Extract filename
    filename = app.cv_variant_url.split("/")[-1] or "cv_variant.docx"

    # Correct MIME type for .docx
    mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    return send_file(
        file_bytes,
        as_attachment=True,
        download_name=filename,
        mimetype=mimetype
    )

@dashboard.route("/application/<int:app_id>/screenshot")
@login_required
def view_application_screenshot(app_id):
    app = Application.query.get_or_404(app_id)

    if app.user_id != current_user.id:
        flash("Unauthorized action.", "error")
        return redirect(url_for("dashboard.dashboard_home"))

    if not app.screenshot_url:
        flash("No screenshot available for this application.", "error")
        return redirect(url_for("dashboard.dashboard_home"))

    return render_template("application_screenshot.html", app=app)


@dashboard.route("/matches")
@login_required
def dashboard_matches():

    matches = Match.query \
        .filter_by(user_id=current_user.id) \
        .order_by(Match.score.desc()) \
        .limit(50) \
        .all()


    return render_template(
            "matches.html",
            matches=matches,
            profile=current_user.profile,
            automation_running=(current_user.profile.application_mode == "auto")
        )


@dashboard.route("/apply-from-match/<int:match_id>", methods=["POST"])
@login_required
def apply_from_match(match_id):

    # Fetch match object
    match = Match.query.get_or_404(match_id)

    # Ensure this match belongs to the logged-in user (via profile.id)
    if match.user_id != current_user.id:
        flash("Unauthorized access.", "error")
        return redirect(url_for("dashboard.dashboard_home"))

    # Fetch job details from Job table
    job = Job.query.get(match.job_id)
    if not job:
        flash("Job no longer available.", "error")
        return redirect(url_for("dashboard.dashboard_home"))

    # Generate hash for dedupe
    job_hash = hashlib.sha256(job.job_url.encode()).hexdigest()

    # Check application already exists
    existing = Application.query.filter_by(
        user_id=current_user.id,
        job_url_hash=job_hash
    ).first()

    if existing:
        flash("You've already applied to this job.", "info")
        return redirect(url_for("dashboard.dashboard_home"))

    # Create new application entry
    new_app = Application(
        user_id=current_user.id,
        job_url=job.job_url,
        job_url_hash=job_hash,
        job_title=job.title,
        company=job.company,
        location=f"{job.city}, {job.state}" if job.city else None,
        salary=None,
        status="pending",
	    job_id=job.id,
    )

    db.session.add(new_app)
    db.session.commit()

    flash("AI is now preparing your application…", "success")
    return redirect(url_for("dashboard.dashboard_home"))

@dashboard.route("/dashboard/metrics")
@login_required
def dashboard_metrics():
    from datetime import datetime, timedelta
    from sqlalchemy import func

    sixty_days_ago = datetime.utcnow() - timedelta(days=60)

    rows = (
        db.session.query(
            func.date(Application.created_at),
            func.count().label("count")
        )
        .filter(
            Application.user_id == current_user.id,
            Application.status.in_(["success", "manual_success"]),
            Application.created_at >= sixty_days_ago,
        )
        .group_by(func.date(Application.created_at))
        .order_by(func.date(Application.created_at))
        .all()
    )

    labels = [row[0].strftime("%d %b") for row in rows]
    values = [row[1] for row in rows]

    return jsonify({"labels": labels, "values": values})
