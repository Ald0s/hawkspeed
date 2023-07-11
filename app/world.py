"""A module for handling world operations."""
import logging
import random
import hashlib

import pyproj
import geopandas
import shapely

from geoalchemy2 import shape
from sqlalchemy import func, desc

from datetime import datetime, date
from marshmallow import fields, Schema, post_load, EXCLUDE

from . import db, error, config, models, draw

LOG = logging.getLogger("hawkspeed.world")
LOG.setLevel( logging.DEBUG )

"""
TODO: find a more permanent solution for this.
It probably isn't a good idea to be instantiating one of these on EVERY player update, so we will create it once here for now.
"""
transformer = pyproj.Transformer.from_crs(4326, config.WORLD_CONFIGURATION_CRS, always_xy = True)


class ParsePlayerJoinedError(Exception):
    """An exception that can be raised by the parse_player_joined function, indicating a failure to parse the join request."""
    REASON_POSITION_NOT_SUPPORTED = "position-not-supported"

    def __init__(self, reason_code):
        self.reason_code = reason_code


class ParsePlayerUpdateError(Exception):
    """An exception that can be raised by the parse_player_update function, indicating a failure to parse the update request."""
    REASON_POSITION_NOT_SUPPORTED = "position-not-supported"

    def __init__(self, reason_code):
        self.reason_code = reason_code


class CollectViewedObjectsError(Exception):
    """An exception that can be raised by the collect_viewed_objects function, indicating a failure to collect viewed objects."""
    REASON_VIEWPORT_NOT_SUPPORTED = "viewport-not-supported"

    def __init__(self, reason_code):
        self.reason_code = reason_code


class CollectNearbyObjectsError(Exception):
    """An exception that can be raised by the collect_nearby_objects function, indicating a failure to collect nearby objects."""
    REASON_POSITION_NOT_SUPPORTED = "position-not-supported"

    def __init__(self, reason_code):
        self.reason_code = reason_code


class PrepareUserLocationError(Exception):
    """An exception that communicates an issue with a value given to _prepare_user_location."""
    REASON_POSITION_NOT_SUPPORTED = "position-not-supported"
    REASON_NO_POSITION = "no-position"

    def __init__(self, request_player_update, reason_code, **kwargs):
        self.request_player_update = request_player_update
        self.reason_code = reason_code


class PositionNotSupportedError(Exception):
    """An exception that communicates a failure to accept a User's locational or viewport update/request on the basis that the
    given point or points are not supported by this server. Can be raised by _ensure_location_supported."""
    CODE_OUTSIDE_CRS = "outside-crs"
    CODE_BAD_CRS = "bad-crs"

    def __init__(self, point, crs, error_code, **kwargs):
        self.point = point
        self.crs = crs
        self.error_code = error_code


class RequestViewport():
    """An object that will represent a Player's current viewport."""
    def __init__(self, **kwargs):
        """Pass all arguments relevant to a base viewport through keyword arguments."""
        self.viewport_minx = kwargs.get("viewport_minx")
        self.viewport_miny = kwargs.get("viewport_miny")
        self.viewport_maxx = kwargs.get("viewport_maxx")
        self.viewport_maxy = kwargs.get("viewport_maxy")


class RequestViewportSchema(Schema):
    """A schema that represents an update to the player's viewport. This can be thought of as a request for objects within a new scope."""
    class Meta:
        unknown = EXCLUDE
    # About the Player's initial viewport on the maps fragment.
    viewport_minx           = fields.Decimal(as_string = True, required = True, allow_none = False)
    viewport_miny           = fields.Decimal(as_string = True, required = True, allow_none = False)
    viewport_maxx           = fields.Decimal(as_string = True, required = True, allow_none = False)
    viewport_maxy           = fields.Decimal(as_string = True, required = True, allow_none = False)

    @post_load
    def request_viewport_post_load(self, data, **kwargs) -> RequestViewport:
        return RequestViewport(**data)


class RequestPlayerUpdate():
    """A container for a player update request."""
    def __init__(self, **kwargs):
        """Call constructor with all arguments passed to keyword arguments."""
        self.latitude = kwargs.get("latitude")
        self.longitude = kwargs.get("longitude")
        self.bearing = kwargs.get("bearing")
        self.speed = kwargs.get("speed")
        self.logged_at = kwargs.get("logged_at")


class RequestPlayerUpdateSchema(Schema):
    """A schema that represents a general update to the player's location.  This is to be sent whenever the player intends to join the world, or every time
    the player's device emits a location update."""
    class Meta:
        unknown = EXCLUDE
    # About the Player's position.
    latitude                = fields.Decimal(as_string = True, required = True, allow_none = False)
    longitude               = fields.Decimal(as_string = True, required = True, allow_none = False)
    bearing                 = fields.Decimal(as_string = True, required = True, allow_none = False)
    speed                   = fields.Decimal(as_string = True, required = True, allow_none = False)
    # Timestamp, in milliseconds.
    logged_at               = fields.Int(required = True, allow_none = False)
    
    @post_load
    def request_player_update_post_load(self, data, **kwargs) -> RequestPlayerUpdate:
        return RequestPlayerUpdate(**data)


class RequestPlayerViewportUpdate():
    """A container for a loaded request for a full player update; that is both a viewport and location."""
    def __init__(self, **kwargs):
        self.player_update = kwargs.get("player_update")
        self.viewport = kwargs.get("viewport")


class RequestPlayerViewportUpdateSchema(Schema):
    """A subtype of the player update, specifically for the Player's communicating their location and viewport."""
    class Meta:
        unknown = EXCLUDE
    player_update           = fields.Nested(RequestPlayerUpdateSchema, many = False)
    viewport                = fields.Nested(RequestViewportSchema, many = False)

    @post_load
    def request_player_update_post_load(self, data, **kwargs) -> RequestPlayerViewportUpdate:
        return RequestPlayerViewportUpdate(**data)


class RequestConnectAuthentication():
    """A container for a loaded request for a player update."""
    def __init__(self, **kwargs):
        self.device_fid = kwargs.get("device_fid")
        self.latitude = kwargs.get("latitude")
        self.longitude = kwargs.get("longitude")
        self.bearing = kwargs.get("bearing")
        self.speed = kwargs.get("speed")
        self.logged_at = kwargs.get("logged_at")


class RequestConnectAuthenticationSchema(Schema):
    """A subtype of the player update, specifically for the Player's initial report upon connection."""
    class Meta:
        unknown = EXCLUDE
    # About the Player's device when joining.
    device_fid              = fields.Str(required = True, allow_none = False)
    # About the Player's initial position when joining.
    latitude                = fields.Decimal(as_string = True, required = True, allow_none = False)
    longitude               = fields.Decimal(as_string = True, required = True, allow_none = False)
    bearing                 = fields.Decimal(as_string = True, required = True, allow_none = False)
    speed                   = fields.Decimal(as_string = True, required = True, allow_none = False)
    # Timestamp, in seconds.
    logged_at               = fields.Int(required = True, allow_none = False)

    @post_load
    def request_connect_auth_post_load(self, data, **kwargs) -> RequestConnectAuthentication:
        return RequestConnectAuthentication(**data)


class RequestViewportUpdate():
    """A container for a loaded request for a viewport update."""
    def __init__(self, **kwargs):
        """"""
        self.viewport = kwargs.get("viewport")


class RequestViewportUpdateSchema(Schema):
    """A subtype of the viewport update, specifically for when the Player wishes to update their view."""
    class Meta:
        unknown = EXCLUDE
    viewport                = fields.Nested(RequestViewportSchema, many = False)

    @post_load
    def request_viewport_update_post_load(self, data, **kwargs) -> RequestViewportUpdate:
        return RequestViewportUpdate(**data)


def create_player_session(user, socket_id, request_connect_authentication) -> models.UserPlayer:
    """Create and return a new UserPlayer session for the given User, based on the request to connect and authenticate for the world. This function will not
    explicitly add the Player to the session, but this may be done automatically by back propagating based on the keys. That said, latest SQLAlchemy should
    still avoid add operation since player is not explicitly added.
    
    Arguments
    ---------
    :user: The User to create the new session for.
    :socket_id: The current Socket's SID.
    :request_connect_authentication: An instance of RequestConnectAuthentication.
    
    Returns
    -------
    The new UserPlayer instance."""
    try:
        # Instantiate a new UserPlayer.
        new_player = models.UserPlayer()
        # Set the key for this Player.
        new_player.set_key(user, request_connect_authentication.device_fid, socket_id)
        """TODO: here is where we can set current Vehicle, if Vehicle is required for the duration of the session."""
        # Return the new Player.
        return new_player
    except Exception as e:
        raise e
    

class ViewportUpdateResult():
    """A container for the Player's update for their viewport being successfully processed."""
    @property
    def tracks(self):
        return self._tracks

    def __init__(self, tracks, **kwargs):
        self._tracks = tracks


def collect_viewed_objects(user, request_viewport, **kwargs) -> ViewportUpdateResult:
    """Given a User, and a dictionary that should have been loaded with a schema that is a derivative of the BaseViewportUpdateSchema schema, attempt to locate all
    world objects in view of that viewport, and report them in the given result.

    Arguments
    ---------
    :user: The User for whom to collect objects.
    :request_viewport: An instance of RequestViewport which contains all details for a new viewport. A dictionary will also be accepted (and loaded.)
    :transformer: A pyproj transformer to use in the transformation of the viewport's coordinates. If not given, one will be generated.

    Returns
    -------
    A ViewportUpdateResult, which is a summary report of those objects."""
    try:
        transformer_ = kwargs.get("transformer", None)
        if isinstance(request_viewport, dict):
            request_viewport_schema = RequestViewportSchema()
            request_viewport = request_viewport_schema.load(request_viewport)
        if not transformer_:
            """TODO: get a proper transformer here, but for now we'll use public one."""
            transformer_ = transformer
        # Build and transform a point a point for the minimum and maximum coordinates in this bbox, by the desired transformer.
        min_pt = shapely.ops.transform(transformer_.transform, shapely.geometry.Point(request_viewport.viewport_minx, request_viewport.viewport_miny))
        max_pt = shapely.ops.transform(transformer_.transform, shapely.geometry.Point(request_viewport.viewport_maxx, request_viewport.viewport_maxy))
        # Produce a box-like Polygon from these points, this is the bounding box.
        """TODO: polygons generated from this property tend to be on an angle. Research this. Though, this is fixed if we manually add the other two corners, as alternative
        combinations of the existing two points."""
        viewport_polygon = shapely.geometry.box(*min_pt.coords[0], *max_pt.coords[0])
        # Collect all tracks in view. Keep in mind, all geometries here are in the designated CRS.
        tracks_in_view = db.session.query(models.Track)\
            .filter(func.ST_Contains(shape.from_shape(viewport_polygon, srid = config.WORLD_CONFIGURATION_CRS), models.Track.point_geom))\
            .all()
        # Create a viewed objects result and return it.
        return ViewportUpdateResult(tracks_in_view)
    except Exception as e:
        raise e


class WorldObjectUpdateResult():
    """A container for the Player's update for all world objects within proximity being successfully updated."""
    @property
    def tracks(self):
        return self._tracks

    def __init__(self, tracks, **kwargs):
        self._tracks = tracks


def collect_nearby_objects(user, user_location, **kwargs) -> WorldObjectUpdateResult:
    """Given a User, and an instance of UserLocation, query all objects within acceptable proximity to the User's location. We will provide this function
    by default because we require objects near the Player to be updated, in the case that the Player moves their viewport elsewhere.

    Arguments
    ---------
    :user: The User for whom to collect objects.
    :user_location: An instance of UserLocation which contains the result of the latest user location update.

    Returns
    -------
    A WorldObjectUpdateResult, which is a summary report of those objects."""
    try:
        # Use the point set on the given User location, and buffer that by the proximity configured in settings.
        proximity_polygon = user_location.point.buffer(config.NUM_METERS_PLAYER_PROXIMITY,
            cap_style = shapely.geometry.CAP_STYLE.round)
        # With the proximity polygon, we can perform a request for all world objects in that geometry.
        tracks_in_proximity = db.session.query(models.Track)\
            .filter(func.ST_Contains(shape.from_shape(proximity_polygon, srid = config.WORLD_CONFIGURATION_CRS), models.Track.point_geom))\
            .all()
        # Create a world object update result and return it.
        return WorldObjectUpdateResult(tracks_in_proximity)
    except Exception as e:
        raise e
    

class PlayerJoinResult():
    """A container for the Player's join request being successfully processed."""
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
    def bearing(self):
        return self._user_location.bearing
    
    @property
    def crs(self):
        return self._user_location.crs

    @property
    def position(self):
        return self._user_location.position
    
    @property
    def world_object_update(self):
        return self._world_object_result

    def __init__(self, user, user_location, world_object_result = None):
        self._user = user
        self._user_location = user_location
        self._world_object_result = world_object_result


def parse_player_joined(user, request_connect_authentication, **kwargs) -> PlayerJoinResult:
    """Parse the locational information from the incoming connection request. Specifically, we must ensure the server is configured to support this location, we
    need to then convert this locational information into the appropriate CRS as per server configuration, and add it to location history for the User. This is
    where we will determine what objects the Player should see on first light.

    Arguments
    ---------
    :user: The User who has joined.
    :request_connect_authentication: An instance of RequestConnectAuthentication.

    Returns
    -------
    A PlayerJoinResult, which can be serialised to result in the Welcome message."""
    try:
        try:
            # Prepare a UserLocation from the given information.
            user_location = _prepare_user_location(request_connect_authentication)
        except PrepareUserLocationError as pule:
            # Failed to prepare user location. Determine why, then raise a PlayerJoinedError based on the outcome.
            raise ParsePlayerJoinedError(ParsePlayerJoinedError.REASON_POSITION_NOT_SUPPORTED)
        try:
            # Attempt to collect nearby objects on the basis of the newly created user location. This may fail, which is OK.
            world_object_result = collect_nearby_objects(user, user_location)
        except CollectNearbyObjectsError as cnoe:
            # Set our world object result to None and pass through.
            world_object_result = None
        # Add the user location to this User's history.
        user.add_location(user_location)
        # Update this user's last location received.
        user.set_last_location_update()
        # Flush session so user location is given an ID.
        db.session.flush()
        # Trim the User's location history to ensure they retain just enough.
        _trim_player_location_history(user)
        # Create and return a player join result.
        return PlayerJoinResult(user, user_location, world_object_result)
    except Exception as e:
        raise e


class PlayerUpdateResult():
    """A container for the Player's update result being successfully processed."""
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
    def bearing(self):
        return self._user_location.bearing
    
    @property
    def crs(self):
        return self._user_location.crs

    @property
    def position(self):
        return self._user_location.position

    @property
    def world_object_update(self):
        return self._world_object_result

    @property
    def user_location(self):
        return self._user_location

    def __init__(self, user, user_location, world_object_result = None):
        self._user = user
        self._user_location = user_location
        self._world_object_result = world_object_result


def parse_player_update(user, request_player_update, **kwargs) -> PlayerUpdateResult:
    """Parse the locational information from the incoming player update request. Specifically, we must ensure the server is configured to support this location,
    we need to then convert this locational information into the appropriate CRS as per server configuration, and add it to location history for the User. This
    function will then determine what objects the Player should be seeing, relative to their locational position, and will return those.

    Arguments
    ---------
    :user: The User to update.
    :request_player_update: An instance of RequestPlayerUpdate.

    Returns
    -------
    A PlayerUpdateResult, which can be serialised to result in the update result."""
    try:
        # Prepare a UserLocation from the given information.
        try:
            user_location = _prepare_user_location(request_player_update)
        except PrepareUserLocationError as pule:
            # Failed to prepare user location. Determine why, then raise a ParsePlayerUpdateError based on the outcome.
            raise ParsePlayerUpdateError(ParsePlayerUpdateError.REASON_POSITION_NOT_SUPPORTED)
        try:
            # Attempt to collect nearby objects on the basis of the newly created user location. This may fail, which is OK.
            world_object_result = collect_nearby_objects(user, user_location)
        except CollectNearbyObjectsError as cnoe:
            # Set our world object result to None and pass through.
            world_object_result = None
        # Add the user location to this User's history.
        user.add_location(user_location)
        # Update this user's last location received.
        user.set_last_location_update()
        # Now, if the User currently has a Player, we'll update that Player's position.
        if user.has_player:
            user.player.set_crs(user_location.crs)
            user.player.set_position(user_location.position)
        # Flush user location so that it is given an ID.
        db.session.flush()
        # Trim the User's location history to ensure they retain just enough.
        _trim_player_location_history(user)
        # Create and return a player update result.
        return PlayerUpdateResult(user, user_location, world_object_result)
    except Exception as e:
        raise e


def _prepare_user_location(request_player_update, **kwargs) -> models.UserLocation:
    """Prepare a UserLocation instance from the given player update dictionary. This will not add the location to the session or associate it with any User, it will only
    process the inputs given by the dictionary and fill the required information, after transforming where applicable. The dictionary given must be a loaded with a derivative
    of the BasePlayerUpdateSchema defined at the top of this file.

    Arguments
    ---------
    :request_player_update: An instance of RequestPlayerUpdate.

    Returns
    -------
    A new UserLocation, filled out with transformed information."""
    try:
        # Build a Shapely point from the location.
        location_pt = shapely.geometry.Point(request_player_update.longitude, request_player_update.latitude)
        # Ensure we are able to support this location.
        _ensure_location_supported(location_pt)
        # Now, we will transform this location point to the localised CRS.
        location_pt = shapely.ops.transform(transformer.transform, location_pt)
        # With that done, instantiate a new user location and set basic information.
        user_location = models.UserLocation(
            longitude = request_player_update.longitude, latitude = request_player_update.latitude, logged_at = request_player_update.logged_at, bearing = request_player_update.bearing, speed = request_player_update.speed)
        # Set the location's CRS and position.
        user_location.set_crs(config.WORLD_CONFIGURATION_CRS)
        user_location.set_position(location_pt)
        # We are done, return the new location.
        return user_location
    except PositionNotSupportedError as pnse:
        if pnse.error_code == PositionNotSupportedError.CODE_OUTSIDE_CRS or pnse.error_code == PositionNotSupportedError.CODE_BAD_CRS:
            raise PrepareUserLocationError(request_player_update, PrepareUserLocationError.REASON_POSITION_NOT_SUPPORTED)
        else:
            raise NotImplementedError(f"Failed to _prepare_user_location, unknown position not supported error: {pnse.error_code}")
    except Exception as e:
        raise e


def prepare_user_location(request_player_update, **kwargs) -> models.UserLocation:
    try:
        if(isinstance(request_player_update, dict)):
            request_player_update_schema = RequestPlayerUpdateSchema()
            request_player_update = request_player_update_schema.load(request_player_update)
        return _prepare_user_location(request_player_update, **kwargs)
    except Exception as e:
        raise e
    

def _ensure_location_supported(point, crs = 4326, **kwargs):
    """Ensure the location in the given Shapely Point (XY format only,) is supported by how this server has been configured. If this is not the case, the function will
    raise an error. Otherwise, it will succeed quietly."""
    try:
        if crs != 4326:
            # Raise a PositionNotSupportedError, because input CRS must be 4326 for now.
            raise PositionNotSupportedError(point, crs, PositionNotSupportedError.CODE_BAD_CRS)
        # CRS is the reference system in which Point is. The Point must be XY, by the way.
        crs = pyproj.CRS(config.WORLD_CONFIGURATION_CRS)
        # Get its area of use.
        crs_area_of_use = crs.area_of_use
        # Get its bounds.
        crs_bounds = crs_area_of_use.bounds
        # Now, construct a shapely Polygon from the bounds, and ensure the given point lands within it.
        bounds_polygon = shapely.geometry.box(*crs_bounds)
        if not bounds_polygon.contains(point):
            # Raise a PositionNotSupportedError because the given point falls outside our supported CRS.
            raise PositionNotSupportedError(point, crs, PositionNotSupportedError.CODE_OUTSIDE_CRS)
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
