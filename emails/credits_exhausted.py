import requests
import os
from flask import render_template, url_for
from datetime import datetime

MAILGUN_DOMAIN = os.getenv("MAILGUN_DOMAIN")
MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY")


def send_credits_exhausted_email(user):

    html_body = render_template(
        "emails/credits_exhausted.html",
        year=datetime.utcnow().year,
    )

    text_body = f"""
You have used all your application credits

Your automation is paused because you have no credits remaining.

Log in to continue applying:
https://app.hirednowai.com/
"""

    return requests.post(
        f"https://api.eu.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
        auth=("api", MAILGUN_API_KEY),
        data={
            "from": f"HiredNow AI <no-reply@{MAILGUN_DOMAIN}>",
            "to": [user.email],
            "subject": "Your applications are paused – add credits to continue",
            "text": text_body,
            "html": html_body,
        }
    )
