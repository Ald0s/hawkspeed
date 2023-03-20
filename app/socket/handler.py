import re
import logging

from flask import request
from flask_socketio import Namespace, emit, join_room, leave_room, disconnect
from flask_login import login_required, current_user

from marshmallow import Schema, fields, post_load, pre_dump, EXCLUDE

from .. import db, config, login_manager, socketio, models, world, races, viewmodel, error
from . import authenticated_only, joined_players_only

LOG = logging.getLogger("hawkspeed.socket.handler")
LOG.setLevel( logging.DEBUG )


class PlayerUpdateRequestSchema(world.BasePlayerUpdateSchema, world.BaseViewportUpdateSchema):
    """A subtype of the player update, specifically for the Player's communicating their location."""
    pass


class ConnectAuthenticationRequestSchema(world.BasePlayerUpdateSchema):
    """A subtype of the player update, specifically for the Player's initial report upon connection."""
    pass


class ViewportUpdateRequestSchema(world.BaseViewportUpdateSchema):
    """A subtype of the viewport update, specifically for when the Player wishes to update their """
    pass


class StartRaceRequestSchema(world.BasePlayerUpdateSchema):
    """A subtype of the player update, specifically for the Player's request to begin a race. The location stored in this request should represent the point at which
    the device was when the race was started. The viewport stored should be used to ensure the Player is facing the right way."""
    track_uid               = fields.Str()
    # The player update position at the time the countdown was started.
    countdown_position      = fields.Nested(world.BasePlayerUpdateSchema, many = False)


class ViewportUpdateResponseSchema(Schema):
    """A response schema for the viewport update handler, or schemas that nest a viewport update. It is expected that a ViewportUpdateResult is dumped
    through this response schema. This schema will handle the conversion of normal model instances to view models prior to being serialised."""
    class Meta:
        unknown = EXCLUDE
    track_viewmodels        = fields.List(viewmodel.SerialiseViewModelField(), data_key = "tracks")

    @pre_dump
    def viewport_update_pre_dump(self, viewport_update_result, **kwargs):
        # Raise an exception if the given object is not a ViewportUpdateResult.
        if not isinstance(viewport_update_result, world.ViewportUpdateResult):
            raise TypeError(f"Failed to dump a ViewportUpdateResponseSchema. This schema requires ViewportUpdateResult type. Instead, {type(viewport_update_result)} was given.")
        # Otherwise, we'll convert this update result to a dictionary containing all view model equivalents of stored entities.
        return dict(
            track_viewmodels = [viewmodel.TrackViewModel(current_user, track) for track in viewport_update_result.tracks]
        )


class PlayerJoinResponseSchema(Schema):
    """A response schema for a player join result."""
    class Meta:
        unknown = EXCLUDE
    uid                     = fields.Str(data_key = "player_uid")
    latitude                = fields.Decimal(as_string = True, required = True)
    longitude               = fields.Decimal(as_string = True, required = True)
    rotation                = fields.Decimal(as_string = True, required = True)


class PlayerUpdateResponseSchema(Schema):
    """A response schema for the player update result."""
    uid                     = fields.Str(data_key = "player_uid")
    latitude                = fields.Decimal(as_string = True, required = True)
    longitude               = fields.Decimal(as_string = True, required = True)
    rotation                = fields.Decimal(as_string = True, required = True)
    # Now, also the viewport update associated with this player update response, this can be None.
    viewport_update         = fields.Nested(ViewportUpdateResponseSchema, many = False, allow_none = True)


class RaceStartedResponseSchema(Schema):
    """A confirmation response that the race started correctly or that the race did not start for some reason or disqualification."""
    is_started              = fields.Bool(allow_none = False)
    race                    = fields.Nested(races.RaceSchema, many = False, allow_none = True)
    error_code              = fields.Str(allow_none = True)


class RaceFinishedSchema(races.RaceSchema):
    """A one-way message from the server to the client, informing them that the current race is complete."""
    pass


class RaceCancelledSchema(races.RaceSchema):
    """A one-way message from the server to the client, informing them that their current race has been cancelled."""
    pass


class RaceDisqualifiedSchema(races.RaceSchema):
    """A one-way message from the server to the client, informing them that their current race has been disqualified."""
    pass


class WorldNamespace(Namespace):
    """The primary world namespace, this will handle all communication relating to the game aspect of HawkSpeed. All handlers require that the incoming User is at least
    authenticated via flask login. The other functions require also that the incoming User is joined to the world."""
    @authenticated_only()
    def on_connect(self, auth_j, **kwargs):
        """On connection to the world namespace, the User must be checked to ensure they are logged in. The connection handler requires a single argument; a JSON object containing
        ConnectAuthenticationSchema attributes. Upon successful result, an instance of ConnectedAndJoinedSchema will be serialised and emitted toward the current socket ID. A User
        can only be connected once at a time. Reconnecting over a previous connection will boot the old one.

        Arguments
        ---------
        :auth_d: A JSON object containing ConnectAuthenticationSchema."""
        try:
            LOG.debug(f"A User ({current_user}) has connected to the world.")
            # Load the auth_d argument as a ConnectAuthenticationSchema instance.
            connect_auth_schema = ConnectAuthenticationRequestSchema()
            connect_auth_d = connect_auth_schema.load(auth_j)
            # Check the current user's socket ID. If this is not None, disconnect and set it to None.
            if current_user.socket_id:
                # We have an existing socket ID on this User; it must go. Call disconnect on it. This will invoke the disconnection event handler, which will set this to None.
                LOG.warning(f"User {current_user} has joined the HawkSpeed world (on sid {request.sid}), but is apparently already connected via SocketID {current_user.socket_id}.")
                disconnect(sid = current_user.socket_id)
            # Update current socket session and commit.
            current_user.set_socket_session(request.sid)
            # Parse the player's join request, getting back a result.
            player_join_result = world.parse_player_joined(current_user, connect_auth_d)
            db.session.commit()
            # Serialise the player join result.
            player_join_response_schema = PlayerJoinResponseSchema()
            player_join_d = player_join_response_schema.dump(player_join_result)
            # Emit this to the current client.
            emit("welcome", player_join_d,
                sid = request.sid)
        except Exception as e:
            raise e

    @authenticated_only()
    def on_disconnect(self, **kwargs):
        """Called when the client has disconnected from the server, or if the server has disconnected the client. Either way, this function will clean the User's session up."""
        try:
            LOG.debug(f"A User ({current_user}, sid={request.sid}) has disconnected from the world!")
            # Check whether the User currently has a socket ID set. If that is the case, we will run disconnect on it; which won't do anything if there is no connection.
            if current_user.socket_id != None:
                disconnect(sid = current_user.socket_id)
                current_user.clear_socket_session()
                # Commit and finish.
                db.session.commit()
        except Exception as e:
            raise e

    @joined_players_only()
    def on_start_race(self, race_j, **kwargs):
        """Handle intent from a Player to start a new race. This will aggressively cancel any existing races. The submitted content should have a location snapshot taken when
        countdown started, and when the actual race started. As well, the desired track's UID should be supplied. This handler will respond with a confirmation schema, or an
        error schema. On the clientside, the DTO should support a merging of both potential states."""
        try:
            # Before anything else, since we received intent to start a new race, cancel any ongoing race for the current User. If any, the existing race will be returned.
            old_ongoing_race = races.cancel_ongoing_race(current_user)
            if old_ongoing_race:
                # We had an existing race, which is now cancelled. Prior to committing, we need to emit an event to the client letting it know this race has been cancelled.
                race_cancelled_schema = RaceCancelledSchema()
                race_cancelled_d = race_cancelled_schema.dump(old_ongoing_race)
                emit("race-cancelled", race_cancelled_d,
                    sid = request.sid)
            # Load the intent to start a new race.
            start_race_request_schema = StartRaceRequestSchema()
            start_race_d = start_race_request_schema.load(race_j)
            # Then parse the received data as a player update, to get back a player update result. This will also log the start point for the new race.
            player_update_result = world.parse_player_update(current_user, start_race_d)
            # Now that we have our player update result, pass it alongside the start race request to the races module, for a new race to be created. A StartRaceResult is expected.
            start_race_result = races.start_race_for(current_user, start_race_d, player_update_result)
            # Start race result will contain both a positive and negative result. Either way, we will return it as a serialised response.
            start_race_response_schema = RaceStartedResponseSchema()
            return start_race_response_schema.dump(start_race_result)
        except Exception as e:
            raise e

    @joined_players_only()
    def on_player_update(self, update_j, **kwargs):
        """Called when the Player's device has received a new location update from their GPS system. This function will invoke the various Player update procedures,
        first validating the information sent, then using it to update the Player's position, statistics, proximity entities etc. This function will reply with a
        world update receipt, that acknowledges the update was done, and updates some crucial data for the User themselves."""
        try:
            # Instantiate a PlayerUpdateRequestSchema and load the update dictionary.
            player_update_request_schema = PlayerUpdateRequestSchema()
            player_update_d = player_update_request_schema.load(update_j)
            # Process the received player update.
            player_update_result = world.parse_player_update(current_user, player_update_d)
            # Only bother executing race participation updates if the current User has an ongoing race.
            if current_user.has_ongoing_race:
                try:
                    # Update this Player's participation in any race, get back a participation result.
                    update_race_participation_result = races.update_race_participation_for(current_user, player_update_result)
                    if update_race_participation_result.is_finished:
                        # The Player has successfully finished the race. For now, a race update will simply be sent to the race-finished event.
                        """TODO: make this reaction a bit more complicated."""
                        race_finished_schema = RaceFinishedSchema()
                        race_finished_d = race_finished_schema.dump(update_race_participation_result.track_user_race)
                        emit("race-finished", race_finished_d,
                            sid = request.sid)
                except error.RaceDisqualifiedError as rde:
                    # On disqualification, the race should already be disqualified.
                    race_disqualified_schema = RaceDisqualifiedSchema()
                    race_disqualified_d = race_disqualified_schema.dump(rde.track_user_race)
                    emit("race-disqualified", race_disqualified_d,
                        sid = request.sid)
            # Calculations and updates are done, we can commit to database, then return the serialised response.
            db.session.commit()
            player_update_response_schema = PlayerUpdateResponseSchema()
            return player_update_response_schema.dump(player_update_result)
        except Exception as e:
            raise e

    @joined_players_only()
    def on_viewport_update(self, update_j, **kwargs):
        """Called when the Player wishes to update specifically their viewport. Since the User may decide to actually scroll away from their position on the overall
        world map, and view objects at that area."""
        try:
            # Instantiate a ViewportUpdateRequestSchema and load the update dictionary.
            viewport_update_request_schema = ViewportUpdateRequestSchema()
            viewport_update_d = viewport_update_request_schema.load(update_j)
            # Now, call out to the world module and call the collect world objects. Expect back a ViewedObjectsResult.
            viewport_update_result = world.collect_viewed_objects(current_user, viewport_update_d)
            # Simply serialise and return this result.
            viewport_update_response_schema = ViewportUpdateResponseSchema()
            return viewport_update_response_schema.dump(viewport_update_result)
        except Exception as e:
            raise e


"""
TODO: proper management of errors here, please.
"""
def handle_world_error(e):
    """"""
    LOG.error(f"Unhandled error occurred in world namespace; {e}")
    raise e


def default_error_handler(e):
    """"""
    if isinstance(e, error.SocketIOUserNotAuthenticated):
        print("User is NOT authenticated.")
        disconnect()
    LOG.error(f"Unhandled error occurred (global); {e}")
    raise e
