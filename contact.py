from flask import Blueprint, render_template, request, flash, redirect, url_for
from models import db, ContactMessage
import time

contact = Blueprint("contact", __name__)

@contact.route("/contact", methods=["GET", "POST"])
def contact_page():
    if request.method == "POST":

        # Honeypot check
        if request.form.get("company"):
            return "", 204  # silently drop bots

        # Time-based check
        loaded_at = request.form.get("form_loaded_at", type=float)
        if not loaded_at or time.time() - loaded_at < 3:
            return "", 204  # submitted too fast = bot
        name = request.form.get("name")
        email = request.form.get("email")
        subject = request.form.get("subject")
        message = request.form.get("message")

        if not name or not email or not message:
            flash("Please fill in all required fields", "error")
            return redirect(request.url)

        db.session.add(
            ContactMessage(
                name=name,
                email=email,
                subject=subject,
                message=message,
            )
        )
        db.session.commit()

        flash("Thanks for contacting us. We will get back to you shortly.", "success")
        return redirect(url_for("contact.contact_page"))

    return render_template("contact.html", time=time.time)
