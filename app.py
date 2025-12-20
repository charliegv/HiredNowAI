from flask import Flask, render_template
from config import Config
from models import db
from auth import auth
from dashboard import dashboard
from onboarding import onboarding
from profile import preferences
from flask_login import LoginManager



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
    from admin.routes import admin_bp
    from billing import billing_bp

    app.register_blueprint(billing_bp, url_prefix="/billing")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    return app


app = create_app()


@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error(f"Unhandled error: {e}")
    return render_template("error.html"), 500


if __name__ == "__main__":
    app.run()
