from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Profile

preferences = Blueprint("preferences", __name__)

@preferences.route("/preferences", methods=["GET", "POST"])
@login_required
def preferences_page():

    profile = current_user.profile

    if request.method == "POST":

        # Basic Info
        profile.first_name = request.form.get("first_name")
        profile.last_name = request.form.get("last_name")

        profile.application_mode = request.form.get("application_mode") or "auto"
        profile.match_mode = request.form.get("match_mode") or "standard"

        # Job titles
        profile.job_titles = request.form.get("job_titles")

        # Location
        profile.city = request.form.get("city")
        profile.state = request.form.get("state")
        profile.country = request.form.get("country")

        # Remote
        profile.remote_preference = bool(request.form.get("remote_preference"))

        # Salary
        profile.min_salary = request.form.get("min_salary") or None
        profile.max_salary = request.form.get("max_salary") or None

        # Frequency
        profile.application_frequency = request.form.get("application_frequency")

        # CV upload (future: integrate S3)
        file = request.files.get("cv_file")
        if file and file.filename:
            filepath = f"uploads/{current_user.id}_{file.filename}"
            file.save(filepath)
            profile.cv_location = filepath

        db.session.commit()
        flash("Preferences updated successfully!", "success")
        return redirect(url_for("preferences.preferences_page"))

    return render_template("preferences.html", profile=profile)
