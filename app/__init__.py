from flask import (
    Flask,
    jsonify,
    render_template,
)
from flask_cors import CORS
from flask_qrcode import QRcode
from flask_session import Session
import logging
import os
from asyncio import run as asyncio_run

from config import Config
from app.plugins import initialize_askar_storage
from app.handlers.errors import bp as errors_bp
from app.routes.main import bp as main_bp
from app.routes.auth import bp as auth_bp
from app.routes.credentials import bp as credentials_bp
from app.routes.webhooks import bp as webhooks_bp
import json


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    asyncio_run(initialize_askar_storage())
    
    # Configure logging
    log_level = os.getenv('PYDENTITY_LOG_LEVEL', 'INFO').upper()
    app.logger.setLevel(getattr(logging, log_level, logging.INFO))
    
    # Create console handler with formatting
    if not app.logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(getattr(logging, log_level, logging.INFO))
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        console_handler.setFormatter(formatter)
        
        app.logger.addHandler(console_handler)

    @app.route("/manifest.json")
    @app.route("/manifest.webmanifest")
    def manifest():
        # Use ngrok URL if available, otherwise use configured APP_URL
        app_url = app.config.get('NGROK_URL') or Config.APP_URL
        manifest_content = json.loads(
            render_template(
                "manifest.jinja", app_url=app_url, app_name=Config.APP_NAME
            )
        )
        return jsonify(manifest_content)

    @app.route("/install")
    def install():
        # Use ngrok URL if available, otherwise use configured APP_URL
        app_url = app.config.get('NGROK_URL') or Config.APP_URL
        return render_template(
            "pages/install.jinja",
            app_url=app_url,
            demo_url=Config.DEMO_ANONCREDS,
            app_name=Config.APP_NAME,
            project_url=Config.PROJECT_URL,
        )

    CORS(app)
    QRcode(app)
    Session(app)

    app.register_blueprint(errors_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(credentials_bp)
    app.register_blueprint(webhooks_bp, url_prefix="/webhooks")


    return app
