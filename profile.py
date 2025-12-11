from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Profile
from utils.geocode import geocode_city

preferences = Blueprint("preferences", __name__)


from utils.geocode import geocode_city

preferences = Blueprint("preferences", __name__)

@preferences.route("/preferences", methods=["GET", "POST"])
@login_required
def preferences_page():

    profile = current_user.profile

    if request.method == "POST":

        # Track old location so we know when to re-geocode
        old_city = profile.city
        old_state = profile.state

        # -----------------------------
        # BASIC USER INFO
        # -----------------------------
        profile.first_name = request.form.get("first_name")
        profile.last_name = request.form.get("last_name")

        profile.application_mode = request.form.get("application_mode") or "auto"
        profile.match_mode = request.form.get("match_mode") or "standard"

        # -----------------------------
        # JOB TITLES
        # -----------------------------
        profile.job_titles = request.form.get("job_titles")

        # -----------------------------
        # LOCATION FIELDS (CITY & STATE)
        # -----------------------------
        new_city = request.form.get("city")
        new_state = request.form.get("state")

        profile.city = new_city
        profile.state = new_state

        # -----------------------------
        # GEO-CODE if city or state changed
        # -----------------------------
        if new_city != old_city or new_state != old_state:
            try:
                lat, lon = geocode_city(new_city, profile.country)
            except Exception:
                lat, lon = None, None

            profile.latitude = lat
            profile.longitude = lon

        # -----------------------------
        # LOCATION SCOPE
        # -----------------------------
        profile.location_scope = request.form.get("location_scope") or "nationwide"

        # -----------------------------
        # LOCAL RADIUS (only if local)
        # -----------------------------
        if profile.location_scope == "local":
            miles = request.form.get("miles_distance")
            try:
                profile.miles_distance = int(miles) if miles else 50
            except:
                profile.miles_distance = 50
        else:
            profile.miles_distance = 5000

        # -----------------------------
        # REMOTE SETTINGS
        # -----------------------------
        remote_mode = request.form.get("remote_mode")

        if remote_mode == "none":
            profile.remote_preference = False
            profile.worldwide_remote = False

        elif remote_mode == "nationwide":
            profile.remote_preference = True
            profile.worldwide_remote = False

        elif remote_mode == "worldwide":
            profile.remote_preference = True
            profile.worldwide_remote = True

        # -----------------------------
        # SALARY
        # -----------------------------
        profile.min_salary = request.form.get("min_salary") or None
        profile.max_salary = request.form.get("max_salary") or None

        # -----------------------------
        # APPLICATION FREQUENCY
        # -----------------------------
        profile.application_frequency = request.form.get("application_frequency")

        # -----------------------------
        # OPTIONAL CV UPLOAD
        # -----------------------------
        file = request.files.get("cv_file")
        if file and file.filename:
            filepath = f"uploads/{current_user.id}_{file.filename}"
            file.save(filepath)
            profile.cv_location = filepath

        # -----------------------------
        # SAVE
        # -----------------------------
        db.session.commit()
        flash("Preferences updated successfully!", "success")
        return redirect(url_for("preferences.preferences_page"))

    # GET request
    return render_template("preferences.html", profile=profile)
