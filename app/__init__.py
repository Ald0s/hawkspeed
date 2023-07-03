"""The main module for the HawkSpeed project.

To Do
-----
2. Need to migrate to SQLAlchemy 2.0

Changes
-------
"""

import os
import logging

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_socketio import SocketIO
from flask_migrate import Migrate
from werkzeug.middleware.proxy_fix import ProxyFix

from . import config, compat
compat.monkey_patch_sqlite()

LOG = logging.getLogger("hawkspeed")
LOG.setLevel( logging.DEBUG )

db = SQLAlchemy(
    session_options = config.SQLALCHEMY_SESSION_OPTS,
    engine_options = config.SQLALCHEMY_ENGINE_OPTS
)
migrate = Migrate()
login_manager = LoginManager()
socketio = SocketIO()

from .api import api as api_blueprint
from .frontend import frontend as frontend_blueprint
from .socket import setup_socketio
from . import handler


def create_app():
    logging.info(f"Creating Flask instance in the '{config.APP_ENV}' environment")
    app = Flask(__name__, instance_path = os.path.join(os.getcwd(), config.INSTANCE_PATH))
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for = config.FORWARDED_FOR,
        x_proto = config.FORWARDED_PROTO,
        x_host = config.FORWARDED_HOST,
        x_port = config.FORWARDED_PORT,
        x_prefix = config.FORWARDED_PREFIX)
    app.config.from_object(config)
    app.url_map.strict_slashes = False
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    socketio.init_app(app)
    with app.app_context():
        # If required, load the spatialite mod onto the sqlite driver.
        if db.engine.dialect.name == "sqlite":
            compat.should_load_spatialite_sync(db.engine)
        app.register_blueprint(api_blueprint)
        app.register_blueprint(frontend_blueprint)
        setup_socketio(socketio)
        # Configure our login manager.
        handler.configure_login_manager(app)
        if config.APP_ENV != "Test":
            pass
    return app
