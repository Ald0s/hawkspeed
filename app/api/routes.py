import os
import re
import json
import time
import logging
from datetime import date, datetime

from flask import request, render_template, redirect, flash, url_for, send_from_directory, abort, jsonify, g
from flask_login import current_user, login_user, logout_user, login_required
from marshmallow import Schema, fields, validate, ValidationError, post_load, pre_load, EXCLUDE
from werkzeug.exceptions import RequestEntityTooLarge

from .. import db, config, login_manager, models, decorators, error, account, viewmodel
from . import api

LOG = logging.getLogger("hawkspeed.api.routes")
LOG.setLevel( logging.DEBUG )


@api.route("/api/v1/auth", methods = [ "POST" ])
def authenticate(**kwargs):
    """Authenticate the current User. You don't need to provide credentials, just an existing session token as a Cookie. Otherwise, an authorization header is required
    where the username is actually the user's email address. This function will respond with a serialised account view model on success."""
    try:
        # If the user is ALREADY logged in, the login success response will be returned without any other functions.
        if not current_user.is_authenticated:
            # Current user requires logging in first.
            # Validate the authorization header.
            authorization = request.authorization
            if not authorization or not "username" in authorization or not "password" in authorization:
                LOG.error(f"{request.remote_addr} failed to authenticate; invalid authorization header.")
                raise error.UnauthorisedRequestFail("bad-auth-header")
            # Load and login the account from the auth header.
            request_login_local = account.RequestLoginLocalSchema().load(dict(
                email_address = authorization.get("username"),
                password = authorization.get("password"),
                remember_me = True
            ))
            # Use the account module to login, then commit whatever changes were made.
            logged_in_user = account.login_local_account(request_login_local)
            db.session.commit()
        # Now, we will check for anything that requires immediate attention by the User; such as account verification, password verification, community restrictions etc.
        @decorators.account_setup_required()
        def ensure_passes_account_checks():
            """Ensures we will pass all checks in decorators login_required and account_setup_required.
            This means the User should have their account verified, password verified and all aspects of their profile setup.
            Otherwise, errors served requiring these."""
            pass
        try:
            ensure_passes_account_checks()
        except error.AccountActionNeeded as accn:
            # We can pass on account action needed.
            pass
        except Exception as e:
            # Raise on all other errors.
            raise e
        # Instantiate a new account view model, and return its serialisation.
        account_view_model = viewmodel.AccountViewModel(current_user)
        return account_view_model.serialise(), 200
    except Exception as e:
        raise e


@api.route("/api/v1/logout", methods = [ "POST" ])
@decorators.login_required(verified_required = False)
def logout(**kwargs):
    """Logout the current User, but only if the User is currently authenticated.
    Either way, return a successful status."""
    try:
        # Use account module to log the User out.
        account.logout_local_account()
        # Commit and return a successful status.
        db.session.commit()
        # Instantiate a new account view model, and return its serialisation.
        account_view_model = viewmodel.AccountViewModel(current_user)
        return account_view_model.serialise(), 200
    except Exception as e:
        raise e


@api.route("/api/v1/register", methods = [ "POST" ])
def register_local_account(**kwargs):
    """Handle a registration attempt from the User.
    This route expects a JSON body, which should be a RequestNewLocalAccountSchema."""
    try:
        """TODO: first, some controls on registration here. Ensure the User can actually register new accounts"""
        # The User wishes to register a new local account. This means the JSON contents can be loaded into a RequestNewLocalAccountSchema.
        request_local_account = account.RequestNewLocalAccountSchema().load(request.json)
        # Attempt to create a new account with this.
        new_account = account.create_local_account(request_local_account)
        LOG.debug(f"{current_user} successfully registered a new account via HawkSpeed! ({new_account.email_address})")
        db.session.commit()
        # Simply return a 201 created, alongside the new User's email address.
        schema = account.RegistrationResponseSchema()
        return schema.dump(new_account), 201
    except Exception as e:
        raise e


@api.route("/api/v1/setup/name/<username>", methods = [ "POST" ])
@decorators.login_required()
def check_username_taken(username, **kwargs):
    """Check whether the username given is already taken by another user. Provide a username in the query path to use the route.
    The reply will be type of CheckNameResponseSchema."""
    try:
        if not username:
            LOG.error(f"An invalid username was provided to check_username_taken")
            raise error.BadRequestArgumentFail("bad-arguments")
        # Check whether this username is taken.
        is_taken = account.check_name_taken(username)
        # Now, return the response schema.
        schema = account.CheckNameResponseSchema()
        return schema.dump(dict(
            username = username,
            is_taken = is_taken
        )), 200
    except Exception as e:
        raise e


@api.route("/api/v1/setup", methods = [ "POST" ])
@decorators.login_required()
def setup_profile(**kwargs):
    """Setup a users profile on their account; this includes their username, bio and profile image.
    This can only be completed once. The route expects a JSON body, which should be a RequestSetupProfileSchema."""
    try:
        # If profile is already setup, simply return a successful state.
        if current_user.is_profile_setup:
            LOG.warning(f"{current_user} tried setting up their profile twice. It is already setup.")
            setup_profile_user = current_user
        else:
            # Otherwise, load a RequestSetupProfileSchema from the JSON body.
            request_setup_profile_d = account.RequestSetupProfileSchema().load(request.json)
            # Now, use account module to setup the user's account.
            setup_profile_user = account.setup_account_profile(current_user, request_setup_profile_d)
            LOG.debug(f"Successfully setup account profile for {current_user}")
            db.session.commit()
        # Instantiate a new account view model, and return its serialisation.
        account_view_model = viewmodel.AccountViewModel(setup_profile_user)
        return account_view_model.serialise(), 200
    except Exception as e:
        raise e


@api.route("/api/v1/track/<track_uid>", methods = [ "GET" ])
@decorators.account_setup_required()
@decorators.get_track(should_belong_to_user = False)
def get_track(track, **kwargs):
    """Perform a GET request with a track's UID to get its detail here. The User must be authenticated and their profile must be set up for them to have access to this."""
    try:
        # Once we've got the track instance, we will instantiate a track view model, and return its serialisation.
        track_view_model = viewmodel.TrackViewModel(current_user, track)
        # Now, return the serialisation.
        return track_view_model.serialise(), 200
    except Exception as e:
        raise e


@api.route("/api/v1/track/<track_uid>/leaderboard", methods = [ "GET" ])
@decorators.account_setup_required()
@decorators.get_track(should_belong_to_user = False)
def page_track_leaderboard(track, **kwargs):
    """Perform a GET request to page the leaderboard for the given track. Supply a query argument 'p' to identify the page we have requested. On success, the route will
    return a page object containing the Track, ordered finished race outcomes, the current page number and the next page number (or None if there are no more.)"""
    try:
        # Get the page argument. By default, page one.
        page = request.args.get("p", 1)
        # With the track and the requested page, create a new track view model and get back a SerialisablePagination object from the view model.
        track_view_model = viewmodel.TrackViewModel(current_user, track)
        leaderboard_sp = track_view_model.page_leaderboard(page)
        # Now, return this as a paged response, providing a base dict containing the serialised track view model, too.
        return leaderboard_sp.as_paged_response(base_dict = dict(
            track = track_view_model.serialise()
        )), 200
    except Exception as e:
        raise e


@api.route("/api/v1/track/<track_uid>/path", methods = [ "GET" ])
@decorators.account_setup_required()
@decorators.get_track(should_belong_to_user = False)
def get_track_path(track, **kwargs):
    """Perform a GET request with a track's UID to get its full path. The User must be authenticated and their profile must be set up for them to have access to this."""
    try:
        # Once we've got the track instance, we will instantiate a track view model. From it, we'll return the path's object.
        track_view_model = viewmodel.TrackViewModel(current_user, track)
        # Return the serialisation of the track path.
        return track_view_model.serialise_path(), 200
    except Exception as e:
        raise e


@api.route("/api/v1/track/new", methods = [ "PUT" ])
@decorators.account_setup_required()
def new_track(**kwargs):
    """Perform a PUT request alongside the requirements for a brand new Track to create one here. The body should be a JSON object, containing the values found
    in the LoadTrackSchema. This route will first ensure the User is allowed to create tracks. The Track will be created and then serialised and returned."""
    try:
        # Get the new track JSON from the request.
        new_track_json = request.json
        # Now, create a new account view model for this User; this will let us know if the User can create tracks.
        account_view_model = viewmodel.AccountViewModel(current_user)
        if not account_view_model.can_create_tracks:
            """TODO: handle this permission issue"""
            raise NotImplementedError(f"{current_user} failed to create a new track, they are not allowed to.")
        # Use the account view model to create the new track. Receive back a TrackViewModel.
        track_view_model = account_view_model.create_track(new_track_json)
        # Commit to the database.
        db.session.commit()
        # Reply with the serialised track.
        return track_view_model.serialise(), 200
    except Exception as e:
        raise e


@api.route("/api/v1/track/<track_uid>/manage", methods = [ "GET", "POST", "DELETE" ])
@decorators.account_setup_required()
@decorators.get_track(should_belong_to_user = True)
def manage_track(track, **kwargs):
    """Perform a GET request to view the track from a management perspective, a POST request to perform an update on the desired track, or a
    DELETE request to delete the track. These operations can only be performed by the track's owner and creator."""
    try:
        raise NotImplementedError()
    except Exception as e:
        raise e


@api.errorhandler(error.AccountActionNeeded)
def account_action_needed(e):
    """An action of some description is needed."""
    if e.action_needed_category_code == "setup":
        # The User needs to be setup somehow.
        LOG.debug(f"{current_user} requires setting up to continue via API.")
        # Check the inner reason code, and return a requirement on that basis.
        if e.action_needed_code == "profile":
            # Require the procedure setup-profile. This will represent as a global API error, which could cause the current activity to pop all open items.
            return error.GlobalAPIError(error.ProcedureRequiredException("setup-profile"), 400).to_response()
        elif e.action_needed_code == "account-not-verified":
            # Require the procedure verify-account. This will represent as a global API error, which could cause the current activity to pop all open items.
            return error.GlobalAPIError(error.ProcedureRequiredException("verify-account"), 400).to_response()
        else:
            LOG.debug(f"{current_user} has been directed toward action needed for 'setup', but required code ({e.action_needed_code}) has no handle, or is not required. Instructing client to hard restart.")
            # Raise a device issue fail, with type reload, this will cause the whole app to restart and require login once again.
            return error.GlobalAPIError(error.DeviceIssueFail("reload"), 400).to_response()
    else:
        raise NotImplementedError(f"account_action_needed when action_needed_category_code is {e.action_needed_category_code} not implemented.")


@api.errorhandler(Exception)
def handle_exception(e):
    """An API exception handler for ALL uncaught exceptions.
    This will differentiate between the exception types that are always global exceptions, and those that are local."""
    if isinstance(e, error.AccountSessionIssueFail):
        # This a global error to do with the User's account, meaning that the User's account session is invalid, expired or otherwise unacceptable. On the client, the reception of any 401
        # status should result in the clearing of the account information and the absolute exit from authenticated activities. An example of a request that falls under this category could
        # be the User account being disabled as its being used. Navigating to ANY protected view will result in this 401.
        # Serve as a GlobalAPIError and HTTP status 401.
        # Prior to actually returning the response we'll first log the User out via the account module.
        account.logout_user()
        return error.GlobalAPIError(e, 401).to_response()
    elif isinstance(e, error.DeviceIssueFail):
        # The device is invalid for some reason. Serve a global API error alongside 400.
        return error.GlobalAPIError(e, 400).to_response()
    elif isinstance(e, error.ProcedureRequiredException):
        # Serve procedure required exception has a global API error, with code 400.
        return error.GlobalAPIError(e, 400).to_response()
    elif isinstance(e, error.UnauthorisedRequestFail):
        # By default, serve as a LocalAPIError with HTTP 403 (Unauthorised) as status.
        # The 403 status means that the User's request itself is not authorised, and should only move as far as the local request or attempt at hand, and should not clear any account info.
        # For example, a 403 may be an incorrect login attempt, or attempting to view a resource that you don't own.
        return error.LocalAPIError(e, 403).to_response()
    elif isinstance(e, ValidationError):
        # By default, serve all validation errors as an API validation error, local API error with HTTP code 400.
        LOG.debug(f"Request failed with validation error: {e}")
        return error.LocalAPIError(error.APIValidationError(e.messages), 400).to_response()
    else:
        # Otherwise, its some other exception that's unhandled. We'll log this, then force the User to logout.
        LOG.error(f"Handle exception called for {e}, this type is not yet supported!")
        LOG.error(e, exc_info = True)
        return error.GlobalAPIError(error.OperationalFail("unknown-error-relog"), 400).to_response()
