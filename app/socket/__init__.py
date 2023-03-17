import logging
import functools

from flask import request
from flask_login import current_user
from flask_socketio import disconnect

from .. import error

LOG = logging.getLogger("hawkspeed.socket")
LOG.setLevel( logging.DEBUG )


def authenticated_only(**kwargs):
    """A SocketIO equivalent to the login_required Flask-Login decorator."""
    def decorator(f):
        @functools.wraps(f)
        def decorated_view(self, *args, **kwargs):
            if not current_user.is_authenticated:
                # Instead of disconnecting the User, we will raise an error instead.
                raise error.SocketIOUserNotAuthenticated()
            return f(self, *args, **kwargs)
        return decorated_view
    return decorator


def joined_players_only(**kwargs):
    """A decorator that will ensure the current Player is connected to the socket server, and that the SID matches between the one stored and the one in this request."""
    def decorator(f):
        @authenticated_only()
        @functools.wraps(f)
        def decorated_view(self, *args, **kwargs):
            # We are already confirmed as being authenticated, so grab the User's player.
            player = current_user.player
            if not player:
                # Player does not even exist for this User. Raise an appropriate exception.
                LOG.error(f"Failed for {current_user} to pass joined players only check - their Player is NONE!")
                """TODO: please handle this properly."""
                raise NotImplementedError()
            elif player.socket_id != request.sid:
                # If the Player's socket IDs do not match at this point, raise an appropriate exception.
                LOG.error(f"Failed for {current_user} to pass joined players only check - their Player's socket ID ({player.socket_id} does not match current session's sid ({request.sid}))")
                """TODO: please handle this properly."""
                raise NotImplementedError()
            # Done deal. This is a valid session, allow it.
            return f(self, *args, **kwargs)
        return decorated_view
    return decorator


# Now, import all handlers.
from . import handler


def setup_socketio(socketio):
    """Setup the given socket io instance to support the required namespaces.

    Arguments
    ---------
    :socketio: A SocketIO instance to attach all namespaces & handlers to."""
    LOG.debug("Setting up socketio")
    # Register all socket namespaces.
    socketio.on_namespace(handler.WorldNamespace("/"))

    @socketio.on_error("/world")
    def error_handler_world(e):
        # Setup error handler for the '/world' namespace.
        handler.handle_world_error(e)

    @socketio.on_error_default
    def default_error_handler(e):
        # Default error handler for namespaces without error handler.
        handler.default_error_handler(e)
