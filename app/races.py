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
from sqlalchemy import func
from marshmallow import fields, Schema, post_load, EXCLUDE

from . import db, config, models, decorators, world

LOG = logging.getLogger("hawkspeed.races")
LOG.setLevel( logging.DEBUG )


class StartRaceResult():
    """A container for storing the result of starting a new race."""
    @property
    def race_uid(self):
        return self._track_user_race.uid

    def __init__(self, track_user_race, **kwargs):
        self._track_user_race = track_user_race


def start_race_for(user, start_race_d, player_update_result, **kwargs) -> StartRaceResult:
    """Upon request, start a race between the given User and the track specified in the start race request. When we create the race, the given user location in the update
    result should be added to the new track user race as the first position. The track will be read from the start race request by its UID. It will be checked that the Player
    is facing in the correct direction.

    Arguments
    ---------
    :user: The User who wishes to race.
    :start_race_d: A loaded StartRaceRequestSchema.
    :player_update_result: The result of passing the request through the player update process, and the very first position in the race.

    Returns
    -------
    Returns an instance of StartRaceResult."""
    try:
        track_uid = start_race_d.get("track_uid", None)
        # Locate the desired track, only verified tracks can be used.
        track = db.session.query(models.Track)\
            .filter(models.Track.uid == track_uid)\
            .filter(models.Track.verified == True)\
            .first()
        # If no track, raise an exception.
        if not track:
            """TODO: handle this correctly."""
            raise NotImplementedError("start_race_for failed because no track could be found, and this case is NOT yet handled.")
        # Now, validate the User's position & countdown measurements to search for disqualification cases. This will either silently succeed, or will raise an error.
        _validate_new_race_intent(user, start_race_d, player_update_result)
        # Get the user location for the point logged at race start.
        user_location = player_update_result.user_location
        # Valid pre-race conditions. We can now build a track user race instance from the different components. Set the time started to the user location's logged at.
        # Obviously, set the Track and User to the subjects of the function call, set the CRS for this TrackUserRace to the configured CRS.
        track_user_race = models.TrackUserRace(started = user_location.logged_at)
        track_user_race.set_track_and_user(track, user)
        track_user_race.set_crs(config.WORLD_CONFIGURATION_CRS)
        # Add the user location in player update result to the track and return a start race result.
        track_user_race.add_location(user_location)
        # Add this race to the database and then instantiate a result.
        db.session.add(track_user_race)
        db.session.flush()
        return StartRaceResult(track_user_race)
    except Exception as e:
        raise e


class UpdateRaceParticipationResult():
    """A container for the result of updating the status of an ongoing race, this result will also contain an indication if this race has been successfully complete.
    However, an indication of disqualification or other interruptions will not be communicated with this result, exceptions will be used instead."""
    @property
    def is_finished(self):
        return self._is_finished

    def __init__(self, _is_finished, **kwargs):
        self._is_finished = _is_finished


def update_race_participation_for(user, player_update_result, **kwargs) -> UpdateRaceParticipationResult:
    """Handle the given User's participation in any ongoing races. This function will check whether the User is currently racing, and depending on that result, make changes
    to the race's state given the update result. If the User has ventured too far from the track for too long or have skipped two or more checkpoints, they should be disqualified
    from continuing and their track race object deleted. Otherwise, the function should update all progress values on the race, and when the Player reaches the finish line, the race
    should be finished and preserved.

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
        _update_race_averages(user, ongoing_race, player_update_result)
        # After verifying the race's progress and searching for disqualification criteria, then updating race averages and other values, we can finally check for the end
        # to the current race. This will return a result.
        if verify_progress_result.is_finished:
            # The race has been finished successfully. We will now set the ongoing race's finished parameter to the time logged in latest Player update.
            ongoing_race.set_finished(verify_progress_result.time_finished)
            return UpdateRaceParticipationResult(True)
        # Otherwise, race is not finished just yet, return a negative result.
        return UpdateRaceParticipationResult(False)
    except error.RaceDisqualifiedError as rde:
        # The race must be disqualified. Call the disqualification procedure.
        disqualify_ongoing_race(rde.user, rde.dq_code,
            dq_extra_info = rde.dq_extra_info)
        # Finally, re-raise this exception once disqualification is complete, so the appropriate response will be sent to Player.
        raise rde
    except Exception as e:
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
        dq_extra_info = kwargs.get("dq_extra_info", dict())
        # First, we will cancel the ongoing race, get back the track user race. If the result is None raise an exception as there is no race to disqualify User for.
        cancelled_race = cancel_ongoing_race(user)
        if not cancelled_race:
            """TODO: handle the case in which a race can't be disqualified because there was none."""
            raise NotImplementedError("disqualify_ongoing_race failed, because the User does not have an ongoing race.")
        """
        TODO: otherwise, store the DQ code and extra info if required.
        """
        # Return the cancelled race.
        return cancelled_race
    except Exception as e:
        raise e


def cancel_ongoing_race(user, **kwargs) -> models.TrackUserRace:
    """Cancel any ongoing race that may be attached to the User. This function will simply check for the ongoing race, then delete it from database. The TrackUserRace
    instance will then be returned. If there is no ongoing race, this function will return None.

    Arguments
    ---------
    :user: The User to clear the ongoing race for."""
    try:
        ongoing_race = user.ongoing_race
        if ongoing_race:
            # We have a race. Delete it from the database.
            db.session.delete(ongoing_race)
        # Return whether None or not.
        return ongoing_race
    except Exception as e:
        raise e


def _validate_new_race_intent(user, start_race_d, player_update_result, **kwargs):
    """Validate a User's intent for a new race along with their position and countdown movement changes relative to the track they intend on racing. The goal of this function
    is to detect cheating through creeping or other false starts. That said, GPS is a real hit and miss, so some inaccuracies are expected and accepted; nothing we can do really.

    Arguments
    ---------
    :user: The User pushing the intent.
    :start_race_d: A loaded instance of StartRaceRequestSchema.
    :player_update_result: The result of passing the request through the player update process, and the very first position in the race."""
    try:
        # Get the countdown location, which is the very first point, logged as soon as the countdown had begun. This will be a base player update as a schema. We will first build
        # a UserLocation instance out of the countdown position.
        countdown_position_d = start_race_d.get("countdown_position", None)
        if not countdown_position_d:
            """Countdown position can't be None. Please handle this correctly."""
            raise NotImplementedError("_validate_new_race_intent failed because no countdown position was given. And this case is unhandled.")
        countdown_user_location = world.prepare_user_location(countdown_position_d)
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

    def __init__(self, _is_finished, **kwargs):
        self._is_finished = _is_finished
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
            return VerifyRaceProgressResult(False)
        # Get the latest user location update.
        user_location = player_update_result.user_location
        # Get the track path.
        track_path = track_user_race.track.path
        # Get the overall course as a line string.
        """TODO: we currently don't support multi lines directly, so simply grab a linestring from the geoms."""
        track_path_linestring = track_path.multi_linestring.geoms[0]
        # Get the Player's progress thus far, as a line string, and buffer it such that it is a Polygon slightly larger on all sides than the track geometry.
        progress_linestring = track_user_race.linestring
        progress_polygon = progress_linestring\
            .buffer(config.NUM_METERS_BUFFER_PLAYER_PROGRESS, cap_style = shapely.geometry.CAP_STYLE.square)
        """TODO: ensure frequency of updates (user locations) do not indicate that connection was ever lost."""
        """TODO: check direction Player is travelling in, verify it is the correct way, warn otherwise or disqualify (determine criteria for disqualification for this case.)"""
        # Now, we'll check for the Player taking unauthorised shortcuts.
        """TODO: do this."""
        """
        We can use shapely's symmetric_difference to work out where the track has not been travelled. Some example code:
        track_path = race.track.path.multi_linestring
        # Buffer the Player's progress.
        # Get the progress linestring.
        progress_linestring = race.linestring
        # Now, buffer this line by out configured buffer value.
        buffered_player_progress = progress_linestring.buffer(config.NUM_METERS_BUFFER_PLAYER_PROGRESS, cap_style = geometry.CAP_STYLE.square)

        import pyproj
        import shapely
        t = pyproj.Transformer.from_crs(3112, 4326, always_xy = True)

        x1 = buffered_player_progress.symmetric_difference(track_path)
        xx = shapely.ops.transform(t.transform, x1)

        print(draw.lines_to_geojson([xx.geoms[1]]))
        #print(draw.polygons_to_geojson([buffered_player_progress], 3112))
        """
        # Finally, we'll determine if the Player has finished the course. Currently, we'll do this by just ensuring the finish point is within the progress polygon.
        if track_path.finish_point.within(progress_polygon):
            """TODO: make this more complex. I don't think just touching the finish point should be thought of as completing the course."""
            # Return a race progress result where we declare the race is finished.
            return VerifyRaceProgressResult(True, time_finished = user_location.logged_at)
        return VerifyRaceProgressResult(False)
    except Exception as e:
        raise e


def _update_race_averages(user, track_user_race, player_update_result, **kwargs):
    """Update the given race's average values based on the latest update. This function will update all values, but will not return any values.

    Arguments
    ---------
    :user: An instance of User, involved in the race.
    :track_user_race: An instance of TrackUserRace, which represents the ongoing race.
    :player_update_result: The latest Player update result."""
    try:
        pass
    except Exception as e:
        raise e
