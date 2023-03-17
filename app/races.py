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

from . import db, config, models, decorators, world, error

LOG = logging.getLogger("hawkspeed.races")
LOG.setLevel( logging.DEBUG )


class RaceSchema(Schema):
    """A Schema that outlines the structure of a report on a TrackUserRace, for the Player currently performing that race."""
    uid                     = fields.Str()
    track_uid               = fields.Str()
    started                 = fields.Int()
    finished                = fields.Int(allow_none = True)
    disqualified            = fields.Bool()
    dq_reason               = fields.Str(allow_none = True)
    """dq_extra_info"""
    cancelled               = fields.Bool()


class StartRaceResult():
    """A container for storing the result of starting a new race."""
    @property
    def is_started(self):
        return self._started

    @property
    def race(self):
        if not self._started:
            return None
        return self._track_user_race

    @property
    def error_code(self):
        return self._error_code

    def __init__(self, _started, **kwargs):
        self._started = _started
        self._track_user_race = kwargs.get("track_user_race", None)
        self._error_code = kwargs.get("error_code", None)


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
        # The result declares we have succesfully started the race.
        return StartRaceResult(True,
            track_user_race = track_user_race)
    except error.RaceDisqualifiedError as rde:
        """TODO: handle any failure to start the race. Return a False result, with appropriate error messaging."""
        raise NotImplementedError()
    except Exception as e:
        """TODO: handle any failure to start the race. Return a False result, with appropriate error messaging."""
        raise NotImplementedError()


class UpdateRaceParticipationResult():
    """A container for the result of updating the status of an ongoing race, this result will also contain an indication if this race has been successfully complete.
    However, an indication of disqualification or other interruptions will not be communicated with this result, exceptions will be used instead."""
    @property
    def is_finished(self):
        return self._is_finished

    @property
    def track_user_race(self):
        return self._track_user_race

    def __init__(self, _is_finished, _track_user_race, **kwargs):
        self._is_finished = _is_finished
        self._track_user_race = _track_user_race


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
        _update_race_averages(user, ongoing_race, player_update_result)
        # After verifying the race's progress and searching for disqualification criteria, then updating race averages and other values, we can finally check for the end
        # to the current race. This will return a result.
        if verify_progress_result.is_finished:
            # The race has been finished successfully. We will now set the ongoing race's finished parameter to the time logged in latest Player update.
            ongoing_race.set_finished(verify_progress_result.time_finished)
            return UpdateRaceParticipationResult(True, ongoing_race)
        # Otherwise, race is not finished just yet, return a negative result.
        return UpdateRaceParticipationResult(False, ongoing_race)
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
        dq_extra_info = kwargs.get("dq_extra_info", None)
        # If there is an ongoing race, disqualify it.
        ongoing_race = user.ongoing_race
        if ongoing_race:
            ongoing_race.disqualify(dq_code,
                dq_extra_info = dq_extra_info)
        return ongoing_race
    except Exception as e:
        raise e


def cancel_ongoing_race(user, **kwargs) -> models.TrackUserRace:
    """Cancel any ongoing race that may be attached to the User. This function will simply check for the ongoing race, then set it to a cancelled state. The TrackUserRace
    instance will then be returned. If there is no ongoing race, this function will return None.

    Arguments
    ---------
    :user: The User to cancel the ongoing race for."""
    try:
        # If there is an ongoing race, cancel it.
        ongoing_race = user.ongoing_race
        if ongoing_race:
            ongoing_race.cancel()
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
        user_location = player_update_result.user_location
        track_path = track_user_race.track.path
        """TODO: we currently don't support multi lines directly, so simply grab a linestring from the geoms."""
        track_path_linestring = track_path.multi_linestring.geoms[0]
        # Get the Player's progress thus far, as a line string, and buffer it such that it is a Polygon slightly larger on all sides than the track geometry.
        progress_polygon = track_user_race.linestring\
            .buffer(config.NUM_METERS_BUFFER_PLAYER_PROGRESS, cap_style = shapely.geometry.CAP_STYLE.square)
        # Ensure the Player has not driven too far from the track.
        _ensure_player_still_racing(track_path_linestring, user_location)
        # Determine, on the basis of the progress polygon & track path, whether the race is now finished.
        """TODO: make this more complex. I don't think the finish point being within the progress is the best indication of course completion."""
        has_finished_race = track_path.finish_point.within(progress_polygon)
        # Ensure the Player hasn't dodged the track at all. This will include all unauthorised shortcuts.
        _ensure_player_hasnt_dodged_track(track_path_linestring, progress_polygon, has_finished_race)
        # Finally, we'll determine if the Player has finished the course. Currently, we'll do this by just ensuring the finish point is within the progress polygon.
        if has_finished_race:
            # Return a race progress result where we declare the race is finished.
            return VerifyRaceProgressResult(True, time_finished = user_location.logged_at)
        return VerifyRaceProgressResult(False)
    except error.PlayerDodgedTrackError as pdte:
        LOG.warning(f"Disqualifying race {track_user_race}, the Player has dodged too much of the track. ({pdte.percentage_dodged}%)")
        raise error.RaceDisqualifiedError(user, track_user_race,
            dq_code = "missed-track")
    except Exception as e:
        raise e


def _update_race_averages(user, track_user_race, player_update_result, **kwargs):
    """Update the given race's average values based on the latest update.
    This function will update all values, but will not return any values.

    Arguments
    ---------
    :user: An instance of User, involved in the race.
    :track_user_race: An instance of TrackUserRace, which represents the ongoing race.
    :player_update_result: The latest Player update result."""
    try:
        # Begin by comprehending a list of all speeds in the race progress.
        race_speeds = [x.speed for x in track_user_race.progress if x.speed is not None]
        # Calculate and set the average speed.
        track_user_race.set_average_speed(sum(race_speeds) / len(race_speeds))
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


def _ensure_player_hasnt_dodged_track(track_path_linestring, progress_polygon, has_finished_race):
    """Determine the symmetric difference between the progress polygon and the track's path linestring. If the return is a Polygon geometry, the Player's progress totally
    contains the track, and therefore this is synonymous with the race being finished. Otherwise, if the result is a GeometryCollection, the first item in the collection
    must be the progress polygon, the rest should all be LineString instances.

    A single linestring (when has_finished_race is False) indicates that the Player has not so far missed any components of the track and that the race is ongoing. However,
    if has_finished_race is False, and there are multiple LineStrings, the race can be disqualified if the Player has already missed too much of the race; in this case, take
    all LineStrings EXCEPT the one representing remainder of the track. If has_finished_race is True, disqualify the Player if they have missed too much, purely on the basis
    of a single LineString.

    Arguments
    ---------
    :track_path_linestring: The Track's LineString.
    :progress_points: A Polygon, which is the Player's progress LineString that has been buffered.
    :has_finished_race: A boolean indicating whether or not we've determined that the Player has finished the race."""
    try:
        race_symmetric_difference = progress_polygon.symmetric_difference(track_path_linestring)

        """TODO: we want to ensure that for now only polygons and linestrings can appear in the result, if it is a geometrycollection. We want to track potential requirements for other
        types to support as time goes on, and as we feed more data."""
        if isinstance(race_symmetric_difference, shapely.geometry.GeometryCollection):
            for x in race_symmetric_difference.geoms:
                if not isinstance(x, shapely.geometry.Polygon) and not isinstance(x, shapely.geometry.LineString):
                    raise NotImplementedError(f"_analyse_race failed. We currently only support Polygon and LineStrings in symmetric diff result. But we have found {type(x)} is used.")

        if has_finished_race:
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
                raise NotImplementedError("_analyse_race failed. There is a single geom in race symmetric difference.")
        # Now, the list of missed track linestrings are those we will check against the track overall for disqualification. Sum the lengths of them all.
        missed_track_lengths_sum = sum([ls.length for ls in missed_track_linestrings])
        # Check if the Player has missed too much, raise a disqualification error if they have.
        percentage_track_missed = (missed_track_lengths_sum/track_path_linestring.length) * 100
        if percentage_track_missed > config.MAX_PERCENT_PATH_MISSED_DISQUALIFY:
            raise error.PlayerDodgedTrackError(int(percentage_track_missed))
    except Exception as e:
        raise e
