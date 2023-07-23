"""A module for handling the race component of hawkspeed."""
import logging
import os
import gpxpy
import random
import hashlib

import pyproj
import geopandas
import shapely
from geoalchemy2 import shape

from datetime import datetime, date
from sqlalchemy import func, update, desc, asc
from sqlalchemy.orm import with_expression
from marshmallow import fields, Schema, post_load, EXCLUDE

from . import db, config, models, tracks, vehicles, world, error

LOG = logging.getLogger("hawkspeed.races")
LOG.setLevel( logging.DEBUG )

"""TODO: implement support for Circuit type tracks. For now, almost this entire module hinges upon the track being a Sprint - one start and one finish. Most
of the logic will need to be refactored for a focus on laps instead of progress etc. This is incoming; just making it clear circuits are NOT supported rn."""


class RaceStartError(error.PublicSocketException):
    """A publicly compatible exception that will communicate the reason for a failed race start."""
    # Player is already in an existing race.
    REASON_ALREADY_IN_RACE = "already-in-race"
    # Either countdown or started position is not supported.
    REASON_POSITION_NOT_SUPPORTED = "position-not-supported"
    # No countdown position was provided. This is a location snapshot taken when the race countdown was started.
    REASON_NO_COUNTDOWN_POSITION = "no-countdown-position"
    # No started position was provided. This is a location snapshot taken when the race actually began; at GO.
    REASON_NO_STARTED_POSITION = "no-started-position"
    # No track was found with the given UID.
    REASON_NO_TRACK_FOUND = "no-track-found"
    # The track the User wishes to race can't be raced at the moment.
    REASON_TRACK_NOT_READY = "cant-be-raced"
    # The race could not be started because the User has multiple Vehicles, but did not provide a UID for the one they want to use.
    REASON_NO_VEHICLE_UID = "no-vehicle-uid"
    # The race could not be started because the desired Vehicle could not be found.
    REASON_NO_VEHICLE = "no-vehicle"
    
    @property
    def name(self):
        return "start-race-fail"


class RaceDisqualifiedError(Exception):
    """An exception to raise that will cause the disqualification of the given race for the given reason."""
    DQ_CODE_DISCONNECTED = "disconnected"
    DQ_CODE_MISSED_TRACK = "missed-track"
    
    def __init__(self, user, track_user_race, **kwargs):
        self.user = user
        self.track_user_race = track_user_race
        self.dq_code = kwargs.get("dq_code", "no-reason-given")
        self.dq_extra_info = kwargs.get("dq_extra_info", dict())


class PlayerDodgedTrackError(Exception):
    def __init__(self, percent_dodged, **kwargs):
        self.percent_dodged = percent_dodged


class RequestStartRace():
    """A container for a loaded request to start a new race."""
    def __init__(self, **kwargs):
        self.track_uid = kwargs.get("track_uid")
        self.vehicle_uid = kwargs.get("vehicle_uid")
        self.started_position = kwargs.get("started_position")
        self.countdown_position = kwargs.get("countdown_position")


class RequestStartRaceSchema(Schema):
    """A subtype of the player update, specifically for the Player's request to begin a race. The location stored in this request should represent the point at which
    the device was when the race was started. The viewport stored should be used to ensure the Player is facing the right way."""
    class Meta:
        unknown = EXCLUDE
    # The track's UID. This is required and can't be None.
    track_uid               = fields.Str(required = True, allow_none = False)
    # The UID for the Vehicle the User wishes to use. If None given, this means there's only one vehicle available. Use that.
    vehicle_uid             = fields.Str(required = True, allow_none = True)
    # The player update position at the time the countdown finished and the race began. This is required and can't be None.
    started_position        = fields.Nested(world.RequestPlayerUpdateSchema, many = False, required = True, allow_none = False)
    # The player update position at the time the countdown was started. This is required and can't be None.
    countdown_position      = fields.Nested(world.RequestPlayerUpdateSchema, many = False, required = True, allow_none = False)

    @post_load
    def request_start_race_post_load(self, data, **kwargs) -> RequestStartRace:
        return RequestStartRace(**data)
    

class TrackUserRaceSchema(Schema):
    """A Schema that outlines the structure of a report on a TrackUserRace, for the Player currently performing that race. So this is not a view model compatible
    object, and does not contain any data points that considers any perspective."""
    class Meta:
        unknown = EXCLUDE
    # The Race's UID. Can't be None.
    uid                     = fields.Str(allow_none = False)
    # The Track's UID. Can't be None.
    track_uid               = fields.Str(allow_none = False)
    # A timestamp, in milliseconds, when the race was started. Can't be None.
    started                 = fields.Int(allow_none = False)
    # A timestamp, in milliseconds, when the race was successfully finished. Can be None.
    finished                = fields.Int(allow_none = True)
    # A boolean; whether the race is disqualified. Can't be None.
    disqualified            = fields.Bool(allow_none = False)
    # A brief reason for race disqualification, set if race is disqualified. Can be None.
    dq_reason               = fields.Str(allow_none = True)
    # Extra info attached to the disqualification. Can be None.
    dq_extra_info           = fields.Dict(keys = fields.Str(), values = fields.Str(), allow_none = True)
    # A boolean; whether the race has been cancelled. Can't be None.
    cancelled               = fields.Bool(allow_none = False)
    # The average speed of the Player in this race, in meters per second. Can be None.
    average_speed           = fields.Int(allow_none = True)
    # The number of laps complete by the Player; only applicable to Circuit type tracks. Can be None.
    num_laps_complete       = fields.Int(allow_none = True)
    # The percent of the track complete by the Player; only applicable to Sprint type tracks. Can be None.
    percent_complete        = fields.Int(allow_none = True)


def races_query_for(user, **kwargs):
    """Assemble a query for the track user race entity, belonging exclusively to the given User. Optionally provide further filtration options via keyword
    arguments. Results will be ordered in descending fashion by the started attribute, that is, latest attempts first.
    
    Arguments
    ---------
    :user: The User to query attempts for.
    
    Keyword arguments
    -----------------
    :track_uid: Optional track UID to filter attempts on.
    
    Returns
    -------
    The query for attempts."""
    try:
        track_uid = kwargs.get("track_uid", None)

        # Build a query for track user race for that User where the track is finished.
        race_attempts_q = db.session.query(models.TrackUserRace)\
            .filter(models.TrackUserRace.is_finished == True)\
            .filter(models.TrackUserRace.user_id == user.id)
        # If track UID is given, apply that too.
        if track_uid:
            race_attempts_q = race_attempts_q\
                .join(models.Track, models.Track.id == models.TrackUserRace.track_id)\
                .filter(models.Track.uid == track_uid)
        # Apply order by on the started attribute
        race_attempts_q = race_attempts_q\
            .order_by(desc(models.TrackUserRace.started))
        return race_attempts_q
    except Exception as e:
        raise e
    

def get_ongoing_race(user, **kwargs) -> models.TrackUserRace:
    """Get the ongoing race for the given User and return it. If there is no ongoing race at the time, None will be returned.
    
    Arguments
    ---------
    :user: The User for whom to get the ongoing race for.
    
    Returns
    -------
    An instance of TrackUserRace, can be None."""
    try:
        # If current environment is testing, expire the given User.
        if config.APP_ENV == "Test":
            db.session.expire(user)
        # Return None if no race ongoing, otherwise the race.
        if not user.has_ongoing_race:
            return None
        return user.ongoing_race
    except Exception as e:
        raise e
    

def get_race(**kwargs) -> models.TrackUserRace:
    """Attempt to locate a race identified by arguments passed in keyword arguments.
    
    Keyword arguments
    -----------------
    :race_uid: The Race's UID.
    :must_be_finished: True if the race should be finished. None will be returned if it is now. True will also attach the finishing place. Default is False.
    
    Returns
    -------
    An instance of TrackUserRace."""
    try:
        must_be_finished = kwargs.get("must_be_finished", False)
        race_uid = kwargs.get("race_uid", None)
        if race_uid == None:
            return None
        
        # Construct a basic query for race.
        race_q = db.session.query(models.TrackUserRace)
        # If race must be finished, filter on that.
        if must_be_finished:
            race_q = race_q\
                .filter(models.TrackUserRace.is_finished == True)
        # Now, if race UID is given, attach it as a filter.
        if race_uid:
            race_q = race_q\
                .filter(models.TrackUserRace.uid == race_uid)
        # If must be finished, also attach finishing place.
        if must_be_finished:
            race_q = race_q\
                .options(
                    with_expression(models.TrackUserRace.finishing_place,
                        func.row_number()
                            .over(order_by = asc(models.TrackUserRace.stopwatch))))
        # Return the first result.
        return race_q.first()
    except Exception as e:
        raise e


def cancel_ongoing_races(reason = "server-problem", **kwargs):
    """This function will update all currently ongoing races to be in a cancelled state, with the given reason. Primarily, this should really only
    be used to cancel all ongoing races at the initialisation of the server. This function will not flush or commit. It returns nothing.
    
    Arguments
    ---------
    :reason: The reason to cancel all ongoing races for. Default is 'server-problem'"""
    try:
        # Run an update statement on all track user race instances, setting is cancelled to True.
        cancel_ongoing_races_stmt = (
            update(models.TrackUserRace)
                .where(models.TrackUserRace.is_ongoing == True)
                .values(cancelled = True)
        )
        # Execute this statement.
        db.session.execute(cancel_ongoing_races_stmt)
        db.session.flush()
    except Exception as e:
        raise e
    

class StartRaceResult():
    """A container for storing the result of starting a new race."""
    class StartRaceResponseSchema(Schema):
        """A confirmation response that the race started correctly or that the race did not start for some reason or disqualification."""
        is_started              = fields.Bool(allow_none = False)
        race                    = fields.Nested(TrackUserRaceSchema, many = False, allow_none = True)
        exception               = fields.Nested(error.PubliclyCompatibleExceptionSchema, many = False, allow_none = True)

    @property
    def is_started(self):
        return self._started

    @property
    def race(self):
        if not self._started:
            return None
        return self._track_user_race

    @property
    def exception(self):
        return self._exception

    def __init__(self, _started, **kwargs):
        self._started = _started
        self._track_user_race = kwargs.get("track_user_race", None)
        self._exception = kwargs.get("exception", None)
    
    def serialise(self, **kwargs):
        schema = self.StartRaceResponseSchema(**kwargs)
        return schema.dump(self)


def start_race_for(user, request_start_race, player_update_result, **kwargs) -> StartRaceResult:
    """Upon request, start a race between the given User and the track specified in the start race request. When we create the race, the given user location in the update
    result should be added to the new track user race as the first position. The track will be read from the start race request by its UID. It will be checked that the Player
    is facing in the correct direction.

    Arguments
    ---------
    :user: The User who wishes to race.
    :request_start_race: An instance of RequestStartRace.
    :player_update_result: The result of passing the request through the player update process, and the very first position in the race.

    Returns
    -------
    Returns an instance of StartRaceResult."""
    try:
        # First, attempt to get the indicated Vehicle. If the vehicle's UID is None, check to ensure the User only has one vehicle OR if a vehicle is already selected.
        # If they have more than one, or no vehicle is selected, raise an exception. Otherwise, if vehicle UID isn't None, find it. If result is None though, fail for sure.
        if not request_start_race.vehicle_uid:
            if user.current_vehicle == None and user.num_vehicles > 1:
                raise RaceStartError(RaceStartError.REASON_NO_VEHICLE_UID)
            # This is the vehicle to use.
            vehicle = user.vehicles.first()
        else:
            # Otherwise, find it.
            vehicle = vehicles.find_vehicle_for_user(user, request_start_race.vehicle_uid)
            if not vehicle:
                raise RaceStartError(RaceStartError.REASON_NO_VEHICLE)
        # We will set this Vehicle as the current vehicle.
        user.set_current_vehicle(vehicle)
        # Locate the desired track, only verified tracks can be used. This can raise NoTrackFound and TrackCantBeRaced.
        track = tracks.find_existing_track(
            track_uid = request_start_race.track_uid)
        # Now, validate the User's position & countdown measurements to search for disqualification cases. This will either silently succeed, or will raise an error.
        _validate_new_race_intent(user, request_start_race, player_update_result)
        # Get the user location for the point logged at race start.
        user_location = player_update_result.user_location
        # Valid pre-race conditions. We can now build a track user race instance from the different components. Set the time started to the user location's logged at.
        # Obviously, set the Track and User to the subjects of the function call, set the CRS for this TrackUserRace to the configured CRS.
        track_user_race = models.TrackUserRace(
            started = user_location.logged_at)
        track_user_race.set_track_and_user(track, user)
        track_user_race.set_vehicle(vehicle)
        track_user_race.set_crs(config.WORLD_CONFIGURATION_CRS)
        # Add the user location in player update result to the track and return a start race result.
        track_user_race.add_location(user_location)
        # Add this race to the database and then instantiate a result.
        db.session.add(track_user_race)
        db.session.flush()
        # The result declares we have succesfully started the race.
        return StartRaceResult(True,
            track_user_race = track_user_race)
    except tracks.NoTrackFound as ntf:
        # Raise a race start error for reason REASON_NO_TRACK_FOUND.
        raise RaceStartError(RaceStartError.REASON_NO_TRACK_FOUND)
    except tracks.TrackCantBeRaced as tcbr:
        # Raise a race start error for reason REASON_TRACK_NOT_READY.
        raise RaceStartError(RaceStartError.REASON_TRACK_NOT_READY)
    except RaceDisqualifiedError as rde:
        """TODO: handle any failure to start the race. Return a False result, with appropriate error messaging."""
        raise NotImplementedError()
    except Exception as e:
        """TODO: handle any failure to start the race. Return a False result, with appropriate error messaging."""
        raise NotImplementedError()


class UpdateRaceParticipationResult():
    """A container for the result of updating a Player's ongoing race. The required argument should be a track user race, updated to reflect the most up to date state of the
    ongoing race, as reports for the result will be determined on that basis."""
    class RaceFinishedSchema(Schema):
        """A one-way message from the server to the client, informing them that the current race is complete."""
        class Meta:
            unknown = EXCLUDE
        race                    = fields.Nested(TrackUserRaceSchema, many = False, allow_none = True)

    class RaceProgressSchema(Schema):
        """A one-way message from the server to the client, informing them that the current race is ongoing."""
        class Meta:
            unknown = EXCLUDE
        race                    = fields.Nested(TrackUserRaceSchema, many = False, allow_none = True)

    class RaceDisqualifiedSchema(Schema):
        """A one-way message from the server to the client, informing them that their current race has been disqualified."""
        class Meta:
            unknown = EXCLUDE
        race                    = fields.Nested(TrackUserRaceSchema, many = False, allow_none = True)
        
    @property
    def is_finished(self):
        """Returns True if the race is now finished."""
        return self._track_user_race.is_finished
    
    @property
    def is_disqualified(self):
        """Returns True if the race has been disqualified."""
        return self._track_user_race.is_disqualified

    @property
    def race(self):
        return self._track_user_race

    def __init__(self, _track_user_race, **kwargs):
        self._track_user_race = _track_user_race

    def serialise(self, **kwargs):
        """Serialise the update race participation result to its response. The response will either be RaceFinishedSchema, RaceProgressSchema or RaceDisqualfiedSchema
        depending on the overall outcome."""
        if self.is_finished:
            schema = self.RaceFinishedSchema(**kwargs)
        elif self.is_disqualified:
            schema = self.RaceDisqualifiedSchema(**kwargs)
        else:
            schema = self.RaceProgressSchema(**kwargs)
        # Serialise the current result object through this schema and return.
        return schema.dump(self)


def update_race_participation_for(user, player_update_result, **kwargs) -> UpdateRaceParticipationResult:
    """Handle the given User's participation in any ongoing races. This function will check whether the User is currently racing, and depending on that result, make changes
    to the race's state given the update result. If the User has ventured too far from the track for too long or have skipped two or more checkpoints, they should be disqualified
    from continuing. Otherwise, the function should update all progress values on the race, and when the Player reaches the finish line, the race should be finished and preserved.

    Arguments
    ---------
    :user: The User for which we will update race participation.
    :player_update_result: An instance of PlayerUpdateResult, which represents the latest update.

    Returns
    -------
    Returns an instance of UpdateRaceParticipationResult."""
    try:
        # Check whether this User is actively involved in a race, before continuing. Return if not.
        ongoing_race = user.ongoing_race
        if not ongoing_race:
            """TODO: implement handler when User has no ongoing race."""
            raise NotImplementedError("update_race_participation_for requires an ongoing race in order to continue. This is not yet handled.")
        # Otherwise, this race is currently ongoing at the time of update. We will now create an association between the user location and ongoing race.
        ongoing_race.add_location(player_update_result.user_location)
        # Our next process will be to take the Track being raced and all progress thus far in the TrackUserRace and overlay the progress to ensure we have tagged every
        # checkpoint along the way. A failure to do so will disqualify Player.
        verify_progress_result = _verify_race_progress(user, ongoing_race, player_update_result)
        # Now, update all averages and related values for the given race.
        _update_race_averages(player_update_result, verify_progress_result, ongoing_race)
        # After verifying the race's progress and searching for disqualification criteria, then updating race averages and other values, we can finally check for the end
        # to the current race. This will return a result.
        if verify_progress_result.is_finished:
            # The race has been finished successfully. We will now set the ongoing race's finished parameter to the time logged in latest Player update.
            ongoing_race.set_finished(verify_progress_result.time_finished)
            return UpdateRaceParticipationResult(ongoing_race)
        # Otherwise, race is not finished just yet, return a negative result.
        return UpdateRaceParticipationResult(ongoing_race)
    except RaceDisqualifiedError as rde:
        # The race must be disqualified. Call the disqualification procedure.
        ongoing_race = disqualify_ongoing_race(rde.user, rde.dq_code,
            dq_extra_info = rde.dq_extra_info)
        # Return an update race participation result.
        return UpdateRaceParticipationResult(ongoing_race)
    except Exception as e:
        raise e


class CancelRaceResult():
    """A container for the result of cancelling a race."""
    REASON_NO_ONGOING_RACE = "no-ongoing-race"

    class CancelRaceResponseSchema(Schema):
        """A one-way message from the server to the client, informing them that their current race has been cancelled."""
        class Meta:
            unknown = EXCLUDE
        race                    = fields.Nested(TrackUserRaceSchema, many = False, allow_none = True)
        cancellation_reason     = fields.Str(allow_none = True)

    @property
    def race(self):
        return self._track_user_race
    
    @property
    def cancellation_reason(self):
        """Return the reason for the cancellation, if any. If race is None, this means there was no race to cancel."""
        if not self._track_user_race:
            return CancelRaceResult.REASON_NO_ONGOING_RACE
        return self._cancellation_reason
    
    def __init__(self, _track_user_race = None, _cancellation_reason = None, **kwargs):
        self._track_user_race = _track_user_race
        self._cancellation_reason = _cancellation_reason
    
    def serialise(self, **kwargs):
        """Serialise this result."""
        schema = self.CancelRaceResponseSchema(**kwargs)
        return schema.dump(self)


def cancel_ongoing_race(user, **kwargs) -> CancelRaceResult:
    """Cancel any ongoing race that may be attached to the User. This function will simply check for the ongoing race, then set it to a cancelled state. This function will
    always return a CancelRaceResult, which can be serialised to a cancelled response object.

    Arguments
    ---------
    :user: The User to cancel the ongoing race for.
    
    Returns
    -------
    An instance of cancel race result, which contains the cancellation outcome."""
    try:
        # Attempt to get the ongoing race.
        ongoing_race = get_ongoing_race(user)
        if ongoing_race:
            ongoing_race.cancel()
            # Flush transaction.
            db.session.flush()
            if config.APP_ENV == "Test":
                # If the environment is test, expire the User to reload from database.
                db.session.expire(user)
        return CancelRaceResult(ongoing_race)
    except Exception as e:
        """TODO: we can catch exceptions here, then pass the relevant reason codes to second argument for cancel race result ctor; CancelRaceResult(race, <reason code>)"""
        raise e
    

def disqualify_ongoing_race(user, dq_code, **kwargs) -> models.TrackUserRace:
    """Disqualify the ongoing race for this User. This will in turn execute the cancellation logic, but also offers additional opportunity for logging or the storage and
    persistence of this disqualification after the fact.

    Arguments
    ---------
    :user: The User for whom the ongoing Race should be disqualified.
    :dq_code: A code for the reason for disqualification.

    Keyword arguments
    -----------------
    :dq_extra_info: Any extra info to store alongside the disqualification, such as forensics data. Optional."""
    try:
        dq_extra_info = kwargs.get("dq_extra_info", None)
        # If there is an ongoing race, disqualify it.
        ongoing_race = user.ongoing_race
        if ongoing_race:
            ongoing_race.disqualify(dq_code,
                dq_extra_info = dq_extra_info)
            # Flush this.
            db.session.flush()
            # If environment is test, expire the race.
            if config.APP_ENV == "Test":
                db.session.expire(ongoing_race)
        return ongoing_race
    except Exception as e:
        raise e


def _validate_new_race_intent(user, request_start_race, player_update_result, **kwargs):
    """Validate a User's intent for a new race along with their position and countdown movement changes relative to the track they intend on racing. The goal of this function
    is to detect cheating through creeping or other false starts. That said, GPS is a real hit and miss, so some inaccuracies are expected and accepted; nothing we can do really.

    Arguments
    ---------
    :user: The User pushing the intent.
    :request_start_race: An instance of RequestStartRace.
    :player_update_result: The result of passing the request through the player update process, and the very first position in the race."""
    try:
        # Get the countdown location, which is the very first point, logged as soon as the countdown had begun. This will be a base player update as a schema. We will first build
        # a UserLocation instance out of the countdown position.
        if not request_start_race.countdown_position:
            """Countdown position can't be None. Please handle this correctly."""
            raise NotImplementedError("_validate_new_race_intent failed because no countdown position was given. And this case is unhandled.")
        countdown_user_location = world.prepare_user_location(request_start_race.countdown_position)
        # Get the race started User location, which is the very first point, logged as soon as the race state changed from countdown to GO!
        first_user_location = player_update_result.user_location
        """
        TODO: perform calculations confirming the countdown user location is facing in the same direction at the bearing from first point in track to second.
        TODO: perform calculations comparing countdown user location to first user location, allowing for some small discrepencies based on accuracy, fail if deviations too high.
        """
    except Exception as e:
        raise e


class VerifyRaceProgressResult():
    @property
    def is_finished(self):
        """Return a boolean indicating the race's result."""
        return self._is_finished

    @property
    def time_finished(self):
        """Return a timestamp, in seconds, at which the race was finished."""
        return self._time_finished

    @property
    def percent_complete(self):
        """Return the percent of this track complete."""
        """TODO: when we implement circuits, this should be moved."""
        return self._percent_complete
    
    @property
    def percent_missed(self):
        """Return the total percent of this track that has been missed."""
        return self._percent_missed
    
    def __init__(self, _is_finished = False, _percent_complete = 0, _percent_missed = 0, **kwargs):
        self._is_finished = _is_finished
        self._percent_complete = _percent_complete
        self._percent_missed = _percent_missed
        self._time_finished = kwargs.get("time_finished", None)


def _verify_race_progress(user, track_user_race, player_update_result, **kwargs) -> VerifyRaceProgressResult:
    """Verify the progress in the given Player's ongoing race. This involves checking that the Player is travelling the right direction and has not taken any
    shortcuts not included in the race and also ensuring the Player has not gone the wrong direction. This function must also ensure there no breaks/connection
    losses in the race.

    Arguments
    ---------
    :user: An instance of User, involved in the race.
    :track_user_race: An instance of TrackUserRace, which represents the ongoing race.
    :player_update_result: The latest Player update result."""
    try:
        # If the race does not yet have progress, silently return.
        if not track_user_race.has_progress:
            return VerifyRaceProgressResult()
        user_location = player_update_result.user_location
        # Calculate and validate progress thus far.
        race_progress_result = _calculate_validate_progress(track_user_race, user_location)
        # Check for disqualification criteria. Ensure percent of track missed is not over what is acceptable.
        if race_progress_result.percent_track_missed > config.MAX_PERCENT_PATH_MISSED_DISQUALIFY:
            raise PlayerDodgedTrackError(int(race_progress_result.percent_track_missed))
        # Finally, we'll determine if the Player has finished the course. Currently, we'll do this by just ensuring the finish point is within the progress polygon.
        if race_progress_result.is_race_finished:
            # Return a race progress result where we declare the race is finished.
            return VerifyRaceProgressResult(True, 100, race_progress_result.percent_track_missed, 
                time_finished = user_location.logged_at)
        else:
            LOG.debug(f"{track_user_race} is not finished ({race_progress_result.percent_complete}% complete)")
        return VerifyRaceProgressResult(False, race_progress_result.percent_complete, race_progress_result.percent_track_missed)
    except PlayerDodgedTrackError as pdte:
        LOG.warning(f"Disqualifying race {track_user_race}, the Player has dodged too much of the track. ({pdte.percent_dodged}%)")
        raise RaceDisqualifiedError(user, track_user_race,
            dq_code = RaceDisqualifiedError.DQ_CODE_MISSED_TRACK)
    except Exception as e:
        raise e


def _update_race_averages(player_update_result, verify_progress_result, track_user_race, **kwargs):
    """Update the given race's average values based on the latest update. This function is track type agnostic and will therefore handle the updating of all track
    types and their respective progress. Ensure progress result object passed is compatible. This function will update all values, but will not return any values.

    Arguments
    ---------
    :player_update_result: The latest Player update result.
    :verify_progress_result: A result of verifying the given race's progress.
    :track_user_race: An instance of TrackUserRace, which represents the ongoing race."""
    try:
        # Begin by comprehending a list of all speeds in the race progress.
        race_speeds = [x.speed for x in track_user_race.progress if x.speed is not None]
        # Calculate and set the average speed.
        average_speed = int(sum(race_speeds) / len(race_speeds))
        track_user_race.set_average_speed(average_speed)
        # Now set the percent of track that has been missed.
        track_user_race.set_percent_missed(verify_progress_result.percent_missed)
        # Now, differentiate between sprints and circuits.
        if track_user_race.track.is_sprint:
            # If this is a sprint, we'll update percent complete.
            track_user_race.set_percent_complete(verify_progress_result.percent_complete)
        elif track_user_race.track.is_circuit:
            """TODO: handle circuits here."""
            raise NotImplementedError(f"Failed to update race averages for {track_user_race}, circuits are not handled.")
        else:
            raise NotImplementedError(f"Failed to update race avergaes for {track_user_race}, this is an unknown race type; {track_user_race.track.track_type}")
    except Exception as e:
        raise e


"""TODO: Notes for implementing Circuits:
RaceProgressResult could become a base class, holding only is_race_finished then subclass twice:
    SprintRaceProgressResult
      -> meters_missed_track
      -> percent_track_missed
      -> percent_complete
    CircuitRaceProgressResult
      -> num_laps_complete
_calculate_validate_progress will then differentiate between exact return type, but typing hint will return RaceProgressResult.

Then; all other dependant functions will have separate logic for circuit & sprints. (except _ensure_player_still_racing):
_is_race_finished                       -> _is_sprint_race_finished, _is_circuit_race_finished
_determine_missed_sections              -> _determine_sprint_missed_sections, _determine_circuit_missed_sections"""
class RaceProgressResult():
    """A container for the result of calculating the player's progress through the track."""
    @property
    def percent_remaining(self):
        """The percent of track remaining; this is simply 100 minus complete."""
        return 100-self.percent_complete
    
    @property
    def percent_track_missed(self):
        """The percent of race/track that has been dodged."""
        return self._percent_track_missed
    
    def __init__(self, _is_race_finished, _progress_linestring, _track_path_linestring, _missed_track_linestrings):
        self.is_race_finished = _is_race_finished
        self._track_path_linestring = _track_path_linestring
        self._missed_track_linestrings = _missed_track_linestrings
        # Now, the list of missed track linestrings are those we will check against the track overall for disqualification. Sum the lengths of them all.
        self.meters_missed_track = sum([ls.length for ls in self._missed_track_linestrings])
        # Calculate percent missed too.
        self._percent_track_missed = int((self.meters_missed_track/_track_path_linestring.length) * 100)
        # Calculate percent complete, which currently is progress linestring's length as a percent of the track path's linestring.
        """TODO: this is not precise, improve this; especially if Player missed sections."""
        self.percent_complete = int((_progress_linestring.length/_track_path_linestring.length) * 100)


class SprintRaceProgressResult(RaceProgressResult):
    """A race progress result subtype specifically for sprint type tracks."""
    @property
    def percent_remaining(self):
        """The percent of track remaining; this is simply 100 minus complete."""
        return 100-self._percent_complete
    
    @property
    def percent_complete(self):
        """The percent of track that has been completed."""
        return self._percent_complete
    
    def __init__(self, _is_race_finished, _progress_linestring, _track_path_linestring, _missed_track_linestrings):
        super().__init__(_is_race_finished, _progress_linestring, _track_path_linestring)
        self._missed_track_linestrings = _missed_track_linestrings
        # Now, the list of missed track linestrings are those we will check against the track overall for disqualification. Sum the lengths of them all.
        self.meters_missed_track = sum([ls.length for ls in self._missed_track_linestrings])
        # Calculate percent missed too.
        self._percent_track_missed = int((self.meters_missed_track/_track_path_linestring.length) * 100)
        # Calculate percent complete, which currently is progress linestring's length as a percent of the track path's linestring.
        """TODO: this is not precise, improve this; especially if Player missed sections."""
        self._percent_complete = int((_progress_linestring.length/_track_path_linestring.length) * 100)


class CircuitRaceProgressResult(RaceProgressResult):
    """A race progress result subtype specifically for circuit type tracks."""
    def __init__(self, _is_race_finished, _progress_linestring, _track_path_linestring):
        super().__init__(_is_race_finished, _progress_linestring, _track_path_linestring)


def _calculate_validate_progress(track_user_race, latest_user_location, **kwargs) -> RaceProgressResult:
    """Calculate and validate progress made by the User thus far in the race attempt. The location provided should already be added to the race. This function will first find the
    difference between the track's path and a linestring built from all user locations provided that have been associated with this race attempt. The result of this differentiation
    will then allow the function to determine if the track has been dodged, missed or ignored. The function will also determine whether the race's attempt has qualified for completion.

    Arguments
    ---------
    :track_user_race: An instance of UserTrackRace, containing the current state of the User's race attempt.
    :latest_user_location: An instance of UserLocation, the most recent loaded player location being used to update the race.
    
    Returns
    -------
    An instance of RaceProgressResult containing the results of calculations performed."""
    try:
        track = track_user_race.track
        track_path = track.path
        # Start by calculating that differentiation. Do this by buffering the Player's progress, then calculating the symmetric difference with the track's path.
        """TODO: we currently don't support multi lines directly, so simply grab a linestring from the geoms.
        TODO: when creating progress polygon, we should actually refrain from buffering in the direction the track is going, as this could result in a false finish."""
        track_path_linestring = track_path.multi_linestring.geoms[0]
        progress_linestring = track_user_race.linestring
        progress_polygon = progress_linestring\
            .buffer(config.NUM_METERS_BUFFER_PLAYER_PROGRESS,
                cap_style = shapely.geometry.CAP_STYLE.square)
        # Ensure the Player has not driven too far from the track. This will raise an exception.
        _ensure_player_still_racing(track_path_linestring, latest_user_location)
        # Determine, on the basis of the progress polygon & track path, whether the race is now finished.
        is_race_finished = _is_race_finished(track_path, track_path_linestring, progress_polygon)
        # Calculate a symmetric difference between the player's progress and the track path.
        race_symmetric_difference = progress_polygon.symmetric_difference(track_path_linestring)
        """TODO: we want to ensure that for now only polygons and linestrings can appear in the result, if it is a geometrycollection. We want to track potential requirements for other
        types to support as time goes on, and as we feed more data."""
        if isinstance(race_symmetric_difference, shapely.geometry.GeometryCollection):
            for symdif_geom in race_symmetric_difference.geoms:
                if not isinstance(symdif_geom, shapely.geometry.Polygon) and not isinstance(symdif_geom, shapely.geometry.LineString):
                    raise NotImplementedError(f"_calculate_validate_progress failed. We currently only support Polygon and LineStrings in symmetric diff result. But we have found {type(symdif_geom)} is used.")
        # Find all missed track sections.
        missed_track_linestrings = _determine_missed_sections(is_race_finished, race_symmetric_difference)
        # Return our race progress result.
        return RaceProgressResult(is_race_finished, progress_linestring, track_path_linestring, missed_track_linestrings)
    except Exception as e:
        raise e


def _ensure_player_still_racing(track_path_linestring, latest_user_location):
    """Ensure the Player has deviated from the track's path to an unacceptable extent, configured with the NUM_METERS_MAX_DEVIATION_DISQUALIFY option. If the function determines
    the Player should be disqualified on this basis, it will raise an error. Otherwise, it will silently succeed.

    Arguments
    ---------
    :track_path_linestring: A Shapely LineString geometry containing the track.
    :latest_user_location: A UserLocation representing the latest accepted update from the Player."""
    try:
        """TODO: finish _ensure_player_still_racing"""
        pass
    except Exception as e:
        raise e
    

def _is_race_finished(track_path, track_path_linestring, progress_polygon, **kwargs) -> bool:
    """"""
    try:
        """TODO: make this more complex. I don't think the finish point being within the progress is the best indication of course completion."""
        return track_path.finish_point.within(progress_polygon)
    except Exception as e:
        raise e


def _determine_missed_sections(is_race_finished, race_symmetric_difference, **kwargs):
    """"""
    try:
        if is_race_finished:
            # We have finished the race. If race_symmetric_difference is a Polygon, this means there is NO track missed.
            if isinstance(race_symmetric_difference, shapely.geometry.Polygon):
                missed_track_linestrings = []
            elif isinstance(race_symmetric_difference, shapely.geometry.GeometryCollection):
                # This is a GeometryCollection, meaning there were probably fragments missed.
                missed_track_linestrings = list(race_symmetric_difference.geoms)[1:]
        else:
            # Otherwise, race is not yet finished. If race_symmetric_difference is NOT a GeometryCollection, raise an exception.
            if not isinstance(race_symmetric_difference, shapely.geometry.GeometryCollection):
                """TODO: handle this edge case."""
                raise NotImplementedError("When the race is not yet finished, race_symmetric_difference should ALWAYS be a GeometryCollection?")
            # Check the number of geometries in this collection. If there are two (the Polygon and a LineString,) this means the Player has not skipped any sections.
            race_symmetric_difference_geoms = list(race_symmetric_difference.geoms)
            if len(race_symmetric_difference_geoms) == 2:
                # As long as the first is a Polygon and the second a LineString, there are no sections missed.
                if not isinstance(race_symmetric_difference_geoms[0], shapely.geometry.Polygon) or not isinstance(race_symmetric_difference_geoms[1], shapely.geometry.LineString):
                    """TODO: only 2 geoms found in race symmetric difference."""
                    raise NotImplementedError("Two geometries found in race_symmetric_difference_geoms, when race is not yet finished, and they are not Polygon & LineString respectively.")
                else:
                    # Otherwise, no sections missed.
                    missed_track_linestrings = []
            elif len(race_symmetric_difference_geoms) > 2:
                # There are more than two geometries. Get all linestrings after the first geometry (the Polygon) and before the last (LineString, that is the remainder of the track.) These
                # are missed track sections.
                missed_track_linestrings = race_symmetric_difference_geoms[1:len(race_symmetric_difference_geoms)-1]
            else:
                # Only one geom? This is not supported.
                raise NotImplementedError("_determine_missed_sections failed. There is a single geom in race symmetric difference.")
        return missed_track_linestrings
    except Exception as e:
        raise e
    

