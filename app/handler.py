import os
import re
import json
import time
import logging
from datetime import datetime

from . import db, config, login_manager, models, error

LOG = logging.getLogger("hawkspeed.handler")
LOG.setLevel( logging.DEBUG )


@login_manager.user_loader
def load_user(id):
    return db.session.get(models.User, int(id))


@login_manager.unauthorized_handler
def unauthorized():
    """Called when the current user is not authenticated. We'll simply raise an account session issue fail. This should ultimately fall down to the
    appropriate handler; either on API or frontend."""
    raise error.AccountSessionIssueFail(error.AccountSessionIssueFail.ERROR_UNAUTHORISED)


def configure_login_manager(app):
    """Configure our login manager here."""
    # Set the anonymous user we'll use.
    login_manager.anonymous_user = models.AnonymousUser