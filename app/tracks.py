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
from sqlalchemy import func
from marshmallow import fields, Schema, post_load, EXCLUDE

from . import db, config, models, decorators

LOG = logging.getLogger("hawkspeed.tracks")
LOG.setLevel( logging.DEBUG )


class TrackPointSchema(Schema):
    """A response schema for a single track point."""
    track_uid               = fields.Str()
    latitude                = fields.Decimal(as_string = True, data_key = "la")
    longitude               = fields.Decimal(as_string = True, data_key = "lo")


class TrackSummarySchema(Schema):
    """A response schema for the minified track; this includes just details, maybe an overall rating but not the track's geometry."""
    uid                     = fields.Str()
    name                    = fields.Str()
    bio                     = fields.Str()
    start_point             = fields.Nested(TrackPointSchema, many = False)


class TrackSchema(TrackSummarySchema):
    """A response schema for the full track; this includes all details, ratings, a few comments and the entire track's geometry."""
    points                  = fields.List(fields.Nested(TrackPointSchema, many = False))


class LoadedPoint():
    """A class to contain a single loaded point, as received from a User who created a track."""
    def __init__(self, latitude, longitude, logged_at = None, speed = None, rotation = None):
        self.latitude = latitude
        self.longitude = longitude
        self.logged_at = logged_at
        self.speed = speed
        self.rotation = rotation


class LoadPointSchema(Schema):
    """A schema for loading a single point from a dictionary. On load, this will return a LoadedPoint instance."""
    latitude                = fields.Decimal(as_string = True)
    longitude               = fields.Decimal(as_string = True)
    # The following attributes are only required if we are NOT in testing mode.
    logged_at               = fields.DateTime(required = not config.TESTING, allow_none = config.TESTING)
    speed                   = fields.Decimal(as_string = True, required = not config.TESTING, allow_none = config.TESTING)
    rotation                = fields.Decimal(as_string = True, required = not config.TESTING, allow_none = config.TESTING)

    @post_load
    def make_loaded_point(self, data, **kwargs):
        return LoadedPoint(**data)


class LoadedTrackSegment():
    """A class to contain a single loaded segment."""
    def __init__(self, points):
        self.points = points

    def get_polygon(self, transform_func):
        """Get this segment as a single Shapely Polygon. This will expect all points involved are projected through EPSG 4326, and will transform these to the native
        coordinate reference system as part of a new Polygon."""
        # For all points in this segment, construct a Shapely Point, then from that list, construct a Shapely Polygon. Expect this in EPSG:4326.
        polygon_4326 = shapely.geometry.Polygon([shapely.geometry.Point(pt.longitude, pt.latitude) for pt in self.points])
        # Convert this polygon from 4326 to the designated coordinate reference system and return that.
        return shapely.ops.transform(transform_func, polygon_4326)


class LoadTrackSegmentSchema(Schema):
    """A schema for loading a single segment from a dictionary. On load, this will return a LoadedTrackSegment instance."""
    points                  = fields.List(fields.Nested(LoadPointSchema, many = False))

    @post_load
    def make_loaded_track_segment(self, data, **kwargs):
        return LoadedTrackSegment(**data)


class LoadedTrack():
    """A class for containing a single loaded track."""
    def __init__(self, name, description, segments):
        self.name = name
        self.description = description
        self.segments = segments
        # We will now produce a track identity hash. The identity hash will be the track's name, and the coordinates from the very first point.
        hash_contents = (name + str(self.segments[0].points[0].latitude) + str(self.segments[0].points[0].longitude)).encode("utf-8")
        self.track_hash = hashlib.blake2b(hash_contents, digest_size = 32).hexdigest().lower()
        # Create a transformer in the class.
        self._transformer = pyproj.Transformer.from_crs(4326, config.WORLD_CONFIGURATION_CRS, always_xy = True)
        # Get the very first point, from the very first segment; this will become the start point.
        first_track_point = segments[0].points[0]
        # Create a Shapely point, in EPSG 4326, then transform that to the designated CRS.
        start_point = shapely.geometry.Point(first_track_point.longitude, first_track_point.latitude)
        self.start_point = shapely.ops.transform(self._transformer.transform, start_point)

    def get_multi_polygon(self):
        """Get a multipolygon representing the entire track. Each polygon itself represents a single track segment."""
        # Create a transform function to handle all coordinates.
        transform_func = self._transformer.transform
        # Now, for each segment, which is a LoadedTrackSegment, get the Polygon transformed via transform_func.
        return shapely.geometry.MultiPolygon([segment.get_polygon(transform_func) for segment in self.segments])


class LoadTrackSchema(Schema):
    """A schema for loading an entire track from a dictionary. On load, this will return a LoadedTrack instance."""
    name                    = fields.Str()
    description             = fields.Str()
    segments                = fields.List(fields.Nested(LoadTrackSegmentSchema, many = False))

    @post_load
    def make_loaded_track(self, data, **kwargs):
        return LoadedTrack(**data)


def create_track_from_json(user, new_track_json, **kwargs):
    """Create a new Track given a JSON object. This should be called when a User intends to create a new track, and in this case, providing a User
    is mandatory. This function will not check to ensure the User is allowed to create tracks, however, this function will verify some basics about
    the track's contents, to ensure it is not too short, too long or otherwise invalid in any way.

    Arguments
    ---------
    :user: The User to whom the track creation should be attributed.
    :new_track_json: The track, as JSON. This should load to a LoadedTrack correctly.

    Keyword arguments
    -----------------
    :is_verified: Should the created track be verified? Default is False."""
    try:
        is_verified = kwargs.get("is_verified", False)

        # Begin by loading the JSON to a loaded track instance.
        load_track_schema = LoadTrackSchema()
        loaded_track = load_track_schema.load(new_track_json)
        """
        TODO: test track for anomalies, raise an error if any found.
        """
        # Call out to create track with the loaded track and the given User.
        return create_track(loaded_track,
            user = user, is_verified = is_verified)
    except Exception as e:
        raise e


@decorators.get_server_configuration()
def create_track_from_gpx(filename, server_configuration, **kwargs):
    """Create a Track from a GPX file. Provide the filename, as well as a directory relative to the working directory. The GPX contents will be read and parsed to produce
    a loaded track instance, which will then be passed to the create track function.

    Arguments
    ---------
    :filename: The name (including extension) of the GPX file to create the new track from.

    Keyword arguments
    -----------------
    :relative_dir: A directory relative to the working directory.
    :is_verified: Whether this track is verified, that is, it does not need to be checked/pruned prior to use by Users.
    :user: The User to whom this Track should be assigned as the owner. Optional, by default will use the HawkSpeed User."""
    try:
        relative_dir = kwargs.get("relative_dir", config.GPX_ROUTES_DIR)
        is_verified = kwargs.get("is_verified", True)
        user = kwargs.get("user", server_configuration.user)

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
        # Now, load this GPX object as a LoadedTrack instance above.
        loaded_track = LoadTrackSchema().load({
            "name": gpx.tracks[0].name,
            "description": gpx.tracks[0].description,
            "segments": [dict(points = [{
                    "latitude": track_point.latitude,
                    "longitude": track_point.longitude
                } for track_point in segment.points
            ]) for segment in gpx.tracks[0].segments]
        })
        # We can now create this track.
        return create_track(loaded_track,
            is_verified = is_verified, user = user)
    except Exception as e:
        raise e


@decorators.get_server_configuration()
def create_track(loaded_track, server_configuration, **kwargs):
    """Create a Track from the loaded track object. This function will check to see if an identical track already exists, and will fail if it does. Otherwise,
    a new Track will be created and added to the requested User. If no User is provided, the HawkSpeed User will be used.

    Arguments
    ---------
    :loaded_track: An instance of LoadedTrack, which will be used to instantiate the Track.

    Keyword arguments
    -----------------
    :user: The owner of the loaded track, by default, the HawkSpeed owner will be used.
    :is_verified: Whether this track is verified, that is, it does not need to be checked/pruned prior to use by Users."""
    try:
        user = kwargs.get("user", server_configuration.user)
        is_verified = kwargs.get("is_verified", False)

        # Search for the track's hash in all existing Tracks. If existing, raise an error.
        existing_track = models.Track.find(track_hash = loaded_track.track_hash)
        if existing_track:
            """TODO: handle this exception."""
            raise NotImplementedError("Failed to create track. This track already exists and the error is not handled.")
        # Now track does not exist yet, we can instantiate a new one. First, instantiate a TrackPath, which will contain the track's geometry.
        track_path = models.TrackPath()
        # Set the path's CRS.
        track_path.set_crs(config.WORLD_CONFIGURATION_CRS)
        # Set the Path's geometry content, by first getting the multi polygon for the track.
        track_multi_polygon = loaded_track.get_multi_polygon()
        track_path.set_geometry(track_multi_polygon)
        # Very the track path does not intersect any existing path.
        _ensure_track_no_intersections(track_path)
        # Create a new Track instance.
        new_track = models.Track(
            track_hash = loaded_track.track_hash,
            name = loaded_track.name,
            description = loaded_track.description,
            verified = is_verified,
            user = user)
        new_track.set_path(track_path)
        # We will use the very first point from the very first segment to represent the start point. So set this as the position on the Track.
        start_point = loaded_track.start_point
        new_track.set_crs(config.WORLD_CONFIGURATION_CRS)
        new_track.set_position(start_point)
        # This process is now complete. Add the track and the track path to the database, and flush them.
        db.session.add_all([new_track, track_path])
        db.session.flush()
        # Return the track.
        return new_track
    except Exception as e:
        raise e


def _ensure_track_no_intersections(track_path, **kwargs):
    """Ensure the geometry represented by the (fully populated) TrackPath model does not intersect with any other existing tracks at all. That is, no
    line from any track my cross, with a slight added buffer. On success, this function will quietly succeed, on failure, an error will be raised.

    Arguments
    ---------
    :track_path: A fully populated TrackPath model."""
    try:
        """TODO"""
        #raise NotImplementedError()
    except Exception as e:
        raise e
