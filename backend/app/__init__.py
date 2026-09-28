import os

from flask import Flask, send_from_directory
from flask_cors import CORS

from .config import Config
from .errors import register_error_handlers
from .extensions import db

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))


def create_app(config_class=Config):
    app = Flask(__name__, static_folder=None)
    app.config.from_object(config_class)

    db.init_app(app)
    from . import models  # noqa: F401  (register tables for create_all)
    from .reference import models as reference_models  # noqa: F401
    CORS(app, resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}})

    from .routes.admin import bp as admin_bp
    from .routes.explore import bp as explore_bp
    from .routes.feedback import bp as feedback_bp

    app.register_blueprint(explore_bp, url_prefix="/api")
    app.register_blueprint(feedback_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")

    from .cli import register_cli

    register_cli(app)
    register_error_handlers(app)

    # The frontend is plain static files; Flask serves them so no separate dev server is needed.
    @app.get("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/admin")
    def admin():
        return send_from_directory(FRONTEND_DIR, "admin.html")

    @app.get("/<path:path>")
    def frontend_files(path):
        return send_from_directory(FRONTEND_DIR, path)

    return app
