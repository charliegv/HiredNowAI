import requests
import os
from flask import render_template
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

MAILGUN_DOMAIN = os.getenv("MAILGUN_DOMAIN")
MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY")

def send_onboarding_bounce_email(user):
    html_body = render_template(
        "emails/onboarding_bounce.html",
        first_name=user.profile.first_name,
        year=datetime.utcnow().year
    )

    text_body = f"""
Hi {user.profile.first_name or ''},

You still have 5 free application credits waiting for you.

You can start applying to real company jobs today - no payment required.

Log back in to continue.
"""

    requests.post(
        f"https://api.eu.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
        auth=("api", MAILGUN_API_KEY),
        data={
            "from": f"HiredNow AI <no-reply@{MAILGUN_DOMAIN}>",
            "to": 'cgvinall@gmail.com', #[user.email],
            "subject": "You still have 5 free applications waiting",
            "text": text_body,
            "html": html_body,
        }
    )
