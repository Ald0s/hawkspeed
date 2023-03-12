"""A module for handling world operations."""
import logging
import os
import gpxpy
import random
import hashlib

import pyproj
import geopandas
import shapely

from geoalchemy2 import shape
from sqlalchemy import func, desc

from datetime import datetime, date
from marshmallow import fields, Schema, post_load, EXCLUDE

from . import db, config, models, decorators, tracks, draw

LOG = logging.getLogger("hawkspeed.world")
LOG.setLevel( logging.DEBUG )

"""
TODO: find a more permanent solution for this.
It probably isn't a good idea to be instantiating one of these on EVERY player update, so we will create it once here for now.
"""
transformer = pyproj.Transformer.from_crs(4326, config.WORLD_CONFIGURATION_CRS, always_xy = True)


class BaseViewportUpdateSchema(Schema):
    """A schema that represents an update to the player's viewport. This can be thought of as a request for objects within a new scope."""
    class Meta:
        unknown = EXCLUDE
    # About the Player's initial viewport on the maps fragment.
    viewport_minx           = fields.Decimal(as_string = True, required = True)
    viewport_miny           = fields.Decimal(as_string = True, required = True)
    viewport_maxx           = fields.Decimal(as_string = True, required = True)
    viewport_maxy           = fields.Decimal(as_string = True, required = True)


class BasePlayerUpdateSchema(Schema):
    """A schema that represents a general update to the player's location (and viewport.) This is to be sent whenever the player intends to join the world, or every time
    the player's device emits a location update."""
    class Meta:
        unknown = EXCLUDE
    # About the Player's initial position when joining.
    latitude                = fields.Decimal(as_string = True, required = True)
    longitude               = fields.Decimal(as_string = True, required = True)
    rotation                = fields.Decimal(as_string = True, required = True)
    speed                   = fields.Decimal(as_string = True, required = True)
    # Timestamp, in seconds.
    logged_at               = fields.Int(required = True)


class PlayerJoinResult():
    """"""
    class PlayerJoinResultSchema(Schema):
        """"""
        class Meta:
            unknown = EXCLUDE
        uid                     = fields.Str(data_key = "player_uid")
        latitude                = fields.Decimal(as_string = True, required = True)
        longitude               = fields.Decimal(as_string = True, required = True)
        rotation                = fields.Decimal(as_string = True, required = True)
        # Now, all objects within the player's view.
        tracks                  = fields.List(fields.Nested(tracks.TrackSummarySchema, many = False))

    @property
    def uid(self):
        return self._user.uid

    @property
    def latitude(self):
        return self._user_location.latitude

    @property
    def longitude(self):
        return self._user_location.longitude

    @property
    def rotation(self):
        return self._user_location.rotation

    def serialise(self, **kwargs):
        schema = self.PlayerJoinResultSchema(**kwargs)
        return schema.dump(self)

    def __init__(self, user, user_location, viewed_objects):
        self._user = user
        self._user_location = user_location
        self._viewed_objects = viewed_objects


def parse_player_joined(user, connect_d, **kwargs) -> PlayerJoinResult:
    """Parse the locational information from the incoming connection request. Specifically, we must ensure the server is configured to support this location, we
    need to then convert this locational information into the appropriate CRS as per server configuration, and add it to location history for the User. This is
    where we will determine what objects the Player should see on first light.

    Arguments
    ---------
    :user: The User who has joined.
    :connect_d: A loaded instance of ConnectAuthenticationRequestSchema.

    Returns
    -------
    A PlayerJoinResult, which can be serialised to result in the Welcome message."""
    try:
        # Prepare a UserLocation from the given information.
        user_location = _prepare_user_location(connect_d)
        """TODO: some extra details here."""
        # Now, collect the objects in view for this User & their current viewport.
        world_objects_result = collect_viewed_objects(user, connect_d)
        # Add the user location to this User's history.
        user.add_location(user_location)
        # Trim the User's location history to ensure they retain just enough.
        _trim_player_location_history(user)
        # Create and return a player join result.
        return PlayerJoinResult(user, user_location)
    except Exception as e:
        """TODO: handle _ensure_location_supported raising exception."""
        raise e


class PlayerUpdateResult():
    """"""
    class PlayerUpdateResponseSchema(Schema):
        """"""
        uid                     = fields.Str(data_key = "player_uid")
        latitude                = fields.Decimal(as_string = True, required = True)
        longitude               = fields.Decimal(as_string = True, required = True)
        rotation                = fields.Decimal(as_string = True, required = True)
        # Now, all objects within the player's view.
        tracks                  = fields.List(fields.Nested(tracks.TrackSummarySchema, many = False))

    @property
    def uid(self):
        return self._user.uid

    @property
    def latitude(self):
        return self._user_location.latitude

    @property
    def longitude(self):
        return self._user_location.longitude

    @property
    def rotation(self):
        return self._user_location.rotation

    @property
    def user_location(self):
        return self._user_location

    def serialise(self, **kwargs):
        schema = self.PlayerUpdateResponseSchema(**kwargs)
        return schema.dump(self)

    def __init__(self, user, user_location, viewed_objects):
        self._user = user
        self._user_location = user_location
        self._viewed_objects = viewed_objects


def parse_player_update(user, player_update_d, **kwargs) -> PlayerUpdateResult:
    """Parse the locational information from the incoming player update request. Specifically, we must ensure the server is configured to support this location,
    we need to then convert this locational information into the appropriate CRS as per server configuration, and add it to location history for the User. This
    function will then determine what objects the Player should be seeing, and will return those.

    Arguments
    ---------
    :user: The User to update.
    :player_update_d: A loaded instance of PlayerUpdateRequestSchema.

    Returns
    -------
    A PlayerUpdateResult, which can be serialised to result in the update result."""
    try:
        # Prepare a UserLocation from the given information.
        user_location = _prepare_user_location(connect_d)
        """TODO: some extra details here."""
        # Add the user location to this User's history.
        user.add_location(user_location)
        # Flush user location so that it is given an ID.
        db.session.flush()
        # Trim the User's location history to ensure they retain just enough.
        _trim_player_location_history(user)
        # Create and return a player update result.
        return PlayerUpdateResult(user, user_location)
    except Exception as e:
        raise e


class Viewport():
    """An object that will represent a Player's current viewport, and will handle transforming it."""
    @property
    def polygon(self):
        """TODO: polygons generated from this property tend to be on an angle. Research this. Though, this is fixed if we manually add the other two corners, as alternative
        combinations of the existing two points."""
        return shapely.geometry.box(*self.min_pt.coords[0], *self.max_pt.coords[0])

    def __init__(self, _viewport_update_d, **kwargs):
        """This constructor requires a dictionary loaded with a schema that is a derivative of the BaseViewportUpdateSchema schema."""
        # Read all relevant values from the schema.
        # From these, construct two Shapely points, from the min and max points, that are also transformed appropriately.
        min_pt = shapely.geometry.Point(_viewport_update_d.get("viewport_minx"), _viewport_update_d.get("viewport_miny"))
        max_pt = shapely.geometry.Point(_viewport_update_d.get("viewport_maxx"), _viewport_update_d.get("viewport_maxy"))
        self.min_pt = shapely.ops.transform(transformer.transform, min_pt)
        self.max_pt = shapely.ops.transform(transformer.transform, max_pt)


class ViewedObjectsResult():
    @property
    def tracks(self):
        return self._tracks

    def __init__(self, tracks, **kwargs):
        self._tracks = tracks


def collect_viewed_objects(user, base_viewport_d, **kwargs) -> ViewedObjectsResult:
    """Given a User, and a dictionary that should have been loaded with a schema that is a derivative of the BaseViewportUpdateSchema schema, attempt to locate all
    world objects in view of that viewport, and report them in the given result.

    Arguments
    ---------
    :user: The User for whom to collect objects.
    :base_viewport_d: A dictionary that should have been loaded with a schema that is a derivative of BaseViewportUpdateSchema.

    Returns
    -------
    A ViewedObjectsResult, which is a summary report of those objects."""
    try:
        # Instante a new viewport object here.
        viewport = Viewport(base_viewport_d)
        viewport_polygon = viewport.polygon
        # Collect all tracks in view.
        tracks_in_view = db.session.query(models.Track)\
            .filter(func.ST_Contains(shape.from_shape(viewport_polygon, srid = config.WORLD_CONFIGURATION_CRS), models.Track.point_geom))\
            .all()
        # Create a viewed objects result and return it.
        return ViewedObjectsResult(tracks_in_view)
    except Exception as e:
        raise e


def _prepare_user_location(base_player_update_d, **kwargs) -> models.UserLocation:
    """Prepare a UserLocation instance from the given player update dictionary. This will not add the location to the session or associate it with any User, it will only
    process the inputs given by the dictionary and fill the required information, after transforming where applicable. The dictionary given must be a loaded with a derivative
    of the BasePlayerUpdateSchema defined at the top of this file.

    Arguments
    ---------
    :base_player_update_d: A dictionary loaded with a derivative of BasePlayerUpdateSchema.

    Returns
    -------
    A new UserLocation, filled out with transformed information."""
    try:
        longitude = base_player_update_d.get("longitude")
        latitude = base_player_update_d.get("latitude")
        logged_at = base_player_update_d.get("logged_at")
        speed = base_player_update_d.get("speed")
        rotation = base_player_update_d.get("rotation")
        # Build a Shapely point from the location.
        location_pt = shapely.geometry.Point(longitude, latitude)
        # Ensure we are able to support this location.
        _ensure_location_supported(location_pt)
        # Now, we will transform this location point to the localised CRS.
        location_pt = shapely.ops.transform(transformer.transform, location_pt)
        # With that done, instantiate a new user location and set basic information.
        user_location = models.UserLocation(
            longitude = longitude, latitude = latitude, logged_at = logged_at, rotation = rotation, speed = speed)
        # Set the location's CRS and position.
        user_location.set_crs(config.WORLD_CONFIGURATION_CRS)
        user_location.set_position(location_pt)
        # We are done, return the new location.
        return user_location
    except Exception as e:
        raise e


def prepare_user_location(base_player_update_d, **kwargs) -> models.UserLocation:
    return _prepare_user_location(base_player_update_d, **kwargs)


def _ensure_location_supported(point, crs = 4326, **kwargs):
    """Ensure the location in the given Shapely Point (XY format only,) is supported by how this server has been configured. If this is not the case, the function will
    raise an error. Otherwise, it will succeed quietly."""
    try:
        """TODO: potentially support other CRSs, for now, only 4326."""
        if crs != 4326:
            raise NotImplementedError("_ensure_location_supported does not support other CRS than 4326.")
        # CRS is the reference system in which Point is. The Point must be XY, by the way.
        crs = pyproj.CRS(config.WORLD_CONFIGURATION_CRS)
        # Get its area of use.
        crs_area_of_use = crs.area_of_use
        # Get its bounds.
        crs_bounds = crs_area_of_use.bounds
        # Now, construct a shapely Polygon from the bounds, and ensure the given point lands within it.
        bounds_polygon = shapely.geometry.box(*crs_bounds)
        if not bounds_polygon.contains(point):
            """TODO: raise an appropriate exception for location not supported."""
            raise NotImplementedError("_ensure_location_supported fails due to unsupported location - NOT HANDLED.")
    except Exception as e:
        raise e


def _trim_player_location_history(user, **kwargs):
    """Ensure the given user has at most NUM_PLAYER_UPDATES_RETAIN location updates stored, and delete the extras if there are any. This function will not attempt to delete
    any user locations that are currently attached to active or complete race instances."""
    try:
        # Get all UserLocation instances from the User, filtering out those where a UserLocationRace exists, and offset from NUM_PLAYER_UPDATES_RETAIN.
        extra_location_updates = user.location_history\
            .filter(models.UserLocation.track_user_race == None)\
            .order_by(desc(models.UserLocation.logged_at))\
            .offset(config.NUM_PLAYER_UPDATES_RETAIN)\
            .all()
        # If there are any, delete them all.
        if len(extra_location_updates) > 0:
            LOG.debug(f"Deleting {len(extra_location_updates)} location updates from {user}.")
        for _update in extra_location_updates:
            db.session.delete(_update)
    except Exception as e:
        raise e
