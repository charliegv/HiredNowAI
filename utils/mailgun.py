import os
import requests

MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY")
MAILGUN_DOMAIN = os.getenv("MAILGUN_DOMAIN")
FROM_EMAIL = os.getenv("SUPPORT_FROM_EMAIL", "support_noreply@notifications.hirednowai.com")
MAILGUN_API_BASE = "https://api.eu.mailgun.net/v3"


def send_contact_reply(to_email: str, subject: str, body: str):
	return requests.post(
		f"{MAILGUN_API_BASE}/{MAILGUN_DOMAIN}/messages",
		auth=("api", MAILGUN_API_KEY),
		data={
			"from": f"HiredNow AI <no-reply@{MAILGUN_DOMAIN}>",
			"to": [to_email],
			"subject": subject,
			"text": body,
		},
		timeout=10,
	)
