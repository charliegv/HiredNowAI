from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Profile
from werkzeug.utils import secure_filename
from utils.cv_parser import extract_cv_text
from utils.cv_ai import parse_cv_with_ai
from utils.geocode import geocode_city
from matching import match_user
import psycopg2
import os
from utils.s3_uploader import upload_to_s3
from utils.background import run_async

onboarding = Blueprint("onboarding", __name__)

UPLOAD_FOLDER = "tmp/"


# =========================================================
# STEP 1 — Ensure a Profile always exists
# =========================================================
def get_or_create_profile():
    profile = Profile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        profile = Profile(user_id=current_user.id)
        db.session.add(profile)
        db.session.commit()
    return profile


# =========================================================
# STEP 1 — Job titles + location
# =========================================================
@onboarding.route("/onboarding/step1", methods=["GET", "POST"])
@login_required
def step1():
    profile = get_or_create_profile()

    if request.method == "POST":
        profile.job_titles = request.form["job_titles"]
        profile.city = request.form["city"]
        profile.country = request.form["country"]

        # Safe geocoding
        try:
            lat, lon = geocode_city(profile.city, profile.country)
        except Exception:
            lat, lon = None, None

        profile.latitude = lat
        profile.longitude = lon

        db.session.commit()
        return redirect(url_for("onboarding.step2"))

    return render_template("onboarding_step1.html", step=1, progress=25)



# STEP 2 — Salary + Remote Preference
# =========================================================
@onboarding.route("/onboarding/step2", methods=["GET", "POST"])
@login_required
def step2():
    profile = get_or_create_profile()

    # Prevent skipping step 1 (must be *before* POST logic returns)
    if not profile.job_titles or not profile.city:
        return redirect(url_for("onboarding.step1"))

    if request.method == "POST":
        profile.min_salary = request.form["min_salary"]
        profile.remote_preference = "remote_preference" in request.form

        # Optional miles filter
        if "miles_distance" in request.form:
            try:
                profile.miles_distance = int(request.form["miles_distance"])
            except:
                profile.miles_distance = None

        db.session.commit()

        # -----------------------------------------------------
        # FIRE ASYNC MATCHING IMMEDIATELY (non blocking)
        # -----------------------------------------------------
        try:
            user_id = current_user.id  # capture before thread

            # Insert user_id into match queue
            from psycopg2.extras import RealDictCursor

            conn = psycopg2.connect(os.getenv("DATABASE_URL"))
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("INSERT INTO match_queue (user_id) VALUES (%s)", (user_id,))
            conn.commit()
            cur.close()
            conn.close()

            print(f"[MATCH] Queued matching job for user {user_id}")


        except Exception as e:
            print("Error Queuing matching thread:", e)

        # Redirect instantly, do not wait for matching
        return redirect(url_for("onboarding.step3"))

    # GET request
    return render_template("onboarding_step2.html", step=2, progress=50)


# =========================================================
# STEP 3 — CV + Final settings + Matching Trigger
# =========================================================
@onboarding.route("/onboarding/step3", methods=["GET", "POST"])
@login_required
def step3():
    profile = get_or_create_profile()

    if request.method == "POST":

        # Save final preferences
        profile.application_frequency = request.form.get("application_frequency")
        profile.application_mode = request.form.get("application_mode", "auto")
        profile.match_mode = request.form.get("match_mode", "standard")

        # Handle CV (optional)
        file = request.files.get("cv_file")
        if not file or file.filename.strip() == "":
            flash("Please upload a valid CV file (.pdf, .docx, .txt)", "error")
            return redirect(request.url)
        print(file)
        if file and file.filename:
            filename = secure_filename(file.filename)

            # Always use true temp dir in Render
            cv_path = os.path.join("/tmp", filename)
            file.stream.seek(0)
            file.save(cv_path)

            # Parse the CV locally BEFORE upload
            raw_text = extract_cv_text(cv_path)
            parsed = parse_cv_with_ai(raw_text)
            profile.ai_cv_data = parsed

            # Upload to S3 after parsing
            s3_url = upload_to_s3(cv_path, folder=f"user-cvs/{current_user.id}")
            profile.cv_location = s3_url

            # Optionally remove temp file
            try:
                os.remove(cv_path)
            except:
                pass

        # Mark onboarding complete
        profile.onboarding_complete = True
        db.session.commit()

        return redirect(url_for("onboarding.step4"))

    return render_template("onboarding_step3.html", profile=profile, step=3, progress=75)


@onboarding.route("/onboarding/step4", methods=["GET", "POST"])
@login_required
def step4():
    user = current_user
    profile = Profile.query.filter_by(user_id=user.id).first()

    if request.method == "POST":
        # Collect form values
        application_data = {
            "sponsorship_required": request.form.get("sponsorship_required") == "yes",
            "work_authorization": request.form.get("work_authorization"),
            "legally_allowed": request.form.get("legally_allowed") == "yes",
            "notice_period": request.form.get("notice_period"),
            "willing_to_relocate": request.form.get("willing_to_relocate") == "yes",
            "location_preference": request.form.get("location_preference"),
            "desired_salary": request.form.get("desired_salary"),
            "years_experience": request.form.get("years_experience"),
            "highest_education": request.form.get("highest_education"),
            "gender": request.form.get("gender"),
            "race": request.form.get("race"),
            "veteran_status": request.form.get("veteran_status"),
            "disability_status": request.form.get("disability_status"),
        }

        profile.application_data = application_data
        profile.onboarding_application_complete = True
        db.session.commit()

        # Redirect to success dashboard or next step
        return redirect(url_for("onboarding.edit_cv"))

    return render_template("onboarding_step4.html", profile=profile, step=4, progress=100)



# =========================================================
# CV Preview
# =========================================================
@onboarding.route("/cv/preview")
@login_required
def cv_preview():
    profile = get_or_create_profile()
    return render_template("cv_preview.html", cv=profile.ai_cv_data, profile=profile)



# =========================================================
# CV Edit
# =========================================================
@onboarding.route("/cv/edit", methods=["GET", "POST"])
@login_required
def edit_cv():
    profile = get_or_create_profile()

    if request.method == "POST":

        def split_clean(value):
            if not value:
                return []
            return [v.strip() for v in value.split(",") if v.strip()]

        # Update JSON CV
        updated_data = {
            "first_name": request.form.get("first_name", ""),
            "last_name": request.form.get("last_name", ""),
            "email": request.form.get("email", ""),
            "phone": request.form.get("phone", ""),
            "address": request.form.get("address", ""),

            "summary": request.form.get("summary", ""),
            "skills": split_clean(request.form.get("skills")),
            "job_titles": split_clean(request.form.get("job_titles")),

            "experience": [],
            "education": [],
            "certifications": split_clean(request.form.get("certifications")),
            "languages": split_clean(request.form.get("languages")),

            "additional_details": {
                "publications": split_clean(request.form.get("publications")),
                "github": request.form.get("github", ""),
                "linkedin": request.form.get("linkedin", ""),
                "portfolio": request.form.get("portfolio", ""),
                "thesis": request.form.get("thesis", ""),
                "awards": split_clean(request.form.get("awards")),
                "volunteering": split_clean(request.form.get("volunteering")),
                "interests": split_clean(request.form.get("interests")),
                "other": request.form.get("other", "")
            }
        }

        # EXPERIENCE
        exp_count = int(request.form.get("exp_count", 0))
        for i in range(exp_count):
            if request.form.get(f"exp_delete_{i}"):
                continue

            role = request.form.get(f"exp_role_{i}")
            company = request.form.get(f"exp_company_{i}")
            start = request.form.get(f"exp_start_{i}")
            end = request.form.get(f"exp_end_{i}")
            desc = request.form.get(f"exp_desc_{i}")

            if any([role, company, start, end, desc]):
                updated_data["experience"].append({
                    "role": role or "",
                    "company": company or "",
                    "start_date": start or "",
                    "end_date": end or "",
                    "description": desc or "",
                })

        # EDUCATION
        edu_count = int(request.form.get("edu_count", 0))
        for i in range(edu_count):
            if request.form.get(f"edu_delete_{i}"):
                continue

            degree = request.form.get(f"edu_degree_{i}")
            institution = request.form.get(f"edu_institution_{i}")
            year = request.form.get(f"edu_year_{i}")

            if any([degree, institution, year]):
                updated_data["education"].append({
                    "degree": degree or "",
                    "institution": institution or "",
                    "graduation_year": year or "",
                })

        profile.ai_cv_data = updated_data
        db.session.commit()

        return redirect(url_for("onboarding.cv_preview"))

    # Ensure fields exist in CV JSON
    def ensure_keys(cv):
        cv = cv or {}
        cv.setdefault("first_name", "")
        cv.setdefault("last_name", "")
        cv.setdefault("email", "")
        cv.setdefault("phone", "")
        cv.setdefault("address", "")
        cv.setdefault("summary", "")
        cv.setdefault("skills", [])
        cv.setdefault("job_titles", [])
        cv.setdefault("experience", [])
        cv.setdefault("education", [])
        cv.setdefault("certifications", [])
        cv.setdefault("languages", [])
        cv.setdefault("additional_details", {})
        add = cv["additional_details"]
        add.setdefault("publications", [])
        add.setdefault("github", "")
        add.setdefault("linkedin", "")
        add.setdefault("portfolio", "")
        add.setdefault("thesis", "")
        add.setdefault("awards", [])
        add.setdefault("volunteering", [])
        add.setdefault("interests", [])
        add.setdefault("other", "")
        return cv

    safe_cv = ensure_keys(profile.ai_cv_data)

    return render_template("cv_edit.html", cv=safe_cv, profile=profile)
