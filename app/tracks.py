"""A module for handling the creation, verification, completion and management of User created tracks."""
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
from sqlalchemy import func, asc, desc
from sqlalchemy.orm import with_expression
from marshmallow import fields, Schema, post_load, EXCLUDE

from . import db, config, models, decorators, error

LOG = logging.getLogger("hawkspeed.tracks")
LOG.setLevel( logging.DEBUG )


class TrackAlreadyExists(Exception):
    """An exception that communicates the track the User has attempted to create already exists."""
    pass


class TrackInspectionFailed(Exception):
    """An exception that communicates a failure in the inspection/validation portion of creating a new track."""
    def __init__(self, error_code, **kwargs):
        self.error_code = error_code
        self.extra_info = kwargs.get("extra_info", None)


def page_leaderboard_for(track, page, **kwargs):
    """Return a query for the desired page of the given track's leaderboard. The leaderboard is simply ordered from slowest stopwatch time to fasted stopwatch time. This function
    will return the query object itself, which can be paginated or received in full.

    Arguments
    ---------
    :track: An instance of Track.
    :page: The page from which to query the leaderboard entries."""
    try:
        # Employ a query_expression / with_expression option on this query, to fill in the place in the leaderboard for each race outcome.
        # We will only include races that are confirmed finished in this query.
        leaderboard_q = db.session.query(models.TrackUserRace)\
            .filter(models.TrackUserRace.is_finished == True)\
            .filter(models.TrackUserRace.track_id == track.id)\
            .order_by(asc(models.TrackUserRace.stopwatch))\
            .options(
                with_expression(models.TrackUserRace.finishing_place,
                    func.row_number()
                        .over(order_by = asc(models.TrackUserRace.stopwatch))))
        return leaderboard_q
    except Exception as e:
        raise e


def page_comments_for(track, page, **kwargs):
    """Return a query for the desired page of the given Track's comments section. The resulting query will be ordered by the newest comments to the oldest comments
    unless specified otherwise in keyword arguments.

    Arguments
    ---------
    :track: An instance of Track.
    :page: The page from which to query the comments."""
    try:
        # Create a new query for the track comment model, that will filter for the given track, order the results by created in a descending fashion.
        comments_q = db.session.query(models.TrackComment)\
            .filter(models.TrackComment.track_id == track.id)\
            .order_by(desc(models.TrackComment.created))
        return comments_q
    except Exception as e:
        raise e


class RatingsSchema(Schema):
    """A schema for dumping a track's ratings."""
    class Meta:
        unknown = EXCLUDE
    # The number of positive ratings. Can't be None.
    num_positive_votes  = fields.Int(required = True, allow_none = False)
    # The number of negative ratings. Can't be None.
    num_negative_votes  = fields.Int(required = True, allow_none = False)


class Ratings():
    """A container for the ratings of a track."""
    def __init__(self, num_positive, num_negative):
        self.num_positive_votes = num_positive
        self.num_negative_votes = num_negative


def get_ratings_for(track, **kwargs) -> Ratings:
    """Get the ratings from the given track, and return a Ratings object."""
    try:
        # Perform a query for this, where we'll select the count of the rating column, then group by that column.
        ratings = db.session.query(models.TrackRating.rating, func.count(models.TrackRating.rating))\
            .filter(models.TrackRating.track_id == track.id)\
            .order_by(desc(models.TrackRating.rating))\
            .group_by(models.TrackRating.rating)\
            .all()
        # We should now have a list of tuples, where each tuple contains a boolean; the rating, and an integer; the associated count. Build a dictionary
        # from this mapping rating, the names compatible with ratings schema, and their associated counts.
        ratings_d = dict(ratings)
        return Ratings(ratings_d.get(True, 0), ratings_d.get(False, 0))
    except Exception as e:
        raise e
    

def get_user_rating(track, user, **kwargs) -> bool:
    """Get the given User's rating created toward the given Track. This will return True if they have upvoted the Track, False if downvoted or None if
    they have not yet voted for the Track."""
    try:
        return db.session.query(models.TrackRating.rating)\
            .filter(models.TrackRating.track_id == track.id)\
            .filter(models.TrackRating.user_id == user.id)\
            .scalar()
    except Exception as e:
        raise e
    

def find_existing_track(**kwargs):
    """"""
    try:
        track_hash = kwargs.get("track_hash", None)

        """TODO: improve this function."""
        return models.Track.find(track_hash = track_hash)
    except Exception as e:
        raise e
    

class TrackPointSchema(Schema):
    """A schema used to serialise a single track point. This is primarily used by the TrackPathViewModel found in viewmodels."""
    class Meta:
        unknown = EXCLUDE
    track_uid               = fields.Str(allow_none = False, data_key = "tuid")
    latitude                = fields.Decimal(allow_none = False, as_string = True, data_key = "la")
    longitude               = fields.Decimal(allow_none = False, as_string = True, data_key = "lo")


class LoadedPoint():
    """A class to contain a single loaded point, irrespective of source."""
    @property
    def is_user_made(self):
        # Returns True if this point has User data.
        return self.logged_at != None and self.speed != None and self.rotation != None
    
    def __init__(self, **kwargs):
        self.latitude = kwargs.get("latitude")
        self.longitude = kwargs.get("longitude")
        self.logged_at = kwargs.get("logged_at", None)
        self.speed = kwargs.get("speed", None)
        self.rotation = kwargs.get("rotation", None)


class LoadPointSchema(Schema):
    """A schema for loading a basic point without any User data."""
    latitude                = fields.Decimal(as_string = True)
    longitude               = fields.Decimal(as_string = True)

    @post_load
    def make_loaded_point(self, data, **kwargs) -> LoadedPoint:
        return LoadedPoint(**data)


class LoadUserPointSchema(Schema):
    """A schema for loading a point with User data."""
    latitude                = fields.Decimal(as_string = True)
    longitude               = fields.Decimal(as_string = True)
    logged_at               = fields.Int(required = True, allow_none = False)
    speed                   = fields.Decimal(as_string = True, required = True, allow_none = False)
    rotation                = fields.Decimal(as_string = True, required = True, allow_none = False)

    @post_load
    def make_loaded_user_point(self, data, **kwargs) -> LoadedPoint:
        return LoadedPoint(**data)


class LoadedTrackSegment():
    """A class to contain a single loaded segment."""
    def __init__(self, **kwargs):
        self.points = kwargs.get("points")

    def get_linestring(self, transform_func):
        """Get this segment as a single Shapely LineString. This will expect all points involved are projected through EPSG 4326, and will transform these to the native
        coordinate reference system as part of a new LineString."""
        try:
            # For all points in this segment, construct a Shapely Point, then from that list, construct a Shapely LineString. Expect this in EPSG:4326.
            linestring_4326 = shapely.geometry.LineString([shapely.geometry.Point(pt.longitude, pt.latitude) for pt in self.points])
            # Convert this linestring from 4326 to the designated coordinate reference system and return that.
            return shapely.ops.transform(transform_func, linestring_4326)
        except Exception as e:
            raise e


class LoadTrackSegmentSchema(Schema):
    """A schema for loading a basic track segment; without User data."""
    points                  = fields.List(fields.Nested(LoadPointSchema, many = False))

    @post_load
    def make_loaded_track_segment(self, data, **kwargs) -> LoadedTrackSegment:
        return LoadedTrackSegment(**data)


class LoadUserTrackSegmentSchema(Schema):
    """A schema for loading a User track segment; with User data."""
    points                  = fields.List(fields.Nested(LoadUserPointSchema, many = False))

    @post_load
    def make_loaded_user_track_segment(self, data, **kwargs) -> LoadedTrackSegment:
        return LoadedTrackSegment(**data)


class LoadedTrack():
    """A class for containing a single loaded track."""
    @property
    def has_user_data(self):
        return False
    
    def __init__(self, **kwargs):
        self.name = kwargs.get("name")
        self.description = kwargs.get("description")
        self.segments = kwargs.get("segments")
        # We will now produce a track identity hash. The identity hash will be the track's name, and the coordinates from the very first point.
        hash_contents = (self.name + str(self.segments[0].points[0].latitude) + str(self.segments[0].points[0].longitude)).encode("utf-8")
        self.track_hash = hashlib.blake2b(hash_contents, digest_size = 32).hexdigest().lower()
        # Create a transformer in the class.
        self._transformer = pyproj.Transformer.from_crs(4326, config.WORLD_CONFIGURATION_CRS, always_xy = True)
        # Get the very first point, from the very first segment; this will become the start point.
        first_track_point = self.segments[0].points[0]
        # Create a Shapely point, in EPSG 4326, then transform that to the designated CRS.
        start_point = shapely.geometry.Point(first_track_point.longitude, first_track_point.latitude)
        self.start_point = shapely.ops.transform(self._transformer.transform, start_point)

    def get_multi_linestring(self):
        """Get a multilinestring representing the entire track. Each linestring itself represents a single track segment."""
        # Create a transform function to handle all coordinates.
        transform_func = self._transformer.transform
        # Now, for each segment, which is a LoadedTrackSegment, get the Polygon transformed via transform_func.
        return shapely.geometry.MultiLineString([segment.get_linestring(transform_func) for segment in self.segments])


class LoadedUserTrack(LoadedTrack):
    """A class for containing a single loaded User track."""
    @property
    def has_user_data(self):
        return True
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class LoadTrackSchema(Schema):
    """A schema for loading a basic track; without User data."""
    name                    = fields.Str()
    description             = fields.Str()
    segments                = fields.List(fields.Nested(LoadTrackSegmentSchema, many = False))

    @post_load
    def make_loaded_track(self, data, **kwargs) -> LoadedTrack:
        return LoadedTrack(**data)


class LoadUserTrackSchema(Schema):
    """A schema for loading a User track schema, with User data attached to it."""
    name                    = fields.Str()
    description             = fields.Str()
    segments                = fields.List(fields.Nested(LoadUserTrackSegmentSchema, many = False))

    @post_load
    def make_loaded_user_track(self, data, **kwargs) -> LoadedUserTrack:
        return LoadedUserTrack(**data)


class CreatedUserTrack():
    """A container for a created track, belonging to a User."""
    def __init__(self, user, new_track, track_path, **kwargs):
        self.user = user
        self.track = new_track
        self.track_path = track_path
    
    @classmethod
    def from_created_track(cls, user, created_track, **kwargs):
        """Build a created user track from the given User and created track instances."""
        return CreatedUserTrack(user, created_track.track, created_track.track_path)


def create_user_track_from_json(user, new_track_json, **kwargs) -> CreatedUserTrack:
    """Create a new Track given a JSON object, but verify the integrity of the track prior to creating it, since it was recorded by a User. This
    should be called when a User intends to create a new track, and in this case, providing a User is mandatory. This function will not check to
    ensure the User is allowed to create tracks, however, this function will verify some basics about the track's contents, to ensure it is not
    too short, too long or otherwise invalid in any way. The JSON should load as a LoadedUserTrack without issue.

    Arguments
    ---------
    :user: The User who recorded the track and to whom the track should be assigned.
    :new_track_json: The track, as a JSON object. This should load to a LoadedUserTrack correctly.

    Keyword arguments
    -----------------
    :should_validate: Should the data given by the new track be validated to ensure the User didn't intentionally screw anything up? Default is False.
    :is_verified: Should the created track be set verified? Default is False.
    :intersection_check: Whether to run the track intersection check at all. Default is True."""
    try:
        should_validate = kwargs.get("should_validate", False)
        is_verified = kwargs.get("is_verified", False)
        intersection_check = kwargs.get("intersection_check", True)

        # Construct a LoadTrackSchema and load the given JSON.
        load_user_track_schema = LoadUserTrackSchema()
        loaded_user_track = load_user_track_schema.load(new_track_json)
        # Now, if this loaded track reports it does not have User data, fail.
        if not loaded_user_track.has_user_data:
            raise NotImplementedError("Failed to create_user_track_from_json(), a non-User data track was passed- please use the create_track_from_json function.")
        # If validation is required, validate the track.
        if should_validate:
            _validate_loaded_user_track(loaded_user_track)
        # Finally, create the track, receiving back a created track object.
        created_track = create_track(loaded_user_track,
            is_verified = is_verified, intersection_check = intersection_check)
        # Now, associate the track with the given User above.
        created_track.set_owner(user)
        # Finally, create and return a created user track.
        return CreatedUserTrack.from_created_track(user, created_track)
    except TrackInspectionFailed as tif:
        raise tif
    except Exception as e:
        raise e
    

class CreatedTrack():
    """A container for a loaded track."""
    def __init__(self, track, track_path, **kwargs):
        self.track = track
        self.track_path = track_path
    
    def set_owner(self, user):
        """Set the Track's owner to the given User."""
        self.track.set_owner(user)


def create_track_from_json(new_track_json, **kwargs) -> CreatedTrack:
    """Create a new Track given a JSON object. This is an internal function from the point of view that no Users are considered or handled in the
    course of this process. The given JSON is assumed to be without any User data, and this function will fail if JSON is provided that has User
    data set at all.

    Arguments
    ---------
    :new_track_json: The track, as a JSON object. This should load to a LoadedTrack correctly, LoadedUserTrack instances are not acceptable.

    Keyword arguments
    -----------------
    :is_verified: Should the created track be set verified? Default is True since is the None-User route.
    :intersection_check: Whether to run the track intersection check at all. Default is True.
    
    Returns
    -------
    An instance of CreatedTrack, which contains the created track."""
    try:
        is_verified = kwargs.get("is_verified", True)
        intersection_check = kwargs.get("intersection_check", True)

        # Construct a LoadTrackSchema and load the given JSON.
        load_track_schema = LoadTrackSchema()
        loaded_track = load_track_schema.load(new_track_json)
        # Now, if this loaded track reports it has User data, fail.
        if loaded_track.has_user_data:
            raise NotImplementedError("Failed to create_track_from_json(), a User data track was passed- please use the User specific function.")
        # We can't properly validate this track, since there is no User data attached. Call out to create track with the loaded track and return its result.
        return create_track(loaded_track,
            is_verified = is_verified, intersection_check = intersection_check)
    except TrackInspectionFailed as tif:
        raise tif
    except Exception as e:
        raise e


def create_track_from_gpx(filename, **kwargs) -> CreatedTrack:
    """Create a Track from a GPX file. Provide the filename, as well as a directory relative to the working directory. The GPX contents will be read
    and parsed to produce a JSON object, which will then be used to produce a loaded track instance, which will then be passed to the create track
    function. All tracks loaded with this function will be loaded as non-User tracks.

    Arguments
    ---------
    :filename: The name (including extension) of the GPX file to create the new track from.

    Keyword arguments
    -----------------
    :relative_dir: A directory relative to the working directory. By default, the configured GPX_ROUTES_DIR.
    :is_verified: Whether this track is verified, that is, it does not need to be checked/pruned prior to use by Users. Default is True.
    :intersection_check: Whether to run the track intersection check at all. Default is True.
    
    Returns
    -------
    An instance of CreatedTrack."""
    try:
        relative_dir = kwargs.get("relative_dir", config.GPX_ROUTES_DIR)
        is_verified = kwargs.get("is_verified", True)
        intersection_check = kwargs.get("intersection_check", True)

        # Assemble the absolute path.
        gpx_absolute_path = os.path.join(os.getcwd(), relative_dir, filename)
        # If it does not exist, raise an error.
        if not os.path.isfile(gpx_absolute_path):
            """TODO: proper exception handling please."""
            raise NotImplementedError("create_track_from_gpx failed because GPX file not found.")
        # Read the contents of the file, and load a GPX instance.
        with open(gpx_absolute_path, "r") as f:
            gpx_file_contents = f.read()
            gpx = gpxpy.parse(gpx_file_contents)
        # Now, load the actual schema. We'll do this first by reading all GPX data and creating JSON compatible objects from it all.
        new_track_json = {
            "name": gpx.tracks[0].name,
            "description": gpx.tracks[0].description,
            "segments": [dict(points = [{
                    "latitude": track_point.latitude,
                    "longitude": track_point.longitude
                } for track_point in segment.points
            ]) for segment in gpx.tracks[0].segments]}
        # We'll now return the result of loading this JSON dictionary from JSON.
        return create_track_from_json(new_track_json,
            is_verified = is_verified, intersection_check = intersection_check)
    except Exception as e:
        raise e


def create_track(loaded_track, **kwargs) -> CreatedTrack:
    """Create a Track from the loaded track object. This function will check to see if an identical track already exists, and will fail if it does. Otherwise,
    a new Track will be created. Importantly, this function does not supply any validation functionality at all- this needs to be done in one of the abstract
    functions defined above.

    Arguments
    ---------
    :loaded_track: An instance of LoadedTrack, or a subtype thereof, which will be used to instantiate the Track.

    Keyword arguments
    -----------------
    :is_verified: Whether this track is verified, that is, it does not need to be checked/pruned prior to use by Users.
    :intersection_check: Whether to run the track intersection check at all. Default is True.
    
    Returns
    -------
    An instance of CreatedTrack, containing the track and its path."""
    try:
        is_verified = kwargs.get("is_verified", False)
        intersection_check = kwargs.get("intersection_check", True)

        # Search for the track's hash in all existing Tracks. If existing, raise an error.
        existing_track = find_existing_track(track_hash = loaded_track.track_hash)
        if existing_track:
            # Updating a track isn't currently supported, so we will simply fail.
            LOG.debug(f"Skipped importing track {loaded_track.name}, it is already imported.")
            raise TrackAlreadyExists()
        # Now track does not exist yet, we can instantiate a new one. First, instantiate a TrackPath, which will contain the track's geometry.
        track_path = models.TrackPath()
        # Set the path's CRS.
        track_path.set_crs(config.WORLD_CONFIGURATION_CRS)
        # Set the Path's geometry content, by first getting the multi linestring for the track.
        track_multi_linestring = loaded_track.get_multi_linestring()
        track_path.set_geometry(track_multi_linestring)
        if intersection_check:
            # Verify the track path does not intersect any existing path.
            _ensure_track_no_interference(track_path)
        # Create a new Track instance.
        new_track = models.Track(
            track_hash = loaded_track.track_hash)
        # Set all basic details on the track.
        new_track.set_name(loaded_track.name)
        new_track.set_description(loaded_track.description)
        new_track.set_verified(is_verified)
        new_track.set_path(track_path)
        # We will use the very first point from the very first segment to represent the start point. So set this as the position on the Track.
        start_point = loaded_track.start_point
        new_track.set_crs(config.WORLD_CONFIGURATION_CRS)
        new_track.set_position(start_point)
        # This process is now complete. Add the track and the track path to the database, and flush them.
        db.session.add_all([new_track, track_path])
        db.session.flush()
        # Return the created track result.
        return CreatedTrack(new_track, track_path)
    except Exception as e:
        raise e


def _validate_loaded_user_track(loaded_user_track, **kwargs):
    """Validate the given loaded track; that is, one that was created by a User and therefore all recording data (speed, rotation, times) were given. This function will
    succeed silently, or it will fail with an exception. Validating the track involves ensuring the data given by the User is properly structured as a track, this does
    not have anything to do with verify the track with Google API to snap to roads or whatever, this will be done programmatically, as long as track is created with
    verified set to False.

    Arguments
    ---------
    :loaded_user_track: An instance of LoadedUserTrack."""
    try:
        """
        TODO: some calculations here ensuring this track suits our requirements.
        Perhaps a check to refuse tracks in high population areas, where intersections or other pedestrian hotzones are detected ???
        """
        pass
    except Exception as e:
        raise e


def _ensure_track_no_interference(track_path, **kwargs):
    """Ensure the geometry represented by the (fully populated) TrackPath model does not intersect in any significant way with other existing tracks. At minimum, track start
    points (buffered) may not intersect any other track's start point. On success, this function will quietly succeed, on failure, an error will be raised.

    Arguments
    ---------
    :track_path: A fully populated TrackPath model."""
    try:
        # Get the very first point in this track path, then buffer it by the configured value of NUM_METERS_MIN_FOR_NEW_TRACK_START.
        start_point_buffered = track_path.start_point\
            .buffer(config.NUM_METERS_MIN_FOR_NEW_TRACK_START, cap_style = shapely.geometry.CAP_STYLE.round)
        # Now, perform a query for any Track whose point geometry is contained within the buffered start point.
        intersecting_tracks = db.session.query(models.Track)\
            .filter(func.ST_Contains(shape.from_shape(start_point_buffered, srid = config.WORLD_CONFIGURATION_CRS), models.Track.point_geom))\
            .all()
        # If there are any, fail for this reason.
        if len(intersecting_tracks) > 0:
            raise TrackInspectionFailed("start-point-too-close")
        # Silently succeed.
    except Exception as e:
        raise e
