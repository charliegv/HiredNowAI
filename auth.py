from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session
from flask_login import login_user, logout_user, login_required
from flask_bcrypt import Bcrypt
from models import db, User, Profile
from itsdangerous import URLSafeTimedSerializer
import requests
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

MAILGUN_DOMAIN = os.getenv("MAILGUN_DOMAIN")
MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY")

auth = Blueprint('auth', __name__)
bcrypt = Bcrypt()



@auth.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash("An account with this email already exists. Please log in.", "error")
            return redirect(url_for("auth.login"))

        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        user = User(email=email, password_hash=hashed_pw)

        db.session.add(user)
        db.session.commit()

        # Create an empty profile linked to the new user
        profile = Profile(user_id=user.id)

        profile.utm_source = session.get("utm_source")
        profile.utm_medium = session.get("utm_medium")
        profile.utm_campaign = session.get("utm_campaign")
        profile.utm_content = session.get("utm_content")
        profile.utm_term = session.get("utm_term")
        profile.first_landing_path = session.get("first_landing_path")
        profile.first_referrer = session.get("first_referrer")

        db.session.add(profile)
        db.session.commit()

        login_user(user)
        return redirect(url_for("onboarding.step1"))

    return render_template("signup.html")



@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        user = User.query.filter_by(email=email).first()

        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for("dashboard.dashboard_home"))

        flash("Invalid credentials", "error")

    return render_template("login.html")


@auth.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))



def generate_reset_token(email):
    s = URLSafeTimedSerializer(current_app.secret_key)
    return s.dumps(email, salt="password-reset")

def confirm_reset_token(token, expiration=3600):
    s = URLSafeTimedSerializer(current_app.secret_key)
    try:
        return s.loads(token, salt="password-reset", max_age=expiration)
    except Exception:
        return None



def send_reset_email(email, token):
    reset_url = url_for("auth.reset_password", token=token, _external=True)

    html_body = render_template(
        "emails/reset_password_email.html",
        reset_url=reset_url,
        year=datetime.utcnow().year
    )

    text_body = f"""
	Reset your HiredNow AI password
	
	Click the link below to create a new password:
	{reset_url}
	
	If you did not request this, you can ignore this message.
	This link expires in 1 hour.
	"""

    return requests.post(
        f"https://api.eu.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
        auth=("api", MAILGUN_API_KEY),
        data={
            "from": f"HiredNow AI <no-reply@{MAILGUN_DOMAIN}>",
            "to": [email],
            "subject": "Reset your password",
            "text": text_body,
            "html": html_body,
        }
    )

@auth.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email")
        user = User.query.filter_by(email=email).first()

        if user:
            token = generate_reset_token(email)
            send_reset_email(email, token)
            flash("A password reset link has been sent to your email.", "success")
        else:
            flash("If that email exists, you will receive a reset link.", "info")

        return redirect(url_for("auth.forgot_password"))

    return render_template("forgot_password.html")


@auth.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    email = confirm_reset_token(token)
    if not email:
        flash("This password reset link has expired or is invalid.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        password = request.form.get("password")
        user = User.query.filter_by(email=email).first()

        if user:
            user.password_hash = bcrypt.generate_password_hash(password).decode()
            db.session.commit()
            flash("Your password has been reset. You can now log in.", "success")
            return redirect(url_for("auth.login"))

    return render_template("reset_password.html", token=token)

