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
    """"""
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
        # Obviously, set the Track and User to the subjects of the function call.
        track_user_race = models.TrackUserRace(started = user_location.logged_at)
        track_user_race.set_track_and_user(track, user)
        # Add the user location in player update result to the track and return a start race result.
        track_user_race.add_location(user_location)
        # Add this race to the database and then instantiate a result.
        db.session.add(track_user_race)
        db.session.flush()
        return StartRaceResult(track_user_race)
    except Exception as e:
        raise e


class UpdateRaceParticipationResult():
    """"""
    pass


def update_race_participation_for(user, player_update_result, **kwargs) -> UpdateRaceParticipationResult:
    """Handle the given User's participation in any ongoing races. This function will check whether the User is currently racing, and depending on that result, make changes
    to the race's state given the update result. If the User has ventured too far from the track for too long or have skipped two or more checkpoints, they should be disqualified
    from continuing and their track race object deleted. Otherwise, the function should update all progress values on the race.

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
            return
        # Otherwise, this race is currently ongoing at the time of update. We will now create an association between the user location and ongoing race.
        ongoing_race.add_location(player_update_result.location)
        """
        TODO: this is where we will take into account the entire race's progress up until this point to determine what we do next.
        TODO: loop progress, calculating averages etc.
        """
        # Create and return a result.
    except Exception as e:
        raise e


def cancel_ongoing_race(user, **kwargs) -> models.TrackUserRace:
    """Cancel any ongoing race that may be attached to the User. This function will simply check for the ongoing race, then delete it from database. The TrackUserRace
    instance will then be returned. If there is no ongoing race, this function will return None.

    Arguments
    ---------
    :user: The User to clear all races for."""
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
