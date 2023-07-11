""""""
import re
import inspect
import logging
from datetime import datetime

from functools import wraps
from flask import request, g, redirect, url_for, render_template
from flask_login import current_user, login_required as flask_login_required
from werkzeug.exceptions import Unauthorized

from . import db, config, models, error, races, tracks, users

LOG = logging.getLogger("hawkspeed.decorators")
LOG.setLevel( logging.DEBUG )


def positional_to_keyword_args(**kwargs):
    """Nice:
    https://stackoverflow.com/a/59179221"""
    def decorator(f):
        @wraps(f)
        def decorated_view(*args, **kwargs):
            kwargs = { **kwargs, **{k: v for k, v in zip(list(inspect.signature(f).parameters), args)} }
            if "self" in kwargs:
                del (kwargs["self"])
            return f(*args, **kwargs)
        return decorated_view
    return decorator


def get_server_configuration(**kwargs):
    """Decorator that supplies the latest server configuration instance in use to keyword arguments."""
    def decorator(f):
        @wraps(f)
        def decorated_view(*args, **kwargs):
            # Get the current server configuration record.
            server_cfg = models.ServerConfiguration.get()
            # Set server_configuration in keywords.
            kwargs["server_configuration"] = server_cfg
            return f(*args, **kwargs)
        return decorated_view
    return decorator


def login_required(**kwargs):
    """A HawkSpeed-based login required decorator that will ensure, apart from the User's logged in status, that the User is verified and
    has completed their profile setup.

    Keyword arguments
    -----------------
    :verified_required: The user must be verified. Default is True."""
    verified_required = kwargs.get("verified_required", True)

    def decorator(f):
        @flask_login_required
        @wraps(f)
        def decorated_view(*args, **kwargs):
            # Check if current user is authenticated. If they are, we can check for verification.
            # Otherwise, we'll simply return the original function.
            if current_user.is_authenticated:
                # Check to ensure the User is enabled.
                if not current_user.enabled:
                    LOG.error(f"Failed to provide access to route at path; {request.path}; {current_user} is not enabled. Logging them out.")
                    # This will return a HTTP 401, which will totally log the User out.
                    raise error.AccountSessionIssueFail(error.AccountSessionIssueFail.ERROR_DISABLED)
                # Check to ensure the User is verified.
                if not current_user.verified and verified_required:
                    LOG.error(f"Failed to provide access to route at path; {request.path}; {current_user} is not yet verified.")
                    # This will require the client complete the account verified procedure prior to continuing anywhere. This won't log the User out, but will certainly pop their
                    # current view stack all the way back to verification requirements.
                    raise error.AccountActionNeeded(current_user, "setup", "account-not-verified")
            return f(*args, **kwargs)
        return decorated_view
    return decorator


def account_setup_required(**kwargs):
    """A decorator that will ensure, on top of the requirement that the User be fully verified (no password issues, account issues or
    community restrictions,) that the User has a FULLY setup account. In the case the User does not have one, they will be redirected
    to the appropriate browser-compatible account-setup route, which will manage how to proceed."""
    def decorator(f):
        @login_required()
        @wraps(f)
        def decorated_view(*args, **kwargs):
            if not current_user.is_setup:
                LOG.warning(f"{current_user} is not yet setup. Redirecting to setup route.") # (Mobile={g.IS_MOBILE})
                # This will require the client setup their profile.
                raise error.AccountActionNeeded(current_user, "setup", "profile")
            return f(*args, **kwargs)
        return decorated_view
    return decorator


def get_user(**kwargs):
    """Locate a User with the given User UID passed in keyword arguments.

    Keyword arguments
    -----------------
    :user_uid_key: The key under which the UID for the User to be grabbed. Default is 'user_uid'.
    :user_output_key: The key under which the located User should be passed. Default is 'user'.
    :required: A boolean; True if the route should fail if the User not found. Default is True."""
    user_uid_key = kwargs.get("user_uid_key", "user_uid")
    user_output_key = kwargs.get("user_output_key", "user")
    required = kwargs.get("required", True)

    def decorator(f):
        @account_setup_required()
        @wraps(f)
        def decorated_view(*args, **kwargs):
            # Get the incoming User UID.
            user_uid = kwargs.get(user_uid_key, None)
            # Attempt to find the User.
            user = users.find_existing_user(
                user_uid = user_uid)
            # If no User found, raise an error.
            if not user:
                """TODO: handle this properly."""
                raise NotImplementedError("Could not find user by UID in decorator. This is not handled either.")
            # Finally, pass the User back via output key.
            kwargs[user_output_key] = user
            # And call original function.
            return f(*args, **kwargs)
        return decorated_view
    return decorator


def get_track(**kwargs):
    """Get a track from the incoming keyword arguments. If the route is not found, this decorator will raise an exception. It is required that the User's account
    is totally setup prior to using any routes decorated with this.

    Keyword arguments
    -----------------
    :track_uid_key: The key under which the UID for the track to be grabbed. Default is 'track_uid'.
    :track_output_key: The key under which the located track should be passed. Default is 'track'.
    :should_belong_to_user: True if the track should belong to the current User, or raise an exception if it does not. Default is True."""
    track_uid_key = kwargs.get("track_uid_key", "track_uid")
    track_output_key = kwargs.get("track_output_key", "track")
    should_belong_to_user = kwargs.get("should_belong_to_user", True)

    def decorator(f):
        @account_setup_required()
        @wraps(f)
        def decorated_view(*args, **kwargs):
            # Get the incoming track UID.
            track_uid = kwargs.get(track_uid_key, None)
            # Attempt to find the Track.
            track = tracks.find_existing_track(
                track_uid = track_uid)
            # If no track found, raise an error.
            if not track:
                """TODO: handle this properly."""
                raise NotImplementedError("Could not find track by UID in decorator. This is not handled either.")
            # Otherwise, if it should belong to User and it doesn't raise.
            if should_belong_to_user and track.user != current_user:
                """TODO: handle this properly."""
                raise NotImplementedError("Track is not owned by the User requesting it, but this is required. This is not handled either.")
            # Finally, pass the track back via output key.
            kwargs[track_output_key] = track
            # And call original function.
            return f(*args, **kwargs)
        return decorated_view
    return decorator


def get_race(**kwargs):
    """Locate a Race with the given Race UID passed in keyword arguments.

    Keyword arguments
    -----------------
    :race_uid_key: The key under which the UID for the Race to be grabbed. Default is 'race_uid'.
    :race_output_key: The key under which the located Race should be passed. Default is 'race'.
    :required: A boolean; True if the route should fail if the Race not found. Default is True."""
    race_uid_key = kwargs.get("race_uid_key", "race_uid")
    race_output_key = kwargs.get("race_output_key", "race")
    required = kwargs.get("required", True)

    def decorator(f):
        @account_setup_required()
        @wraps(f)
        def decorated_view(*args, **kwargs):
            # Get the incoming Race UID.
            race_uid = kwargs.get(race_uid_key, None)
            # Attempt to find the Race.
            race = races.find_existing_user(
                race_uid = race_uid)
            # If no Race found, raise an error.
            if not race:
                """TODO: handle this properly."""
                raise NotImplementedError("Could not find race by UID in decorator. This is not handled either.")
            # Finally, pass the Race back via output key.
            kwargs[race_output_key] = race
            # And call original function.
            return f(*args, **kwargs)
        return decorated_view
    return decorator