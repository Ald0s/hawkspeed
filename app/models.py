import re
import os
import time
import uuid
import random
import logging
import pytz
import string
import json
import binascii
import hashlib
from datetime import datetime, date, timedelta, timezone

# All imports for geospatial aspect.
import pyproj
from shapely import geometry, wkb, ops
from geoalchemy2 import Geometry, shape

from flask_login import AnonymousUserMixin, UserMixin, current_user
from flask import request, g
from sqlalchemy import asc, desc, or_, and_, func, select, case
from sqlalchemy.orm import relationship, aliased, with_polymorphic, declared_attr
from sqlalchemy.sql.expression import cast
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.hybrid import hybrid_property, hybrid_method
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.event import listens_for
from sqlite3 import IntegrityError as SQLLite3IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from marshmallow import Schema, fields, EXCLUDE, post_load

from . import db, config, login_manager, error, compat

LOG = logging.getLogger("hawkspeed.models")
LOG.setLevel( logging.DEBUG )


class EPSGWrapperMixin():
    """Mixin for enabling geodetic transformation on objects."""
    @property
    def crs_object(self):
        if not self.crs:
            return None
        return pyproj.crs.CRS.from_user_input(self.crs)

    @property
    def geodetic_transformer(self):
        return pyproj.Transformer.from_crs(self.crs_object, self.crs_object.geodetic_crs, always_xy = True)

    @declared_attr
    def crs(cls):
        return db.Column(db.Integer, nullable = True, default = None)

    def set_crs(self, crs):
        self.crs = crs


class PointGeometryMixin(EPSGWrapperMixin):
    """Enables all subclasses to own a Point; perhaps as a center for example."""
    @property
    def is_position_valid(self):
        return self.point_geom != None

    @declared_attr
    def point_geom(self):
        """Represents a column for a geometry of type Point that defines the center/position of this object."""
        return db.Column(Geometry("POINT", srid = config.WORLD_CONFIGURATION_CRS, management = config.POSTGIS_MANAGEMENT))

    @property
    def point(self) -> geometry.Point:
        """Return a XY format Point for this object's longitude & latitude."""
        if not self.point_geom:
            return None
        return shape.to_shape(self.point_geom)

    @point.setter
    def point(self, value):
        if not value:
            self.point_geom = None
        else:
            if not self.crs:
                raise NotImplementedError("No CRS set! We can't set this point geom, not properly handled.")
            self.point_geom = shape.from_shape(value, srid = self.crs)

    @property
    def position(self):
        return self.point

    @property
    def geodetic_point(self):
        if not self.point_geom:
            return None
        return ops.transform(self.geodetic_transformer.transform, self.point)

    def set_position(self, point):
        if not self.crs:
            raise AttributeError(f"Could not set position for {self}, this object does not have a CRS set!")
        if isinstance(point, tuple):
            point = geometry.Point(point)
        self.point = point

    def clear_position(self):
        """This clears both the Point geometry and CRS currently set."""
        self.point = None
        self.crs = None


class PolygonGeometryMixin(EPSGWrapperMixin):
    """Enables all subclasses to own an arbitrary Polygon geometry, such as a Suburb."""
    @declared_attr
    def polygon_geom(self):
        """Represents a column for a geometry of type Polygon"""
        return db.Column(Geometry("POLYGON", srid = config.WORLD_CONFIGURATION_CRS, management = config.POSTGIS_MANAGEMENT))

    @property
    def polygon(self) -> geometry.Polygon:
        if not self.polygon_geom:
            return None
        return shape.to_shape(self.polygon_geom)

    @polygon.setter
    def polygon(self, value):
        if not value:
            self.polygon_geom = None
        else:
            if not self.crs:
                raise Exception("No CRS set! We can't set this polygon geom.")
            self.polygon_geom = shape.from_shape(value, srid = self.crs)

    @property
    def geodetic_polygon(self):
        return ops.transform(self.geodetic_transformer.transform, self.polygon)

    def set_geometry(self, polygon):
        if not self.crs:
            raise AttributeError(f"Could not set geometry for {self}, this object does not have a CRS set!")
        self.polygon = polygon


class MultiPolygonGeometryMixin(EPSGWrapperMixin):
    """Enables subclasses to own a multipolygon geometry, such as a State or a Suburb that is for some reason two separate regions."""
    @declared_attr
    def multi_polygon_geom(self):
        """Represents a column for a geometry of type MultiPolygon"""
        return db.Column(Geometry("MULTIPOLYGON", srid = config.WORLD_CONFIGURATION_CRS, management = config.POSTGIS_MANAGEMENT))

    @property
    def multi_polygon(self) -> geometry.MultiPolygon:
        if not self.multi_polygon_geom:
            return None
        return shape.to_shape(self.multi_polygon_geom)

    @multi_polygon.setter
    def multi_polygon(self, value):
        if not value:
            self.multi_polygon_geom = None
        else:
            if not self.crs:
                raise NotImplementedError("No CRS set! We can't set this multipolygon geom.")
            self.multi_polygon_geom = shape.from_shape(value, srid = self.crs)

    @property
    def geodetic_multi_polygon(self):
        return ops.transform(self.geodetic_transformer.transform, self.multi_polygon)

    def set_geometry(self, multi_polygon):
        if not self.crs:
            raise AttributeError(f"Could not set geometry for {self}, this object does not have a CRS set!")
        self.multi_polygon = multi_polygon


class LineStringGeometryMixin(EPSGWrapperMixin):
    """Enables all subclasses to own an arbitrary LineString geometry."""
    @declared_attr
    def linestring_geom(self):
        """Represents a column for a geometry of type LineString."""
        return db.Column(Geometry("LINESTRING", srid = config.WORLD_CONFIGURATION_CRS, management = config.POSTGIS_MANAGEMENT))

    @property
    def linestring(self) -> geometry.LineString:
        if not self.linestring_geom:
            return None
        return shape.to_shape(self.linestring_geom)

    @linestring.setter
    def linestring(self, value):
        if not value:
            self.linestring_geom = None
        else:
            if not self.crs:
                raise Exception("No CRS set! We can't set this linestring geom.")
            self.linestring_geom = shape.from_shape(value, srid = self.crs)

    @property
    def geodetic_linestring(self):
        return ops.transform(self.geodetic_transformer.transform, self.linestring)

    def set_geometry(self, linestring):
        if not self.crs:
            raise AttributeError(f"Could not set geometry for {self}, this object does not have a CRS set!")
        self.linestring = linestring


class MultiLineStringGeometryMixin(EPSGWrapperMixin):
    """Enables subclasses to own a MultiLineString geometry."""
    @declared_attr
    def multi_linestring_geom(self):
        """Represents a column for a geometry of type MultiLineString."""
        return db.Column(Geometry("MULTILINESTRING", srid = config.WORLD_CONFIGURATION_CRS, management = config.POSTGIS_MANAGEMENT))

    @property
    def multi_linestring(self) -> geometry.MultiLineString:
        if not self.multi_linestring_geom:
            return None
        return shape.to_shape(self.multi_linestring_geom)

    @multi_linestring.setter
    def multi_linestring(self, value):
        if not value:
            self.multi_linestring_geom = None
        else:
            if not self.crs:
                raise NotImplementedError("No CRS set! We can't set this multilinestring geom.")
            self.multi_linestring_geom = shape.from_shape(value, srid = self.crs)

    @property
    def geodetic_multi_linestring(self):
        return ops.transform(self.geodetic_transformer.transform, self.multi_linestring)

    def set_geometry(self, multi_linestring):
        if not self.crs:
            raise AttributeError(f"Could not set geometry for {self}, this object does not have a CRS set!")
        self.multi_linestring = multi_linestring


class TrackUserRace(db.Model, LineStringGeometryMixin):
    """An association object between the Track model and the User model that represents a race completed by the User. This is a model that is created as soon as the User
    engages in a Race, and will enter the complete state once the server is happy the track was actually raced. Alternatively, if the race is determined to be interrupted,
    the fledgling association will be deleted. There can only be one incomplete race for one User at any time. Starting a new race will delete any currently incomplete races
    that are not already deleted.

    This model also represents a single line string, which is a geometry of the User's progress through the track."""
    __tablename__ = "track_user_race"

    track_id                = db.Column(db.Integer, db.ForeignKey("track.id", ondelete = "CASCADE"), primary_key = True, nullable = False)
    user_id                 = db.Column(db.Integer, db.ForeignKey("user_.id", ondelete = "CASCADE"), primary_key = True, nullable = False)
    uid                     = db.Column(db.String(65), unique = True, default = lambda: uuid.uuid4().hex.lower(), primary_key = True, nullable = False)

    # When this race was started; in milliseconds. This is said to be when the User's client communicates their intent to begin the race. Can't be None.
    started                 = db.Column(db.BigInteger, nullable = False, default = lambda: time.time() * 1000)
    # When this race was completed; in milliseconds. This is said to be when the server determines the race was completed successfully.
    finished                = db.Column(db.BigInteger, nullable = True, default = None)
    # If this race was disqualified. Can't be None.
    disqualified            = db.Column(db.Boolean, nullable = False, default = False)
    # The reason for this race's disqualification. Can be None.
    dq_reason               = db.Column(db.String(32), nullable = True, default = None)
    # Extra info for the disqualification; this is JSON stored as a string. Can be None.
    dq_extra_info_          = db.Column(db.Text, nullable = True, default = None)
    # If this race has been cancelled. Can't be None.
    cancelled               = db.Column(db.Boolean, nullable = False, default = False)
    # Average speed, in meters per hour.
    average_speed           = db.Column(db.Integer, nullable = True, default = None)
    # The time taken thus far, in milliseconds.
    stopwatch               = db.Column(db.Integer, nullable = True, default = None)

    # An association proxy for the Track's UID.
    track_uid               = association_proxy("track", "uid")

    # All UserLocation instances logged by the User at the time this track was being raced. This is an eager relationship, and the referred UserLocation objects can only
    # be deleted if the User or the TrackUserRace instances are deleted. The race progress is loaded in order from start to finish (ascending.)
    progress                = db.relationship(
        "UserLocation",
        back_populates = "track_user_race",
        uselist = True,
        order_by = "asc(UserLocation.logged_at)",
        secondary = "user_location_race")
    # The Track being raced.
    track                   = db.relationship(
        "Track",
        back_populates = "races_",
        uselist = False)
    # The User doing the racing.
    user                    = db.relationship(
        "User",
        back_populates = "races_",
        uselist = False)

    def __repr__(self):
        return f"TrackUserRace<{self.track},{self.user},o={self.is_ongoing}>"

    @hybrid_property
    def is_ongoing(self):
        """Returns True when finished is None, disqualified is False and cancelled is False."""
        return self.finished == None and self.disqualified == False and self.cancelled == False

    @is_ongoing.expression
    def is_ongoing(cls):
        """Expression level is ongoing."""
        return and_(cls.finished == None, and_(cls.disqualified == False, cls.cancelled == False))

    @property
    def is_finished(self):
        """Return True if the race is finished. That is, the finished flag is True, and the race is NOT ongoing."""
        return self.finished != None and not self.is_ongoing

    @property
    def is_disqualified(self):
        """Returns True if the race has been disqualified."""
        return self.disqualified

    @property
    def is_cancelled(self):
        """Returns True if the race has been cancelled."""
        return self.cancelled

    @property
    def has_progress(self):
        """Returns True if there are at least 2 or more points attached, meaning a valid progress geometry can be set."""
        return len(self.progress) > 1

    @property
    def dq_extra_info(self):
        """TODO: If not None, parse dq_extra_info_ as JSON and return."""
        raise NotImplementedError()

    @dq_extra_info.setter
    def dq_extra_info(self, value):
        """TODO: If value None, set dq_extra_info_ to None. Else, dump value as JSON string and set dq_extra_info_."""
        raise NotImplementedError()

    def set_track_and_user(self, track, user):
        """Setting the track and user."""
        self.track = track
        self.user = user

    def set_finished(self, time_finished):
        """Set this race to finished. This supplied timestamp must be in milliseconds."""
        # Set the timestamp we finished at.
        self.finished = time_finished
        # Update the stopwatch with this time.
        self.update_stopwatch(time_finished)

    def disqualify(self, dq_reason, **kwargs):
        """A one way function. This will set the disqualified flag to True, and the accompanying arguments."""
        dq_extra_info = kwargs.get("dq_extra_info", None)
        self.disqualified = True
        self.disqualification_reason = dq_reason
        if dq_extra_info:
            self.dq_extra_info = dq_extra_info

    def cancel(self):
        """A one way function. This will set the cancelled flag to True."""
        self.cancelled = True

    def set_average_speed(self, average_speed):
        """Set this race's average speed."""
        self.average_speed = average_speed

    def add_location(self, location):
        """Add the location as progress."""
        # Add the new location.
        self.progress.append(location)
        # With each new accepted location, update the stopwatch too.
        self.update_stopwatch(location.logged_at)
        # Each time we add a new location, we must re-comprehend the progress geometry.
        self._refresh_progress_geometry()

    def update_stopwatch(self, new_timestamp_ms):
        """Update the stopwatch attribute with the latest timestamp. The given timestamp should be in milliseconds."""
        # Stopwatch is the difference between the new timestamp and the timestamp at which we started the race.
        new_stopwatch = new_timestamp_ms-self.started
        if new_stopwatch < 0:
            """TODO: remove after we are sure this is never triggered."""
            raise NotImplementedError()
        self.stopwatch = new_stopwatch

    def _refresh_progress_geometry(self):
        """Get all geometries from the list of progress locations, and set the race's progress geometry to the result."""
        # Do not perform this refresh if the race does not yet have sufficient progress.
        if not self.has_progress:
            return
        # We will comprehend a list of Shapely Points, where each represents the position from each UserLocation instance stored in progress.
        progress_points = [ul.point.coords[0] for ul in self.progress]
        # With this list of Point geometries, we'll instance a new LineString.
        progress_geometry = geometry.LineString(progress_points)
        # Set this model's geometry.
        self.set_geometry(progress_geometry)


class TrackPath(db.Model, MultiLineStringGeometryMixin):
    """A model specifically for storing the path for a recorded track, as a MultiLineString type geometry. Each LineString is a single segment of the overall track.
    This is associated with at most one Track instance."""
    __tablename__ = "track_path"

    id                      = db.Column(db.Integer, primary_key = True)
    track_id                = db.Column(db.Integer, db.ForeignKey("track.id", ondelete = "CASCADE"))

    # The track this path belongs to. Can't be None.
    track                   = db.relationship(
        "Track",
        back_populates = "path_",
        uselist = False)

    def __repr__(self):
        return f"TrackPath<for={self.track.name}>"

    @property
    def start_point(self):
        """Return the Shapely Point at the very start of the track, or None if not yet set."""
        if not self.multi_linestring:
            return None
        return geometry.Point(self.multi_linestring.geoms[0].coords[0])

    @property
    def finish_point(self):
        """Return the Shapely Point at the very end of the track, or None if not yet set."""
        if not self.multi_linestring:
            return None
        last_linestring = self.multi_linestring.geoms[len(self.multi_linestring.geoms)-1]
        return geometry.Point(last_linestring.coords[len(last_linestring.coords)-1])


class Track(db.Model, PointGeometryMixin):
    """A track created and uploaded by a User. This implements the point geometry mixin, which will refer to the start point of the track. Also, a one-to-one
    relationship with the track path represents the actual track. This model is on a dynamic load strategy for query speed reasons."""
    __tablename__ = "track"

    id                      = db.Column(db.Integer, primary_key = True)
    user_id                 = db.Column(db.Integer, db.ForeignKey("user_.id", ondelete = "CASCADE"))

    # A hash of this track, this is used to uniquely identify this track. This can't be None.
    track_hash              = db.Column(db.String(256), nullable = False)
    # Images for this track.
    """TODO: images for the track here."""
    # The track's name, can't be None.
    name                    = db.Column(db.String(64), nullable = False)
    # The track's description, can't be None.
    description             = db.Column(db.String(256), nullable = False)
    # This track's leaderboard.
    """TODO: leaderboard for the track."""
    # The ratings given by Users to this track.
    """TODO: ratings for the track."""
    # The comments given by Users to this track.
    """TODO: comments for the track."""
    # Whether this track has been verified. Default is False.
    verified                = db.Column(db.Boolean, default = False)

    # The User that owns this track. Can't be None.
    user                    = db.relationship(
        "User",
        back_populates = "tracks_",
        uselist = False)
    # This track's path instance. This is a dynamic relationship so the entire path is not loaded on each query. Can't be None.
    path_                   = db.relationship(
        "TrackPath",
        back_populates = "track",
        uselist = False,
        cascade = "all, delete")
    # The races attached to this track, as a dynamic relationship.
    races_                  = db.relationship(
        "TrackUserRace",
        back_populates = "track",
        uselist = True,
        lazy = "dynamic",
        cascade = "all, delete")

    def __repr__(self):
        return f"Track<{self.name},v={self.verified}>"

    @hybrid_property
    def uid(self):
        return self.track_hash

    @property
    def path(self):
        """Return the track's path."""
        return self.path_

    def set_path(self, track_path):
        """Set this track's path. This should be an instance of TrackPath."""
        self.path_ = track_path

    @classmethod
    def find(cls, **kwargs):
        """Find a track matching the given criteria. Only the first result of whatever criteria given will be returned."""
        track_hash = kwargs.get("track_hash", None)
        track_uid = kwargs.get("track_uid", None)
        track_q = db.session.query(Track)
        if track_hash:
            track_q = track_q\
                .filter(Track.track_hash == track_hash)
        if track_uid:
            track_q = track_q\
                .filter(Track.uid == track_uid)
        return track_q.first()


class UserVerify(db.Model):
    """A model that will contain verification requests posted toward specific users."""
    __tablename__ = "user_verify"

    id                      = db.Column(db.Integer, primary_key = True)
    user_id                 = db.Column(db.Integer, db.ForeignKey("user_.id", ondelete = "CASCADE"))

    created                 = db.Column(db.DateTime, default = datetime.now)
    expires                 = db.Column(db.BigInteger, default = -1)
    token                   = db.Column(db.String(256), nullable = False, unique = True)
    reason_id               = db.Column(db.String(128), nullable = False)

    verified                = db.Column(db.Boolean, default = False)
    verified_on             = db.Column(db.DateTime, default = None)
    last_email_sent_on      = db.Column(db.DateTime, default = None)

    user                    = db.relationship(
        "User",
        back_populates = "verifies_",
        uselist = False)

    def __repr__(self):
        return f"UserVerify<{self.user},v={self.verified}>"

    @hybrid_property
    def is_expired(self):
        """Returns True if this verification instance is expired."""
        if self.expires < 0:
            return False
        timestamp_now = g.get("datetime_now", datetime.now()).timestamp() \
            or datetime.now().timestamp()
        return timestamp_now > self.expires

    @is_expired.expression
    def is_expired(self):
        """Expression level implementation of is_expired"""
        timestamp_now = g.get("datetime_now", datetime.now()).timestamp() \
            or datetime.now().timestamp()
        return or_(self.expires < 0, timestamp_now > self.expires)

    @classmethod
    def create(cls, user, reason, **kwargs):
        """Creates and returns a UserVerify row. Does not add it to the session.

        Arguments
        ---------
        :user: The user who needs to verify.
        :reason: A reason code for this verification.

        Keyword arguments
        -----------------
        :token: Optional. The token to use for this verification row. This MUST be unique. By default, a SHA256 hash will be generated from the time & user's UID."""
        token = kwargs.get("token", None)

        try:
            # Generate our own token if none is given, or one is given but it isn't unique.
            if not token or (token and UserVerify.get_by_token(token) != None):
                # Generate a new one for this verification row.
                LOG.debug(f"Generating token for UserVerify row with User uid: {user.uid}")
                hash = hashlib.sha256()
                hash.update(user.uid.encode("utf-8"))
                hash.update(str(time.time()).encode("utf-8"))
                token = hash.hexdigest()
            # Now create and return the user verify.
            return UserVerify(
                user = user,
                token = token,
                reason_id = reason
            )
        except Exception as e:
            raise e

    @classmethod
    def get_by_user_and_reason(cls, user, reason_id):
        return db.session.query(UserVerify)\
            .filter(UserVerify.user_id == user.id)\
            .filter(UserVerify.reason_id == reason_id)\
            .first()

    @classmethod
    def get_by_token(cls, token):
        return db.session.query(UserVerify)\
            .filter(UserVerify.token == token)\
            .first()


class UserLocationRace(db.Model):
    """An association object to be used as a secondary between the UserLocation and TrackUserRace models."""
    __tablename__ = "user_location_race"

    user_location_id        = db.Column(db.Integer, db.ForeignKey("user_location.id"), primary_key = True, nullable = False)
    # Composite foreign key to TrackUserRace.
    race_track_id           = db.Column(db.Integer, nullable = False, primary_key = True)
    race_user_id            = db.Column(db.Integer, nullable = False, primary_key = True)
    race_uid                = db.Column(db.String(65), nullable = False, primary_key = True)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ["race_track_id", "race_user_id", "race_uid"],
            ["track_user_race.track_id", "track_user_race.user_id", "track_user_race.uid"]
        ),
    )


class UserLocation(db.Model, PointGeometryMixin):
    """Represents a single position of location history for a specific User."""
    __tablename__ = "user_location"

    id                      = db.Column(db.Integer, primary_key = True)
    user_id                 = db.Column(db.Integer, db.ForeignKey("user_.id", ondelete = "CASCADE"), nullable = False)

    # The original latitude & longitude for the User, in an indeterminate EPSG.
    longitude               = db.Column(db.Numeric(14, 11), nullable = False)
    latitude                = db.Column(db.Numeric(13, 11), nullable = False)
    # The time (in milliseconds) at which this location snapshot was taken. Can't be None.
    logged_at               = db.Column(db.BigInteger, nullable = False)
    # The User's rotation at this time. Can't be None.
    rotation                = db.Column(db.Numeric(8,5), nullable = False)
    # The User's speed at this time. Can't be None.
    speed                   = db.Column(db.Numeric(8, 5), nullable = False)

    # A UserLocation can also potentially belong to a single TrackUserRace instance, meaning that this user location was logged while
    # the User was racing a particular track. These user locations can only be deleted if the TrackUserRace itself is deleted.
    track_user_race         = db.relationship(
        "TrackUserRace",
        back_populates = "progress",
        uselist = False,
        secondary = "user_location_race")
    # The User that owns this location. Can't be None.
    user                    = db.relationship(
        "User",
        back_populates = "location_history_",
        uselist = False)

    def __repr__(self):
        return f"UserLocation<{self.user}>"


class User(UserMixin, db.Model):
    """Represents an individual's account with HawkSpeed."""
    __tablename__ = "user_"

    PRIVILEGE_USER = 0
    PRIVILEGE_ADMINISTRATOR = 5

    id                      = db.Column(db.Integer, primary_key = True)

    uid                     = db.Column(db.String(65), unique = True, default = lambda: uuid.uuid4().hex.lower())
    email_address           = db.Column(db.String(128), nullable = False)
    username                = db.Column(db.String(32), nullable = True, default = None)
    bio                     = db.Column(db.Text, nullable = True, default = None)
    password                = db.Column(db.String(254), nullable = False)

    is_bot                  = db.Column(db.Boolean, default = False)
    enabled                 = db.Column(db.Boolean, default = True)
    verified                = db.Column(db.Boolean, default = False)
    profile_setup           = db.Column(db.Boolean, default = False)
    created                 = db.Column(db.BigInteger, default = time.time)
    privilege               = db.Column(db.Integer, default = PRIVILEGE_USER)
    # The request session ID/socket ID associated with SocketIO. When this is not None, the User is connected to the world.
    socket_id               = db.Column(db.String(32), nullable = True, default = None)

    # The User's ongoing race, if any. This is a view only relationship.
    ongoing_race_           = db.relationship(
        "TrackUserRace",
        primaryjoin = and_(TrackUserRace.user_id == id, TrackUserRace.is_ongoing == True),
        uselist = False,
        viewonly = True)
    # This User's location history, as a dynamic relationship. Order this so newest updates appear first.
    location_history_       = db.relationship(
        "UserLocation",
        back_populates = "user",
        lazy = "dynamic",
        uselist = True,
        cascade = "all, delete")
    # This User's tracks, as a dynamic relationship.
    tracks_                 = db.relationship(
        "Track",
        back_populates = "user",
        uselist = True,
        lazy = "dynamic",
        cascade = "all, delete")
    # The races performed by this User, as a dynamic relationship.
    races_                  = db.relationship(
        "TrackUserRace",
        back_populates = "user",
        uselist = True,
        lazy = "dynamic",
        cascade = "all, delete")
    # Dynamic relationship for all verifies.
    verifies_               = db.relationship(
        "UserVerify",
        back_populates = "user",
        uselist = True,
        order_by = "desc(UserVerify.created)",
        lazy = "dynamic",
        cascade = "all, delete")

    def __repr__(self):
        return f"User<{self.email_address},e={self.enabled},v={self.verified}>"

    @hybrid_property
    def is_setup(self):
        """Determine if this User is setup."""
        return self.profile_setup == True and self.requires_verification == False

    @is_setup.expression
    def is_setup(cls):
        """Expression level for determining whether User is setup."""
        raise NotImplementedError()

    @property
    def player(self):
        """Return the world Player. For now, it is just the User itself."""
        return self

    @property
    def location_history(self):
        """Return the User's location history as a query."""
        return self.location_history_

    @property
    def is_profile_setup(self):
        """Returns True if the User's profile is setup."""
        return self.profile_setup

    @property
    def is_account_verified(self):
        """Returns True if there are no open UserVerifies on this User and verified attribute is False."""
        return self.find_open_verify_requirement(reason_id = "new-account") == None and self.verified == True

    @property
    def is_password_verified(self):
        """Returns True if there is an open UserVerify on this User, of type password."""
        """TODO: proper logic when we have password verification."""
        return True

    @property
    def requires_verification(self):
        """Returns True if this User requires a verification of any type currently."""
        return not self.is_account_verified or not self.is_password_verified

    @property
    def has_ongoing_race(self):
        """Return True if the User currently has an ongoing race, otherwise False."""
        return self.ongoing_race != None

    @property
    def ongoing_race(self):
        """Return the currently ongoing race instance for this User, or None."""
        return self.races_\
            .filter(TrackUserRace.is_ongoing == True)\
            .first()

    def add_location(self, location):
        """Adds this location to the User's history."""
        self.location_history_.append(location)

    def set_socket_session(self, sid):
        """Set this User's socket session to the latest one."""
        self.socket_id = sid

    def clear_socket_session(self):
        """Clear the User's socket session."""
        self.socket_id = None

    def set_username(self, username):
        """Set this User's username to the given text."""
        self.username = username

    def set_bio(self, bio):
        """Set this User's bio to the given text."""
        self.bio = bio

    def update_password(self, new_password):
        """Update this User's password to the given text. This function will call for password verification."""
        raise NotImplementedError("update_password is not implemented correctly; password needs to be verified!")

    def set_password(self, new_password):
        """Set this User's password to the given text."""
        self.password = generate_password_hash(new_password)

    def check_password(self, password):
        """Check the given password against the hash stored in this User."""
        return check_password_hash(self.password, password)

    def set_privilege(self, privilege):
        LOG.debug(f"Setting privilege for {self} to {privilege}")
        self.privilege = privilege

    def set_enabled(self, enabled):
        LOG.debug(f"Setting {self} enabled to {enabled}")
        self.enabled = enabled

    def set_verified(self, verified):
        LOG.debug(f"Setting {self} verified to {verified}")
        self.verified = verified

    def set_profile_setup(self, setup):
        LOG.debug(f"Setting profile setup for {self} to {setup}")
        self.profile_setup = setup

    def find_open_verify_requirement(self, **kwargs) -> UserVerify:
        """Search for and return a UserVerify instance belonging to this User that match the given arguments.

        Keyword arguments
        -----------------
        :reason_id: The reason ID to search an open verify for.

        Returns
        -------
        A UserVerify instance, or None."""
        reason_id = kwargs.get("reason_id", None)
        verify_q = self.verifies_\
            .filter(UserVerify.verified == False)
        if reason_id:
            verify_q = verify_q\
                .filter(UserVerify.reason_id == reason_id)
        return verify_q.first()

    @classmethod
    def search(cls, **kwargs):
        """Searches for a single User matching any of the following criteria; email address, username.
        You can provide entries for multiple, as long as there's a hit on at least one attribute, that result will be returned.

        Keyword arguments
        -----------------
        :email_address: Filter by an email address.
        :username: Filter by a username.

        Returns
        -------
        A User, if one is found."""
        email_address = kwargs.get("email_address", None)
        username = kwargs.get("username", None)
        query = db.session.query(User)
        and_filters = []
        if email_address:
            and_filters.append(func.lower(User.email_address) == email_address.lower())
        if username:
            and_filters.append(func.lower(User.username) == username.lower())
        query = query\
            .filter(and_(*and_filters))
        return query.first()


class ServerConfiguration(db.Model):
    """"""
    __tablename__ = "server_configuration"

    id                      = db.Column(db.Integer, primary_key = True)
    user_id                 = db.Column(db.Integer, db.ForeignKey("user_.id"), nullable = False)

    # The HawkSpeed user.
    user                    = db.relationship(
        "User",
        uselist = False)

    def __repr__(self):
        return f"ServerConfiguration<{self.id}>"

    @classmethod
    def new(cls):
        # Create a new ServerConfiguration, set attributes, add to session and return it.
        server_cfg = ServerConfiguration()
        # Create the HawkSpeed User for this server configuration instance.
        """TODO: improve this structure."""
        hawkspeed_user = User(**(config.HAWKSPEED_USER))
        hawkspeed_user.is_bot = True
        hawkspeed_user.set_password(config.HAWKSPEED_USER["password"])
        server_cfg.user = hawkspeed_user

        db.session.add(server_cfg)
        # Also flush it.
        db.session.flush()
        return server_cfg

    @classmethod
    def get(cls):
        # Get the first entry, always.
        server_cfg = db.session.query(ServerConfiguration).first()
        if not server_cfg:
            # Raise an exception, as creating the server_cfg must be done BEFORE ever calling get.
            # This should be done in a manage function.
            LOG.error(f"Failed to get the ServerConfiguration instance! One does not yet exist.")
            raise error.NoServerConfigurationError()
        # Otherwise return it.
        return server_cfg
