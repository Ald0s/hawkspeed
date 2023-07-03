from flask import make_response, Response
from marshmallow import Schema, fields, EXCLUDE


class PubliclyCompatibleExceptionSchema(Schema):
    """A schema for serialising an object that implements publicly compatible exception."""
    class Meta:
        unknown = EXCLUDE
    # The name of the error overall.
    name                    = fields.Str(required = True, allow_none = False)
    # A peak into the error dict itself - the reason for the error occurring.
    reason                  = fields.Str(required = True, allow_none = False)
    # The full error dictionary, this will contain at least the reason.
    error_dict              = fields.Dict(keys = fields.Str(), values = fields.Str())


class PubliclyCompatibleException(Exception):
    """Provides functionality to an implementing subtype to make that type both an Exception that can be raised
    and also providing the subtype a function through which an object describing the error/exception can be
    called. Warning: data returned by the 'get_error_dict' will be sent to clients; so keep this in mind when
    considering the depth of information."""
    @property
    def name(self):
        """This should return the name for the exception as a whole."""
        raise NotImplementedError(f"Property name not implemented on PubliclyCompatibleException subtype {self}")
    
    @property
    def reason(self):
        """This should return a peek into the error dictionary, and retrieve the reason for this failure."""
        raise NotImplementedError(f"Property reason not implemented on PubliclyCompatibleException subtype {self}")
    
    @property
    def error_dict(self):
        """The full error dictionary, this will contain at least the reason."""
        raise NotImplementedError(f"Property error_dict not implemented on PubliclyCompatibleException subtype {self}")

    def get_error_name(self):
        """
        Get a minimised name for the outer exception. This will differentiate the errors on the client side, and should be a minimised version
        of the exception's type name. This will be added to the outer shell of the dropped error object under 'name'.
        This is required.
        """
        raise NotImplementedError(f"get_error_name() not implemented on PubliclyCompatibleException subtype {self}")

    def get_error_dict(self):
        """
        Get a dictionary describing the contents of this error.
        This can be absolutely anything as long as it is in the form of a dictionary. This is not required, and an empty dictionary
        will be returned by default.
        """
        return dict()
    

class PublicSocketException(PubliclyCompatibleException):
    """A type of publicly compatible exception that adheres specifically to socket type errors. These errors differ in that they do not have a severity level,
    and do not need to be wrapped by anything else."""
    @property
    def name(self):
        """This should return the name for the exception as a whole."""
        raise NotImplementedError(f"Property name not implemented on PublicSocketException subtype {self}")
    
    @property
    def reason(self):
        return self.error_dict.get("reason", "unknown")
    
    @property
    def error_dict(self):
        return self._error_dict
    
    def __init__(self, reason, extra_args = dict(), **kwargs):
        """Construct with a reason and an extra arguments dictionary. A new dictionary will be created; with reason and all extra arguments placed inside."""
        self._error_dict = dict(
            reason = reason,
            **extra_args)
    
    def serialise(self, **kwargs):
        schema = PubliclyCompatibleExceptionSchema(**kwargs)
        return schema.dump(self)


"""Server-only errors. These do not implement PubliclyCompatibleException at all."""
class AccountActionNeeded(Exception):
    """An exception that communicates an issue with the given User's account that must be resolved."""
    def __init__(self, _user, _action_needed_category_code, _action_needed_code, **kwargs):
        self.user = _user
        self.action_needed_code = _action_needed_code
        self.action_needed_category_code = _action_needed_category_code


class SocketIOUserNotAuthenticated(Exception):
    """"""
    pass


class NoServerConfigurationError(Exception):
    """"""
    pass


"""Global errors"""
class ProcedureRequiredException(PubliclyCompatibleException):
    def __init__(self, _error_code):
        self.error_code = _error_code

    def get_error_name(self):
        return "procedure-required"

    def get_error_dict(self):
        return {
            "error-code": self.error_code
        }


class AccountSessionIssueFail(PubliclyCompatibleException):
    ERROR_UNAUTHORISED = "unauthorised"
    ERROR_DISABLED = "disabled"

    def __init__(self, _error_code):
        self.error_code = _error_code

    def get_error_name(self):
        return "account-issue"

    def get_error_dict(self):
        return {
            "error-code": self.error_code
        }


class DeviceIssueFail(PubliclyCompatibleException):
    def __init__(self, _error_code):
        self.error_code = _error_code

    def get_error_name(self):
        return "device-issue"

    def get_error_dict(self):
        return {
            "error-code": self.error_code
        }


"""Local errors"""
class OperationalFail(PubliclyCompatibleException):
    def __init__(self, _error_code):
        self.error_code = _error_code

    def get_error_name(self):
        return "operational-fail"

    def get_error_dict(self):
        return {
            "error-code": self.error_code
        }


class ContentFail(PubliclyCompatibleException):
    def __init__(self, _error_code):
        self.error_code = _error_code

    def get_error_name(self):
        return "content-fail"

    def get_error_dict(self):
        return {
            "error-code": self.error_code
        }


class BadRequestArgumentFail(PubliclyCompatibleException):
    def __init__(self, _error_code, _message = "No specific message."):
        self.error_code = _error_code
        self.message = _message

    def get_error_name(self):
        return "bad-request-argument"

    def get_error_dict(self):
        return {
            "error-code": self.error_code,
            "message": self.message
        }


class UnauthorisedRequestFail(PubliclyCompatibleException):
    def __init__(self, _error_code, _message = "No specific message.", _detailed_info = None, _should_log = False):
        self.error_code = _error_code
        self.message = _message
        self.detailed_info = _detailed_info
        self.should_log = _should_log

    def get_error_name(self):
        return "unauthorised-request"

    def get_error_dict(self):
        return {
            "error-code": self.error_code,
            "message": self.message
        }


class APIValidationError(PubliclyCompatibleException):
    def __init__(self, _messages):
        self.messages = _messages

    def get_error_name(self):
        return "validation-error"

    def get_error_dict(self):
        return {
            "messages": self.messages
        }


class APIErrorWrapper(Exception):
    """
    Provides a base for errors that should be compatible with HawkSpeed mobile clients.
    This base will ensure that a specific severity and name is used in the outer error, as well as an HTTP error code in the response.

    This class exposes the 'to_response' function, that will assemble a Flask response object ready
    to be dropped.
    """
    def __init__(self, _severity, _publicly_compatible_exception, _http_error_code, **kwargs):
        self.severity = _severity
        self.http_error_code = _http_error_code
        if not isinstance(_publicly_compatible_exception, PubliclyCompatibleException):
            """Totally broken now, good luck recovering."""
            raise Exception(f"{_publicly_compatible_exception} is not an instance of PubliclyCompatibleException!")
        self.compat_exception = _publicly_compatible_exception

    def to_response(self) -> Response:
        """
        Turns the contents of this object into a Flask Response and returns it.
        This uses the make_response function.
        """
        return make_response(dict(
            severity = self.severity,
            name = self.compat_exception.get_error_name(),
            error = self.compat_exception.get_error_dict()
        ), self.http_error_code, { "Content-Type": "application/json" })


class LocalAPIError(APIErrorWrapper):
    """
    A local API error, this is in response to a single request from the client's device. This is the lowest severity, and the error
    will not be propogated anywhere past the clientside locale from where the request was created.
    """
    def __init__(self, _publicly_compatible_exception, _http_error_code, **kwargs):
        super().__init__("local-error", _publicly_compatible_exception, _http_error_code)


class GlobalAPIError(APIErrorWrapper):
    """
    Provides functionality to subtypes that allow the translation for an error of some description to a
    type of error that can be used globally by a Mobile client to invoke some sort of change.
    """
    def __init__(self, _publicly_compatible_exception, _http_error_code, **kwargs):
        super().__init__("global-error", _publicly_compatible_exception, _http_error_code)
