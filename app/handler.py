import os
import re
import json
import time
import logging
from datetime import datetime

from flask import request, render_template, redirect, flash, url_for, send_from_directory, abort, jsonify, current_app
from flask_login import login_required, current_user, login_user, logout_user
from marshmallow import Schema, fields

from . import db, config, login_manager, models, error

LOG = logging.getLogger("hawkspeed.handler")
LOG.setLevel( logging.DEBUG )


@login_manager.user_loader
def load_user(id):
    return models.User.query.get(int(id))


@login_manager.unauthorized_handler
def unauthorized():
    """Called when the current user is not authenticated."""
    raise error.AccountSessionIssueFail("session-expired-login")


def configure_app_handlers(app):
    @app.errorhandler(404)
    def handle_not_found(e):
        """"""
        raise NotImplementedError()
