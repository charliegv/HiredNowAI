from flask import Flask, render_template, Response, request, session
from config import Config
from models import db
from auth import auth
from dashboard import dashboard
from onboarding import onboarding
from profile import preferences
from flask_login import LoginManager
from contact import contact
from admin.routes import admin_bp
from billing import billing_bp
from werkzeug.exceptions import HTTPException

UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = "auth.login"
    login_manager.init_app(app)

    from models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # -----------------------------
    # REGISTER BLUEPRINTS
    # -----------------------------

    app.register_blueprint(auth)
    app.register_blueprint(dashboard)
    app.register_blueprint(preferences)
    app.register_blueprint(onboarding)

    # >>> ADD THIS PART <<<

    app.register_blueprint(billing_bp, url_prefix="/billing")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    app.register_blueprint(contact)

    return app


app = create_app()


@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return e

    app.logger.error(f"Unhandled error: {e}", exc_info=True)
    return render_template("error.html"), 500


@app.route("/robots.txt")
def robots():
    return Response(
        "User-agent: *\nAllow: /\n",
        mimetype="text/plain"
    )


@app.route("/health")
def health():
    return "ok", 200


@app.before_request
def capture_utm_to_session():
    if session.get("utm_captured"):
        return

    found_any = False

    for key in UTM_KEYS:
        value = request.args.get(key)
        if value and not session.get(key):
            session[key] = value
            found_any = True

    if found_any:
        session["utm_captured"] = True

        session["first_landing_path"] = request.full_path
        session["first_referrer"] = request.headers.get("Referer")



if __name__ == "__main__":
    app.run()
