import logging

from flask import request
from flask_socketio import Namespace, emit, disconnect, ConnectionRefusedError
from flask_login import current_user

from marshmallow import Schema, fields, pre_dump, EXCLUDE

from .. import db, error, config, models, users, world, races, viewmodel
from . import authenticated_only, joined_players_only

LOG = logging.getLogger("hawkspeed.socket.handler")
LOG.setLevel( logging.DEBUG )


class JoinWorldRefusedError(error.PublicSocketException):
    """A serialisable exception that communicates a User's attempt to join the world has been refused. This will only ever be raised in on_connect. Reason code
    can be any reason found in ParsePlayerJoinedError."""
    @property
    def name(self):
        return "join-world-refused"


class KickedFromWorldError(error.PublicSocketException):
    """A serialisable exception that informs the client they've been kicked from the world. Reason can be any reason code from ParsePlayerUpdateError."""
    @property
    def name(self):
        return "kicked-from-world"
    

class ViewportUpdateResponseSchema(Schema):
    """A schema for dumping a response to a viewport update. This schema requires view models returned."""
    class Meta:
        unknown = EXCLUDE
    tracks                  = fields.List(viewmodel.SerialiseViewModelField())

    @pre_dump
    def viewport_update_pre_dump(self, viewport_update_result, **kwargs):
        # Raise an exception if the given object is not a ViewportUpdateResult.
        if not isinstance(viewport_update_result, world.ViewportUpdateResult):
            raise TypeError(f"Failed to dump a ViewportUpdateResponseSchema. This schema requires ViewportUpdateResult type. Instead, {type(viewport_update_result)} was given.")
        # Otherwise, we'll convert this update result to a dictionary containing all view model equivalents of stored entities.
        return dict(
            tracks = [viewmodel.TrackViewModel(current_user, track) for track in viewport_update_result.tracks]
        )


class WorldObjectUpdateResponseSchema(Schema):
    """A schema for dumping a general response to a world object update. This schema requires view models returned."""
    class Meta:
        unknown = EXCLUDE
    tracks                  = fields.List(viewmodel.SerialiseViewModelField())

    @pre_dump
    def world_object_update_pre_dump(self, world_object_result, **kwargs):
        # Raise an exception if the given object is not a WorldObjectUpdateResult.
        if not isinstance(world_object_result, world.WorldObjectUpdateResult):
            raise TypeError(f"Failed to dump a WorldObjectUpdateResponseSchema. This schema requires WorldObjectUpdateResult type. Instead, {type(world_object_result)} was given.")
        # Otherwise, we'll convert this update result to a dictionary containing all view model equivalents of stored entities.
        return dict(
            tracks = [viewmodel.TrackViewModel(current_user, track) for track in world_object_result.tracks]
        )
    

class PlayerJoinResponseSchema(Schema):
    """A response schema for a player join result."""
    class Meta:
        unknown = EXCLUDE
    uid                     = fields.Str(data_key = "player_uid")
    latitude                = fields.Decimal(as_string = True, required = True)
    longitude               = fields.Decimal(as_string = True, required = True)
    rotation                = fields.Decimal(as_string = True, required = True)
    # Now, also the nearby object update associated with this player join response, this can be None.
    world_object_update     = fields.Nested(WorldObjectUpdateResponseSchema, many = False, allow_none = True)


class PlayerUpdateResponseSchema(Schema):
    """A response schema for the player update result."""
    uid                     = fields.Str(data_key = "player_uid")
    latitude                = fields.Decimal(as_string = True, required = True)
    longitude               = fields.Decimal(as_string = True, required = True)
    rotation                = fields.Decimal(as_string = True, required = True)
    # Now, also the nearby object update associated with this player update response, this can be None.
    world_object_update     = fields.Nested(WorldObjectUpdateResponseSchema, many = False, allow_none = True)


class WorldNamespace(Namespace):
    """The primary world namespace, this will handle all communication relating to the game aspect of HawkSpeed. All handlers require that the incoming User is at least
    authenticated via flask login. The other functions require also that the incoming User is joined to the world."""
    @authenticated_only()
    def on_connect(self, auth_j, **kwargs):
        """On connection to the world namespace, the User must be checked to ensure they are logged in. The connection handler requires a single argument; a JSON object containing
        ConnectAuthenticationSchema attributes. Upon successful result, an instance of ConnectedAndJoinedSchema will be serialised and emitted toward the current socket ID. A User
        can only be connected once at a time. Reconnecting over a previous connection will boot the old one.

        There is no reason to place an exception in the connect response schema, because the only way joining can fail is with the return of connectionrefusederror.

        Arguments
        ---------
        :auth_j: A JSON object containing RequestConnectAuthenticationSchema."""
        try:
            LOG.debug(f"User {current_user} ({request.sid}) is attempting to join the world.")
            # Load the auth_j argument as a RequestConnectAuthentication instance.
            request_connect_auth_schema = world.RequestConnectAuthenticationSchema()
            request_connect_authentication = request_connect_auth_schema.load(auth_j)
            # Check to see if the current User currently has a Player. If they do, disconnect that version and clear it.
            if current_user.has_player:
                # We have an existing player on this User; it must go. Call via socketio, but also explicitly call the disconnect function.
                LOG.warning(f"User {current_user} has joined the HawkSpeed world (on sid {request.sid}), but is apparently already connected via SocketID {current_user.player.socket_id}.")
                disconnect(sid = current_user.player.socket_id)
                # Try calling the disconnect function and handle, by passing, all exceptions related to there being no socket.
                try:
                    self.on_disconnect(disconnecting_sid = current_user.player.socket_id)
                except AttributeError as ae:
                    # Attribute error here usually means that there is no player (existing connection closed.)
                    pass
            # We'll now create a new Player instance for the incoming User.
            new_player = world.create_player_session(current_user, request.sid, request_connect_authentication)
            # Parse the player's join request, getting back a result.
            try:
                player_join_result = world.parse_player_joined(current_user, request_connect_authentication)
            except world.ParsePlayerJoinedError as ppje:
                # Raise this to be handled globally.
                raise ppje
            # If processing the location is a success, we will set the geometry on our new Player instance to mirror that location.
            new_player.set_crs(player_join_result.crs)
            new_player.set_position(player_join_result.position)
            # Now, set the User's player, then add the new Player to the session and finally commit + refresh the User.
            current_user.set_player(new_player)
            db.session.add(new_player)
            db.session.commit()
            db.session.refresh(current_user)
            # Serialise the player join result.
            player_join_response_schema = PlayerJoinResponseSchema()
            player_join_d = player_join_response_schema.dump(player_join_result)
            # Emit this to the current client.
            emit("welcome", player_join_d,
                sid = request.sid)
        except Exception as e:
            db.session.rollback()
            raise e

    @authenticated_only()
    def on_disconnect(self, **kwargs):
        """Called when the client has disconnected from the server, or if the server has disconnected the client. Either way, this function will clean the User's session up."""
        try:
            disconnecting_sid = kwargs.get("disconnecting_sid", request.sid)

            LOG.debug(f"A User ({current_user}, sid={disconnecting_sid}) has disconnected from the world!")
            # Check whether the User currently has a Player.
            if current_user.has_player:
                # Does User have an ongoing race? If so, disqualify it.
                if current_user.has_ongoing_race:
                    races.disqualify_ongoing_race(current_user, races.RaceDisqualifiedError.DQ_CODE_DISCONNECTED)
                # Then clear the User's Player.
                current_user.clear_player()
                # Commit, then expire then refresh the current User.
                db.session.commit()
                db.session.refresh(current_user)
        except Exception as e:
            raise e

    @joined_players_only()
    def on_player_update(self, update_j, **kwargs):
        """Called when a player sends a message to update their location in the world. This handler will ensure the location is supported and valid, and will then commit its changes
        to the database. As well as updating the player's location within the world, this function will update any ongoing race participations the Player is involved in. If the race
        is disqualified or finished, this function will emit messages along these lines, but will not return those messages. The response returned will be a receipt of the newly
        accepted changes to location data, as well as world objects that are close to the player by proximity."""
        try:
            # Load a new request for complete player update.
            request_player_update_schema = world.RequestPlayerUpdateSchema()
            request_player_update = request_player_update_schema.load(update_j)
            try:
                # Process the received player update.
                player_update_result = world.parse_player_update(current_user, request_player_update)
            except world.ParsePlayerUpdateError as ppue:
                # Raise this to handle globally.
                raise ppue
            # Only bother executing race participation updates if the current User has an ongoing race.
            if current_user.has_ongoing_race:
                # Update this Player's participation in any race, get back a participation result.
                update_race_participation_result = races.update_race_participation_for(current_user, player_update_result)
                # Serialise the result.
                update_race_response = update_race_participation_result.serialise()
                # Now, emit a message with the proper name depending on the outcome.
                if update_race_participation_result.is_finished:
                    emit("race-finished", update_race_response,
                        sid = request.sid)
                elif update_race_participation_result.is_disqualified:
                    emit("race-disqualified", update_race_response,
                        sid = request.sid)
                else:
                    emit("race-progress", update_race_response,
                        sid = request.sid)
            # Calculations and updates are done, we can commit to database, then return the serialised response.
            db.session.commit()
            player_update_response_schema = PlayerUpdateResponseSchema()
            return player_update_response_schema.dump(player_update_result)
        except Exception as e:
            db.session.rollback()
            raise e
        
    @joined_players_only()
    def on_viewport_update(self, update_j, **kwargs):
        """Called when the user wishes to update specifically their viewport. Since the User may decide to actually scroll away from their position on the overall
        world map, and view objects at that area."""
        try:
            # Load update json object as a request for a viewport update.
            request_viewport_update_schema = world.RequestViewportUpdateSchema()
            request_viewport_update = request_viewport_update_schema.load(update_j)
            try:
                # Now, call out to the world module and call the collect world objects. Expect back a ViewedObjectsResult.
                viewport_update_result = world.collect_viewed_objects(current_user, request_viewport_update.viewport)
            except world.CollectViewedObjectsError as cvoe:
                # Raise this to handle locally.
                raise cvoe
            # Simply serialise and return this result.
            viewport_update_response_schema = ViewportUpdateResponseSchema()
            return viewport_update_response_schema.dump(viewport_update_result)
        except world.CollectViewedObjectsError as cvoe:
            """TODO: failed to collect viewed objects. Determine why and re-raise if necessary. But for errors like viewport not supported, we can simply return
            a failure-type object that informs client of the issue."""
            raise NotImplementedError()
        except Exception as e:
            raise e
    
    @joined_players_only()
    def on_start_race(self, race_j, **kwargs):
        """Handle intent from a Player to start a new race. The submitted content should have a location snapshot taken when countdown started, and when the actual race
        started. As well, the desired track's UID should be supplied. This handler will respond with a start race result schema which will communicate whether the race
        was started or an issue occurred.

        If a race is ongoing, this function will fail and return an error in the result. The result is a serialised StartRaceResult."""
        try:
            if races.get_ongoing_race(current_user) != None:
                # If there is an ongoing race, raise the appropriate race start error, with reason REASON_ALREADY_IN_RACE.
                raise races.RaceStartError(races.RaceStartError.REASON_ALREADY_IN_RACE)
            # Load the intent to start a new race.
            request_start_race_schema = races.RequestStartRaceSchema()
            request_start_race = request_start_race_schema.load(race_j)
            try:
                # Then parse the received started location data as a player update, to get back a player update result. This will also log the start point for the new race.
                player_update_result = world.parse_player_update(current_user, request_start_race.started_position)
            except world.ParsePlayerUpdateError as ppue:
                # The update was not able to be parsed. This is grounds for a race start error, but we must map reasons from parse player update error to relevant
                # start race error reasons.
                if ppue.reason_code == world.ParsePlayerUpdateError.REASON_POSITION_NOT_SUPPORTED:
                    # Raise this to be handled locally, in the handler for on start race.
                    raise races.RaceStartError(races.RaceStartError.REASON_POSITION_NOT_SUPPORTED)
                else:
                    raise NotImplementedError(f"An unsupported condition was hit in ParsePlayerUpdateError handler within on_start_race! Reason code: {ppue.reason_code}")
            # Now that we have our player update result, pass it alongside the start race request to the races module, for a new race to be created. A StartRaceResult is expected.
            # This function call may also raise a RaceStartError.
            start_race_result = races.start_race_for(current_user, request_start_race, player_update_result)
            # If we've made it this far, we can commit.
            db.session.commit()
            # Start race result will contain both a positive and negative result. Either way, we will return it as a serialised response.
            return start_race_result.serialise()
        except races.RaceStartError as rse:
            # Rollback transaction, then build a new start race result that contains the failure reason and return this as receipt.
            db.session.rollback()
            start_race_result = races.StartRaceResult(False, 
                exception = rse)
            return start_race_result.serialise()
        except Exception as e:
            db.session.rollback()
            raise e
    
    @joined_players_only()
    def on_cancel_race(self, cancel_j, **kwargs):
        """Cancel the currently ongoing race, and return a race cancelled response as a result. If there is no ongoing race, this function will respond with a None-like race cancelled result."""
        try:
            # Attempt to cancel any ongoing races.
            cancel_race_result = races.cancel_ongoing_race(current_user)
            LOG.debug(f"Cancelled race {cancel_race_result.race} for {current_user}")
            # Commit, then serialise and return the result.
            db.session.commit()
            # Serialise and return result.
            return cancel_race_result.serialise()
        except Exception as e:
            raise e
        

def handle_world_error(e):
    """Where we handle general world errors. If required, this is where we actually map server-only errors that relate to the world module, to response compatible errors that can be
    sent to the client."""
    if isinstance(e, world.ParsePlayerJoinedError):
        """This error being raised constitutes a failure to parse a request by a player to join the world. This may be for many reasons, that we will log here."""
        """
        TODO: log data about the failure; was it a position not supported error?

        Main responsibilities:
        1. Log this position that's not supported, along with the reason code for specifics.
        2. Send the user an error event describing this issue.
        3. Disconnect the user from the socket server."""
        # Finally, raise a connection refused error with the content being a local socket error of a join world refused error.
        LOG.warning(f"{current_user} ({request.sid}) was refused access to the world; {e.reason_code}")
        raise ConnectionRefusedError(JoinWorldRefusedError(e.reason_code).serialise())
    elif isinstance(e, world.ParsePlayerUpdateError):
        """This error being raised constitutes a failure to parse a request by a player to update their position in the world. This may be for many reasons, that we will log here.
        The resolution for this error always involves disconnecting the user from the world."""
        """
        TODO: log data about the failure; was it a position not supported error?

        Main responsibilities:
        1. Log this position that's not supported, along with the reason code for specifics.
        2. Send the user an error event describing this issue.
        3. Disconnect the user from the socket server."""
        # Finally, emit a kick notification for the client, prior to disconnecting them.
        LOG.warning(f"{current_user} ({request.sid}) was kicked from world; {e.reason_code}")
        emit("kicked", KickedFromWorldError(e.reason_code).serialise(),
            sid = request.sid)
    else:
        # Otherwise, re-raise this error after printing it.
        LOG.error(f"Unhandled error occurred in world namespace; {e}")
        raise e
    # Dropped out of error handling without raising. This means a message has been sent. We will now close the connection.
    disconnect()


def default_error_handler(e):
    """"""
    if isinstance(e, error.SocketIOUserNotAuthenticated):
        print("User is NOT authenticated.")
        disconnect()
    LOG.error(f"Unhandled error occurred (global); {e}")
    raise e