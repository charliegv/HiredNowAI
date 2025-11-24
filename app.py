from flask import Flask
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

    app.register_blueprint(auth)
    app.register_blueprint(dashboard)
    app.register_blueprint(preferences)
    app.register_blueprint(onboarding)

    return app

app = create_app()

if __name__ == "__main__":
    app.run()
