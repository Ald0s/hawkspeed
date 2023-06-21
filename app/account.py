""""""
import re
import time
import emoji
import hashlib
import logging

from datetime import datetime, date
from flask_login import login_user, logout_user, current_user
from email_validator import validate_email, EmailNotValidError
from password_strength import PasswordPolicy
from marshmallow import Schema, fields, EXCLUDE, post_load, ValidationError, validates, pre_load

from . import db, config, models, factory, decorators, error

LOG = logging.getLogger("hawkspeed.account")
LOG.setLevel( logging.DEBUG )

account_password_policy = PasswordPolicy.from_names(
    length = 8,  # min length: 8
    uppercase = 1,  # need min. 1 uppercase letters
    numbers = 1,  # need min. 1 digits
    special = 1,  # need min. 1 special characters
)


class RequestLoginLocal():
    """A container for a request for a login local."""
    def __init__(self, **kwargs):
        self.email_address = kwargs.get("email_address")
        self.password = kwargs.get("password")
        self.remember_me = kwargs.get("remember_me")


class RequestLoginLocalSchema(Schema):
    """Defines the base schema for logging into a local account.
    This requires an email address and a password."""
    class Meta:
        unknown = EXCLUDE
    email_address           = fields.Str()
    password                = fields.Str()
    remember_me             = fields.Bool()

    @validates("email_address")
    def validate_email_address(self, value):
        """Ensure the email address is valid.

        Raises
        ------
        ValidationError
        :invalid-email-address: The email isn't valid."""
        if not value:
            LOG.error(f"Failed to parse login local account - email is invalid")
            raise ValidationError("invalid-email-address")
        try:
            validate_email(value)
        except EmailNotValidError as enve:
            LOG.error(f"Failed to parse login local account - email is invalid")
            raise ValidationError("invalid-email-address")

    @validates("password")
    def validate_password(self, value):
        """Ensure the password is at least one character long.

        Raises
        ------
        ValidationError
        :password-too-short: The password is too short."""
        if not value:
            LOG.error(f"Failed to parse login local account - password is too short")
            raise ValidationError("password-too-short")

    @post_load
    def request_login_local_post_load(self, data, **kwargs) -> RequestLoginLocal:
        return RequestLoginLocal(**data)


class RegistrationResponseSchema(Schema):
    """A schema that defines the message sent back upon a successful registration."""
    email_address           = fields.Str()


class RequestNewAccountBaseSchema(Schema):
    """Defines the base schema for a brand new account. This is essentially all the information that must be provided upon
    creation of the account."""
    class Meta:
        unknown = EXCLUDE
    email_address           = fields.Str()

    @validates("email_address")
    def validate_email_address(self, value):
        """The user's email address.
        Must be provided, and must be a valid email address.
        There must be no existing User with this email address. If there is a duplicate User with the email address, but they
        are not yet verified, a specific error is given for this case reporting that the email address will be unlocked for
        use after some amount of time.

        Raises
        ------
        ValidationError
        :email-too-short: Email address is too short.
        :invalid-email-address: Given email address is not a valid email address.
        :email-address-registered: The given email address is already registered, but has NOT yet been verified.
        :email-address-registered-verified: The given email address is already registered and verified."""
        if not len(value):
            LOG.error(f"Failed to create a new account - email address is too short.")
            raise ValidationError("email-too-short")
        try:
            validate_email(value)
        except EmailNotValidError as enve:
            raise ValidationError("invalid-email-address")
        # Is the email address already registered?
        existing_user_check = models.User.search(email_address = value)
        if existing_user_check:
            # User exists. Now, the specific error returned depends on whether this is verified or not.
            if not existing_user_check.verified:
                LOG.error(f"Failed to create a new account - email address is already registered. However, the account is not verified yet. If their verification expires, this email will be available again.")
                raise ValidationError("email-address-registered")
            else:
                LOG.error(f"Failed to create a new account - email address is already registered.")
                raise ValidationError("email-address-registered-verified")


class RequestNewLocalAccount():
    """A container for a loaded request for a new local account."""
    def __init__(self, **kwargs):
        self.email_address = kwargs.get("email_address")
        self.password = kwargs.get("password")
        self.confirm_password = kwargs.get("confirm_password")


class RequestNewLocalAccountSchema(RequestNewAccountBaseSchema):
    """Defines the data for setting up an account via HawkSpeed- this involves a password provided by the User and
    will result in the requirement for the User to verify the supplied information."""
    # Required explicitly set to True as this is a jimmy-rigged way of running password first, so that password attribute can be set.
    password                = fields.Str( required = True )
    confirm_password        = fields.Str()

    @pre_load
    def save_password(self, data, **kwargs):
        self.password = data["password"]
        return data

    @validates("password")
    def validate_password(self, value):
        """Validate the requested password.
        The password must satisfy our password policy to ensure security.

        Raises
        ------
        ValidationError
        :password-not-complex: The password does not satisfy the policy."""
        if len(account_password_policy.test(value)) > 0:
            LOG.error(f"Failed to create a new account - password is not complex enough.")
            raise ValidationError("password-not-complex")

    @validates("confirm_password")
    def validate_confirm_password(self, value):
        """Validate the requested password against the confirm password.
        The passwords must match.

        Raises
        ------
        ValidationError
        :passwords-dont-match: The password and confirmation passwords don't match."""
        if self.password != value:
            LOG.error(f"Failed to create a new account - passwords don't match.")
            raise ValidationError("passwords-dont-match")
    
    @post_load
    def request_new_local_account_post_load(self, data, **kwargs) -> RequestNewLocalAccount:
        return RequestNewLocalAccount(**data)


class CheckNameResponseSchema(Schema):
    """Defines a schema for reporting whether a username has been taken or not."""
    username                = fields.Str()
    is_taken                = fields.Bool()


class RequestSetupProfile():
    """A container for a loaded request to setup a profile."""
    def __init__(self, **kwargs):
        #profile_image
        self.username = kwargs.get("username")
        self.bio = kwargs.get("bio")
        self.vehicle_information = kwargs.get("vehicle_information")


class RequestSetupProfileSchema(Schema):
    """Defines the data required for completing the profile setup step when creating a new account. This is done after
    verification, and allows the User the chance to setup their username, bio and profile image."""
    class Meta:
        unknown = EXCLUDE
    #profile_image           = media.MediaField(allow_none = True)
    username                = fields.Str(allow_none = False)
    bio                     = fields.Str(load_default = "", allow_none = True)
    vehicle_information     = fields.Str(allow_none = False)

    @validates("username")
    def validate_username(self, value):
        """Validate the requested Username.
        This must be unique, contain no spaces, special characters (aside from '_') or emojis
        and can be no longer than 32 characters in length.

        Raises
        ------
        ValidationError
        :no-username: A username has not been provided.
        :username-registered: A User is already registered with this Username.
        :username-too-long: The username is longer than 32 characters.
        :username-invalid: The username contains invalid characters."""
        if not value or not len(value):
            LOG.error(f"Failed to setup social account, no username was given!")
            raise ValidationError("no-username")
        elif len(value) > 32:
            LOG.error(f"Failed to setup social account, username was too long!")
            raise ValidationError("username-too-long")
        elif emoji.emoji_list(value):
            LOG.error(f"Failed to setup social account, invalid username; contains emojis!")
            raise ValidationError("username-invalid")
        elif not re.match(r"^[\S_]+$", value):
            LOG.error(f"Failed to setup social account, invalid username; contains spaces!")
            raise ValidationError("username-invalid")
        elif models.User.search(username = value):
            LOG.error(f"Failed to setup social account with username {value}, this username is already taken!")
            raise ValidationError("username-registered")

    @validates("bio")
    def validate_bio(self, value):
        """Validate the requested bio.
        The bio, if provided, must be no longer than 250 characters.
        
        Raises
        ------
        ValidationError
        :bio-too-long: The alias is longer than 250 characters."""
        if value and len(value) > 250:
            LOG.error(f"Failed to setup social account, bio was too long!")
            raise ValidationError("bio-too-long")

    @validates("vehicle_information")
    def validate_vehicle_information(self, value):
        """Validate the User's vehicle information."""
        pass
    
    @post_load
    def request_setup_profile_post_load(self, data, **kwargs) -> RequestSetupProfile:
        return RequestSetupProfile(**data)


def login_local_account(request_login_local, **kwargs) -> models.User:
    """Login the given user and run logic associated with logging in. This function will also ensure the User has been verified; both by their account's creation status and by
    their password's validity.

    Arguments
    ---------
    :request_login_local: An instance of RequestLoginLocal.

    Raises
    ------
    UnauthorisedRequestFail
    :incorrect-login: The account does not exist, or the given password is not correct.
    OperationalFail
    :unknown: login_user returned False

    Returns
    -------
    The User."""
    try:
        # Now, search for a User that owns this email address.
        target_user = models.User.search( email_address = request_login_local.email_address )
        if not target_user:
            LOG.error(f"Failed to login local account; no User for email; {request_login_local.email_address}")
            raise error.UnauthorisedRequestFail("incorrect-login")
        # Found the User, now check that the password verifies.
        if not target_user.check_password(request_login_local.password):
            LOG.error(f"Failed to login local account {target_user}; password was incorrect.")
            raise error.UnauthorisedRequestFail("incorrect-login")
        # Is the User disabled? If so, don't even log the User in.
        if not target_user.enabled:
            LOG.error(f"Failed to login local account {target_user}; account is DISABLED.")
            # Raise a critical error that will log the User out of their account on the client.
            raise error.AccountSessionIssueFail("disabled")
        # We can now log the User in.
        if not login_user(target_user, remember = request_login_local.remember_me):
            LOG.error(f"Failed to login local account {target_user}; login_user returned False!.")
            raise error.OperationalFail("unknown")
        """
        TODO: login logic here
        -> Add this as a login history item
        """
        return target_user
    except Exception as e:
        raise e


def logout_local_account(**kwargs):
    """Logout the given User.

    Arguments
    ---------
    :user: The user to logout.

    Returns
    -------
    A boolean."""
    try:
        if current_user.is_authenticated:
            LOG.debug(f"Logging out user {current_user}")
            """TODO: logout logic."""
            logout_user()
        return True
    except Exception as e:
        raise e


def logout(**kwargs):
    """"""
    try:
        if current_user.is_authenticated:
            LOG.debug(f"Logging out user {current_user}")
            logout_local_account()
        return True
    except Exception as e:
        raise e


def _create_account(request_new_account, **kwargs) -> models.User:
    """A registration request for a User. This will create a new User and prepare it for first time use. The function does not handle validation for arguments. A
    single primary dictonary is required; either RequestNewAccountBaseSchema derivative. This object will handle validation. This function does not check for the
    user's permission to create a new account, this must be done in calling code.

    Arguments
    ---------
    :request_new_account: An instance of RequestNewAccount, or a subtype thereof.

    Keyword arguments
    -----------------
    :enabled: True if the account should be created enabled. Default is True.

    Returns
    -------
    The new User instance."""
    try:
        enabled = kwargs.get("enabled", True)

        # Create a new User with the request dictionary.
        new_user = models.User()
        new_user.set_email_address(request_new_account.email_address)
        new_user.set_password(request_new_account.password)
        # Set the account enabled.
        new_user.set_enabled(enabled)
        db.session.add(new_user)
        return new_user
    except Exception as e:
        raise e


def create_local_account(request_local_account, **kwargs) -> models.User:
    """A registration request for a User. This will create a new User and prepare it for first time use. This function is specifically for accounts
    created via the HawkSpeed system. A loaded RequestNewLocalAccountSchema is expected. No validation is done within this function, this should
    be done prior to calling.

    Arguments
    ---------
    :request_local_account: An instance of RequestNewLocalAccount.

    Keyword arguments
    -----------------
    :enabled: True if the account should be created enabled. Default is True.
    :verification_required: True if the new User instance should be required to verify via email before using the account. Default is True.

    Returns
    -------
    The newly created User."""
    try:
        enabled = kwargs.get("enabled", True)
        verification_required = kwargs.get("verification_required", True)

        # Create the new User object.
        new_user = _create_account(request_local_account, enabled = enabled)
        # Set the user's password.
        new_user.set_password(request_local_account.password)
        LOG.debug(f"Created a new localised account; {new_user}")
        # If we require verification, call out to require_verification.
        if verification_required:
            # Flush to grab the user a UID.
            db.session.flush()
            user_verify = require_verification(new_user, "new-account", expires = config.TIME_UNTIL_NEW_ACCOUNT_EXPIRES)
        return new_user
    except Exception as e:
        raise e


def check_name_taken(username, **kwargs) -> bool:
    """This function will simply search all users for one with the given username. If found, True will be returned, else False.

    Arguments
    ---------
    :username: The username to check.

    Returns
    -------
    True if the name is taken, False otherwise."""
    try:
        existing_user = models.User.search(username = username)
        if existing_user:
            return True
        return False
    except Exception as e:
        raise e


def setup_account_profile(user, request_setup_profile, **kwargs) -> models.User:
    """Setup the user's account for use in the social aspects of HawkSpeed. This will allow the user to set their username, bio, profile image.
    No validation is done within this function, please ensure this is done prior to calling.

    Arguments
    ---------
    :user: The User instance to setup profile account. The User must be verified.
    :request_setup_profile: An instance of RequestSetupProfile.

    Raises
    ------
    OperationalFail
    :no-user: No User was provided.
    :profile-already-setup: The User already qualifies as a socially participating User.
    :user-not-verified: The User is not yet verified, and therefore can't be completed.

    Returns
    -------
    The User instance that has been successfully upgraded."""
    try:
        if not user:
            LOG.error(f"Failed to setup social account, no user provided.")
            raise error.OperationalFail("no-user")
        # If the User is not verified, raise an exception.
        if not user.verified:
            LOG.error(f"Failed to setup account profile for {user}, they are not verified yet!")
            raise error.OperationalFail("user-not-verified")
        elif user.is_profile_setup:
            LOG.error(f"Failed to setup account profile for {user}, they have already had their profile setup!")
            raise error.OperationalFail("profile-already-setup")
        # Get our input data.
        #TODO: profile_image = request_setup_profile_d.get("profile_image")
        # Set the user's username.
        user.set_username(request_setup_profile.username)
        # Set the user's bio.
        user.set_bio(request_setup_profile.bio)
        """TODO: vehicle information."""
        # Set profile setup.
        user.set_profile_setup(True)
        return user
    except Exception as e:
        raise e


def require_verification(user, reason_id, **kwargs) -> models.UserVerify:
    """Require that a specific User be flagged for some sort of verification. This will not guide how the verification is
    dealt with or rectified, it only provides the mechanism for awareness that some response by the User is requried at
    some point. Verification reasons can be found in constant.py under USER_VERIFY_REASONS.

    Duplicates of UserVerify instances are not allowed for a single reason, an error will be raised in this case.

    Arguments
    ---------
    :user: The user to require verification from.
    :reason_id: A reason, found under USER_VERIFY_REASONS, to use as the flag for this verification.

    Keyword arguments
    -----------------
    :time_until_expiry: The number of seconds to wait until the UserVerify instance expires; and can no longer be completed. -1 to disable (default is -1.)
    :update_if_duplicate: If this is a duplicate verify request, the UserVerify instance will be updated instead of an error raised. Default is False.
    :token: The token to use for the UserVerify instance. If not given, one will be generated.

    Raises
    ------
    OperationalFail
    :duplicate-verification: A UserVerify instance with the requested reason was found, and we were not told to update.
    :invalid-reason: The given reason ID is not an accepted verification reason.
    :token-not-unique: The given token already exists for another verification request.

    Returns
    -------
    An instance of UserVerify."""
    token = kwargs.get("token", None)
    time_until_expiry = kwargs.get("time_until_expiry", -1)
    update_if_duplicate = kwargs.get("update_if_duplicate", False)

    # Ensure the reason is valid.
    """TODO: verify reasons"""
    #if not reason_id in constant.USER_VERIFY_REASONS:
    #    LOG.warning(f"Failed to create UserVerify for {user} under reason '{reason_id}'; this is not a valid reason.")
    #    raise error.OperationalFail("invalid-reason")
    # Attempt to get an existing UserVerify of this reason from the user.
    existing_verify = models.UserVerify.get_by_user_and_reason(user, reason_id)
    if existing_verify and not update_if_duplicate:
        LOG.warning(f"Failed to create UserVerify for {user} under reason '{reason_id}'; this is a duplicate request.")
        raise error.OperationalFail("duplicate-verification")
    elif existing_verify:
        # The verify exists, but we've been asked to update if there's a duplicate.
        # This is essentially for a reactivation.
        LOG.debug(f"Instead of creating UserVerify for {user} under reason '{reason_id}', we'll update their existing request.")
    else:
        # No verify. Create a new one.
        LOG.debug(f"Creating UserVerify for {user} under reason '{reason_id}'")
        existing_verify = models.UserVerify(
            reason_id = reason_id
        )
    # Generate our own token if none is given, or one is given but it isn't unique.
    if not token:
        # Generate a new one for this verification row.
        LOG.debug(f"Generating token for UserVerify {existing_verify}")
        hash = hashlib.sha256()
        hash.update(user.uid.encode("utf-8"))
        hash.update(str(time.time()).encode("utf-8"))
        token = hash.hexdigest()
    else:
        unique_token_search = models.UserVerify.get_by_token(token)
        if unique_token_search and unique_token_search != existing_verify:
            # Otherwise, if we were given a token but it is not unique, raise an error.
            LOG.error(f"Failed to create UserVerify for {user} under reason '{reason_id}'; token is a duplicate")
            raise error.OperationalFail("token-not-unique")
    existing_verify.token = token
    if time_until_expiry > 0:
        existing_verify.expires = time.time() + time_until_expiry
        LOG.debug(f"Set expiry for {existing_verify} to {time_until_expiry} seconds after right now.")
    existing_verify.user = user
    if not existing_verify in db.session:
        db.session.add(existing_verify)
    return existing_verify
