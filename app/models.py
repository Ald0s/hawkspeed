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

from typing import List, Optional
from datetime import datetime, date, timedelta, timezone

# All imports for geospatial aspect.
import pyproj
from shapely import geometry, wkb, ops
from geoalchemy2 import Geometry, shape

from flask_login import AnonymousUserMixin, UserMixin, current_user
from flask import request, g
from sqlalchemy import asc, desc, or_, and_, func, select, case, insert, union_all
from sqlalchemy import Table, Column, BigInteger, Boolean, Date, DateTime, Numeric, String, Text, PickleType, ForeignKey, ForeignKeyConstraint
from sqlalchemy.types import TypeDecorator, CHAR
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, aliased, Mapped, mapped_column, with_polymorphic, declared_attr, column_property, query_expression
from sqlalchemy.sql.expression import cast
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.hybrid import hybrid_property, hybrid_method
from sqlalchemy.ext.associationproxy import association_proxy, AssociationProxy
from sqlalchemy.event import listens_for
from sqlite3 import IntegrityError as SQLLite3IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from marshmallow import Schema, fields, EXCLUDE, post_load

from . import db, config, login_manager, error, compat

LOG = logging.getLogger("hawkspeed.models")
LOG.setLevel( logging.DEBUG )


class GUID(TypeDecorator):
    """https://gist.github.com/gmolveau/7caeeefe637679005a7bb9ae1b5e421e
    Platform-independent GUID type.
    Uses PostgreSQL's UUID type, otherwise uses
    CHAR(32), storing as stringified hex values."""
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(UUID())
        else:
            return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == 'postgresql':
            return str(value)
        else:
            if not isinstance(value, uuid.UUID):
                try:
                    return "%.32x" % uuid.UUID(value).int
                except ValueError as ve:
                    return None
            else:
                # hexstring
                return "%.32x" % value.int

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value.hex.lower()
        return value


class HasUUIDMixin():
    """A mixin that grants implementing types a UUID."""
    __abstract__ = True

    uid = mapped_column(GUID(), unique = True, default = lambda: uuid.uuid4().hex.lower())


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
    def crs(cls) -> Mapped[int]:
        #return db.Column(db.Integer, nullable = True, default = None)
        return mapped_column(nullable = True, default = None)

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

    # A composite primary key that involves the track being raced, the User doing the racing, and a UID; which uniquely identifies this particular track/user race instance, so
    # the User can have multiple attempts.
    track_id: Mapped[int] = mapped_column(ForeignKey("track.id", ondelete = "CASCADE"), primary_key = True, nullable = False)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_.id", ondelete = "CASCADE"), primary_key = True, nullable = False)
    uid: Mapped[str] = mapped_column(GUID(), primary_key = True, default = lambda: uuid.uuid4().hex.lower())
    # A foreign key to the user vehicle table. This is the vehicle selected at the start of the race. Can't be None.
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("user_vehicle.id", ondelete = "CASCADE"), nullable = False)

    # A query expression which, at query time, is to be filled with a window function that queries this race's place in the leaderboard. This should only be with'd on
    # races that are finished, that is, finished is True.
    finishing_place = query_expression()
    # When this race was started; in milliseconds. This is said to be when the User's client communicates their intent to begin the race. Can't be None.
    started: Mapped[int] = mapped_column(BigInteger(), nullable = False, default = lambda: time.time() * 1000)
    # When this race was completed; in milliseconds. This is said to be when the server determines the race was completed successfully.
    finished: Mapped[int] = mapped_column(BigInteger(), nullable = True, default = None)
    # If this race was disqualified. Can't be None.
    disqualified: Mapped[bool] = mapped_column(nullable = False, default = False)
    # The reason for this race's disqualification. Can be None.
    dq_reason: Mapped[str] = mapped_column(String(32), nullable = True, default = None)
    # Extra info for the disqualification; this is JSON stored as a string. Can be None.
    dq_extra_info_: Mapped[str] = mapped_column(Text(), nullable = True, default = None)
    # If this race has been cancelled. Can't be None.
    cancelled: Mapped[bool] = mapped_column(nullable = False, default = False)
    # Average speed, in meters per hour. Can be None.
    average_speed: Mapped[int] = mapped_column(nullable = True, default = None)
    # The time taken thus far, in milliseconds. Can be None.
    stopwatch: Mapped[int] = mapped_column(nullable = True, default = None)

    # An association proxy for the Track's UID, which is actually the track's hash.
    track_uid: AssociationProxy["Track"] = association_proxy("track", "uid")

    # All UserLocation instances logged by the User at the time this track was being raced. This is an eager relationship, and the referred UserLocation objects can only
    # be deleted if the User or the TrackUserRace instances are deleted. The race progress is loaded in order from start to finish (ascending.)
    progress: Mapped[List["UserLocation"]] = relationship(
        back_populates = "track_user_race",
        uselist = True,
        order_by = "asc(UserLocation.logged_at)",
        secondary = "user_location_race")
    # The Vehicle chosen to race. Can't be None.
    vehicle: Mapped["UserVehicle"] = relationship(
        back_populates = "races_",
        uselist = False)
    # The Track being raced.
    track: Mapped["Track"] = relationship(
        back_populates = "races_",
        uselist = False)
    # The User doing the racing.
    user: Mapped["User"] = relationship(
        back_populates = "races_",
        uselist = False)

    def __repr__(self):
        return f"TrackUserRace<{self.track},{self.user},o={self.is_ongoing},dq={self.is_disqualified}>"

    @hybrid_property
    def is_ongoing(self):
        """Returns True when finished is None, disqualified is False and cancelled is False."""
        return self.finished == None and self.disqualified == False and self.cancelled == False

    @is_ongoing.expression
    def is_ongoing(cls):
        """Expression level is ongoing."""
        return and_(cls.finished == None, and_(cls.disqualified == False, cls.cancelled == False))

    @hybrid_property
    def is_finished(self):
        """Return True if the race is finished. That is, the finished flag is True, and the race is NOT ongoing."""
        return self.finished != None and not self.is_ongoing

    @is_finished.expression
    def is_finished(cls):
        """Expression level is finished."""
        return and_(cls.finished != None, cls.is_ongoing != True)

    @property
    def is_disqualified(self):
        """Returns True if the race has been disqualified."""
        return self.disqualified

    @property
    def vehicle_used(self):
        """Returns the vehicle used by this Player to complete the race."""
        """TODO: complete this. For now, it will just return NO VEHICLE."""
        return "NO VEHICLE"
    
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

    def set_vehicle(self, vehicle):
        """Set this race's vehicle."""
        self.vehicle = vehicle
        
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

    id: Mapped[int] = mapped_column(primary_key = True)
    track_id: Mapped[int] = mapped_column(ForeignKey("track.id", ondelete = "CASCADE"))

    # The track this path belongs to. Can't be None.
    track: Mapped["Track"] = relationship(
        back_populates = "path_",
        uselist = False)

    def __repr__(self):
        return f"TrackPath<for={self.track.name}>"

    @property
    def uid(self):
        """Returns the track path's UID, for now, this is just the Track's UID."""
        return self.track.uid
    
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


class TrackComment(db.Model):
    """A model that represents a comment created by a User toward a track. There can be multiple Comments by a single User toward a Track, therefore we will employ
    a third attribute as part of our composite key; a UID."""
    __tablename__ = "track_comment"

    # Composite primary key, involving the track and user models, as well as a UID. Both track and user references will cascade on delete, hopefully deleting the
    # rating if either the User or track is deleted.
    track_id: Mapped[int] = mapped_column(ForeignKey("track.id", ondelete = "CASCADE"), primary_key = True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_.id", ondelete = "CASCADE"), primary_key = True)
    uid: Mapped[str] = mapped_column(GUID(), primary_key = True, default = lambda: uuid.uuid4().hex.lower())
    
    # An association proxy through to the Track's UID.
    track_uid: AssociationProxy["Track"] = association_proxy("track", "uid")

    # A timestamp, in seconds, when this comment was created. Can't be None.
    created: Mapped[int] = mapped_column(BigInteger(), nullable = False, default = time.time)
    # The comment's text. Can't be None.
    text: Mapped[str] = mapped_column(Text(), nullable = False)

    # The User that created this comment. Can't be None.
    user: Mapped["User"] = relationship(
        back_populates = "track_comments_",
        uselist = False)
    # The Track being commented on. Can't be None.
    track: Mapped["Track"] = relationship(
        back_populates = "comments_",
        uselist = False)
    
    def __repr__(self):
        return f"TrackComment<{self.user},{self.track}>"
    

class TrackRating(db.Model):
    """A model that represents a rating created by a User toward a track. There can be at most one User/Track rating, and this rating can hold a positive or
    negative value, which will evaluate to a like or a dislike."""
    __tablename__ = "track_rating"

    # Composite primary key, involving the track and user models. Both will cascade on delete, hopefully deleting the rating if either the User or track is deleted.
    track_id: Mapped[int] = mapped_column(ForeignKey("track.id", ondelete = "CASCADE"), primary_key = True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_.id", ondelete = "CASCADE"), primary_key = True)
    # The actual rating. This is a boolean; True for like, False for dislike. There is no default, and this can't be None.
    rating: Mapped[bool] = mapped_column(nullable = False)

    # The User that created this rating. Can't be None.
    user: Mapped["User"] = relationship(
        back_populates = "track_ratings_",
        uselist = False)
    # The Track being rated. Can't be None.
    track: Mapped["Track"] = relationship(
        back_populates = "ratings_",
        uselist = False)
    
    def __repr__(self):
        return f"TrackRating<{self.user},{self.track},r={self.rating}>"


class Track(db.Model, PointGeometryMixin):
    """A track created and uploaded by a User. This implements the point geometry mixin, which will refer to the start point of the track. Also, a one-to-one
    relationship with the track path represents the actual track. This model is on a dynamic load strategy for query speed reasons."""
    __tablename__ = "track"

    # Track types here.
    TYPE_SPRINT = 0
    TYPE_CIRCUIT = 1

    id: Mapped[int] = mapped_column(primary_key = True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_.id", ondelete = "CASCADE"), nullable = True)

    # A hash of this track, this is used to uniquely identify this track. This can't be None.
    track_hash: Mapped[str] = mapped_column(String(256), nullable = False, unique = True)
    # The track's name, can't be None.
    name: Mapped[str] = mapped_column(String(64), nullable = False)
    # The track's description, can't be None.
    description: Mapped[str] = mapped_column(String(256), nullable = False)
    # The track's type. Can't be None.
    track_type: Mapped[int] = mapped_column(nullable = False)
    # Whether this track has been verified. Default is False, can't be None.
    verified: Mapped[bool] = mapped_column(nullable = False, default = False)

    # The ratings given by Users to this track, as a dynamic relationship. Unordered.
    ratings_: Mapped[List[TrackRating]] = relationship(
        back_populates = "track",
        lazy = "dynamic",
        uselist = True,
        cascade = "all, delete")
    # The comments given by Users to this track, as a dynamic relationship. Unordered.
    comments_: Mapped[List[TrackComment]] = relationship(
        back_populates = "track",
        lazy = "dynamic",
        uselist = True,
        cascade = "all, delete")
    # The User that owns this track. Can be None, but if its None, the track will not be visible.
    user: Mapped["User"] = relationship(
        back_populates = "tracks_",
        uselist = False)
    # This track's path instance. Can't be None.
    """TODO: should make this dynamic?"""
    path_: Mapped["TrackPath"] = relationship(
        back_populates = "track",
        uselist = False,
        cascade = "all, delete")
    # The races attached to this track, as a dynamic relationship. Unordered.
    races_: Mapped[List["TrackUserRace"]] = relationship(
        back_populates = "track",
        uselist = True,
        lazy = "dynamic",
        cascade = "all, delete")

    def __repr__(self):
        return f"Track<{self.name},v={self.verified}>"

    @hybrid_property
    def uid(self):
        return self.track_hash
    
    @hybrid_property
    def can_be_raced(self):
        """Returns True if this track can be raced; that is, it is verified and has an owner. This boolean should be used to determine whether
        this track appears on the world map."""
        return self.is_verified and self.has_owner
    
    @can_be_raced.expression
    def can_be_raced(cls):
        """Expression level equivalent of can be raced."""
        raise NotImplementedError()
    
    @hybrid_property
    def is_verified(self):
        return self.verified
    
    @property
    def has_owner(self):
        """Returns True if this Track has an owner."""
        return self.user != None
    
    @property
    def num_comments(self):
        """Returns the total count of the comments relationship."""
        return self.comments_.count()
    
    @property
    def start_point(self):
        """Return this track's start point, as a geometry."""
        return self.point
    
    @property
    def path(self):
        """Return the track's path."""
        return self.path_

    def set_name(self, name):
        """Set this track's name."""
        self.name = name

    def set_description(self, description):
        """Set this track's description."""
        self.description = description

    def set_track_type(self, track_type):
        """Set this track's type."""
        self.track_type = track_type

    def set_verified(self, verified):
        """Set whether this track is verified."""
        self.verified = verified

    def set_path(self, track_path):
        """Set this track's path. This should be an instance of TrackPath."""
        self.path_ = track_path
    
    def set_owner(self, user):
        """Set the owner of this track to the given User."""
        self.user = user

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

    id: Mapped[int] = mapped_column(primary_key = True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_.id", ondelete = "CASCADE"))

    created: Mapped[datetime] = mapped_column(DateTime, default = datetime.now)
    expires: Mapped[int] = mapped_column(BigInteger, default = -1)
    token: Mapped[str] = mapped_column(String(256), nullable = False, unique = True)
    reason_id: Mapped[str] = mapped_column(String(128), nullable = False)

    verified: Mapped[bool] = mapped_column(Boolean, default = False)
    verified_on: Mapped[datetime] = mapped_column(DateTime, nullable = True, default = None)
    last_email_sent_on: Mapped[datetime] = mapped_column(DateTime, nullable = True, default = None)

    user: Mapped["User"] = relationship(
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

    user_location_id: Mapped[int] = mapped_column(ForeignKey("user_location.id"), primary_key = True, nullable = False)
    # Composite foreign key to TrackUserRace.
    race_track_id: Mapped[int] = mapped_column(nullable = False, primary_key = True)
    race_user_id: Mapped[int] = mapped_column(nullable = False, primary_key = True)
    race_uid: Mapped[str] = mapped_column(GUID(), nullable = False, primary_key = True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["race_track_id", "race_user_id", "race_uid"],
            ["track_user_race.track_id", "track_user_race.user_id", "track_user_race.uid"]
        ),
    )


class UserLocation(db.Model, PointGeometryMixin):
    """Represents a single position of location history for a specific User."""
    __tablename__ = "user_location"

    id: Mapped[int] = mapped_column(primary_key = True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_.id", ondelete = "CASCADE"), nullable = False)

    # The original latitude & longitude for the User, in an indeterminate EPSG.
    longitude: Mapped[float] = mapped_column(Numeric(14, 11), nullable = False)
    latitude: Mapped[float] = mapped_column(Numeric(13, 11), nullable = False)
    # The time (in milliseconds) at which this location snapshot was taken. Can't be None.
    logged_at: Mapped[int] = mapped_column(BigInteger, nullable = False)
    # The User's rotation at this time. Can't be None.
    rotation: Mapped[float] = mapped_column(Numeric(8,5), nullable = False)
    # The User's speed at this time. Can't be None.
    speed: Mapped[float] = mapped_column(Numeric(8, 5), nullable = False)

    # A UserLocation can also potentially belong to a single TrackUserRace instance, meaning that this user location was logged while
    # the User was racing a particular track. These user locations can only be deleted if the TrackUserRace itself is deleted.
    track_user_race: Mapped["TrackUserRace"] = relationship(
        back_populates = "progress",
        uselist = False,
        secondary = "user_location_race")
    # The User that owns this location. Can't be None.
    user: Mapped["User"] = relationship(
        back_populates = "location_history_",
        uselist = False)

    def __repr__(self):
        return f"UserLocation<{self.user}>"


class UserVehicle(db.Model, HasUUIDMixin):
    """Represents a User's vehicle."""
    __tablename__ = "user_vehicle"

    id: Mapped[int] = mapped_column(primary_key = True)
    # The User's ID that owns this vehicle, can't be None.
    user_id: Mapped[int] = mapped_column(ForeignKey("user_.id", ondelete = "CASCADE"), nullable = False)

    # For now, we will store the vehicle info as just a string, that we don't really bother validating.
    """TODO: perhaps a better way of storing vehicles?"""
    text: Mapped[str] = mapped_column(String(64), nullable = False)

    # All races this vehicle has been involved in. This is a dynamic relationship. Unordered.
    races_: Mapped[TrackUserRace] = relationship(
        back_populates = "vehicle",
        uselist = True,
        lazy = "dynamic",
        cascade = "all, delete")
    # The User that owns this Vehicle. Can't be None.
    user: Mapped["User"] = relationship(
        back_populates = "vehicles_",
        foreign_keys = [user_id],
        uselist = False)
    
    def __repr__(self):
        return f"UserVehicle<{self.title},u={self.user}>"
    
    @property
    def title(self):
        """Returns a string that can be used as this vehicle's display name."""
        return self.text
    
    def set_text(self, text):
        """Set this vehicle's text."""
        self.text = text


class User(UserMixin, db.Model, HasUUIDMixin):
    """Represents an individual's account with HawkSpeed."""
    __tablename__ = "user_"

    PRIVILEGE_USER = 0
    PRIVILEGE_ADMINISTRATOR = 5

    id: Mapped[int] = mapped_column(primary_key = True)

    # The User's email address. This must be unique and can't be None.
    email_address: Mapped[str] = mapped_column(String(128), nullable = False)
    # The User's username. This must be unique and is configured to be nullable; since this is only set when the User sets their profile up.
    username: Mapped[str] = mapped_column(String(32), unique = True, nullable = True, default = None)
    # The User's bio. This can be None.
    bio: Mapped[str] = mapped_column(Text(), nullable = True, default = None)
    # The User's password, or hash thereof. Can't be None as this is set on initial registration.
    password: Mapped[str] = mapped_column(String(254), nullable = False)

    # A boolean indicating whether this User is a bot. Default is False, can't be None.
    is_bot: Mapped[bool] = mapped_column(nullable = False, default = False)
    # A boolean indicating whether this User is enabled. Default is True, can't be None.
    enabled: Mapped[bool] = mapped_column(nullable = False, default = True)
    # A boolean indicating whether this User is verified. Default is False, can't be None.
    verified: Mapped[bool] = mapped_column(nullable = False, default = False)
    # A boolean indicating whether this User has set their profile up. Default is False, can't be None.
    profile_setup: Mapped[bool] = mapped_column(nullable = False, default = False)
    # A timestamp in seconds, when this User was created. Can't be None.
    created: Mapped[int] = mapped_column(BigInteger(), nullable = False, default = time.time)
    # The User's privilege, can't be None.
    privilege: Mapped[int] = mapped_column(nullable = False, default = PRIVILEGE_USER)
    # The last time a location update was received from this User, as a timestamp in seconds. This will not be nulled in between sessions. Can be None.
    last_location_update: Mapped[int] = mapped_column(BigInteger(), nullable = True, default = None)

    """TODO: Objects that pertain to a single session of connection. Migrate these to a separate Player model that is dependant on the SocketIO connection, and as such is
    deleted if that connection is lost."""
    # The request session ID/socket ID associated with SocketIO. When this is not None, the User is connected to the world. Can be None, indicating the User is not playing.
    socket_id: Mapped[str] = mapped_column(String(32), nullable = True, default = None)
    # The ID of the vehicle currently in use by this User. Can be None.
    current_vehicle_id: Mapped[int] = mapped_column(ForeignKey("user_vehicle.id", ondelete = "SET NULL", use_alter = True), nullable = True)
    # The vehicle currently in use by this User. Can be None.
    current_vehicle: Mapped[UserVehicle] = relationship(
        primaryjoin = current_vehicle_id == UserVehicle.id,
        post_update = True,
        uselist = False)

    # All Track comments posted by this User, as a dynamic relationship. Unordered.
    track_comments_: Mapped[List[TrackComment]] = relationship(
        back_populates = "user",
        uselist = True,
        lazy = "dynamic",
        cascade = "all, delete")
    # All Track ratings given by this User, as a dynamic relationship. Unordered.
    track_ratings_: Mapped[List[TrackRating]] = relationship(
        back_populates = "user",
        uselist = True,
        lazy = "dynamic",
        cascade = "all, delete")
    # The User's ongoing race, if any. This is a view only relationship.
    ongoing_race_: Mapped[TrackUserRace] = relationship(
        primaryjoin = and_(TrackUserRace.user_id == id, TrackUserRace.is_ongoing == True),
        uselist = False,
        viewonly = True)
    # This User's location history, as a dynamic relationship. Unordered.
    location_history_: Mapped[List[UserLocation]] = relationship(
        back_populates = "user",
        lazy = "dynamic",
        uselist = True,
        cascade = "all, delete")
    # This User's vehicles, as a dynamic relationship. Unordered.
    vehicles_: Mapped[List[UserVehicle]] = relationship(
        back_populates = "user",
        uselist = True,
        primaryjoin = id == UserVehicle.user_id,
        lazy = "dynamic",
        cascade = "all, delete")
    # This User's tracks, as a dynamic relationship. Unordered.
    tracks_: Mapped[List[Track]] = relationship(
        back_populates = "user",
        uselist = True,
        lazy = "dynamic",
        cascade = "all, delete")
    # All races performed by this User, as a dynamic relationship. This will include any ongoing races. Unordered.
    races_: Mapped[List[TrackUserRace]] = relationship(
        back_populates = "user",
        uselist = True,
        lazy = "dynamic",
        cascade = "all, delete")
    # Dynamic relationship for all verifies. Unordered.
    verifies_: Mapped[List[UserVerify]] = relationship(
        back_populates = "user",
        uselist = True,
        lazy = "dynamic",
        cascade = "all, delete")

    def __repr__(self):
        return f"User<{self.email_address},e={self.enabled},v={self.verified}>"

    @hybrid_property
    def is_setup(self):
        """Determine if this User is setup. Returns True if profile is setup, and no verification is required."""
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
        """Return a query for the User's location history."""
        return self.location_history_

    @property
    def num_vehicles(self):
        return self.vehicles.count()
    
    @property
    def vehicles(self):
        """Return a query for the User's vehicles."""
        return self.vehicles_
    
    @property
    def all_vehicles(self):
        """Return a list of all Vehicles belonging to this User."""
        return self.vehicles.all()
    
    @property
    def is_profile_setup(self):
        """Returns True if the User's profile is setup."""
        return self.profile_setup

    @property
    def requires_verification(self):
        """Returns True if this User requires a verification of any type currently."""
        return not self.is_account_verified or not self.is_password_verified
    
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
    def has_ongoing_race(self):
        """Return True if the User currently has an ongoing race, otherwise False."""
        return self.ongoing_race != None

    @property
    def ongoing_race(self):
        """Return the currently ongoing race instance for this User, or None."""
        return self.ongoing_race_
    
    @property
    def is_playing(self):
        """Returns True if the User is currently in the world. This will return True when the User currently has a socket ID set, and a location update
        has been received from them in the last minute."""
        return self.socket_id != None and (self.last_location_update != None and time.time()-self.last_location_update < 60)

    def add_location(self, location):
        """Adds this location to the User's history."""
        self.location_history_.append(location)

    def set_socket_session(self, sid):
        """Set this User's socket session to the latest one."""
        self.socket_id = sid
    
    def set_last_location_update(self, last_timestamp_s = time.time()):
        """Set the timestamp, in seconds, when the last location update was received."""
        self.last_location_update = last_timestamp_s

    def clear_socket_session(self):
        """Clear the User's socket session."""
        self.socket_id = None

    def clear_current_vehicle(self):
        """Clear the User's current Vehicle."""
        self.current_vehicle = None

    def set_email_address(self, email_address):
        """Set this User's email address."""
        self.email_address = email_address
        
    def set_username(self, username):
        """Set this User's username to the given text."""
        self.username = username

    def set_bio(self, bio):
        """Set this User's bio to the given text."""
        self.bio = bio

    def add_vehicle(self, vehicle):
        """Add a vehicle to this User's vehicles list."""
        self.vehicles_.append(vehicle)

    def set_current_vehicle(self, vehicle):
        """Set this User's current vehicle to the one given."""
        self.current_vehicle = vehicle
        
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
        """Set this User's privilege."""
        LOG.debug(f"Setting privilege for {self} to {privilege}")
        self.privilege = privilege

    def set_enabled(self, enabled):
        """Set this User enabled."""
        LOG.debug(f"Setting {self} enabled to {enabled}")
        self.enabled = enabled

    def set_verified(self, verified):
        """Set this User verified."""
        LOG.debug(f"Setting {self} verified to {verified}")
        self.verified = verified

    def set_profile_setup(self, setup):
        """Set this User's profile setup."""
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

    id: Mapped[int] = mapped_column(primary_key = True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_.id"), nullable = False)

    # The HawkSpeed user.
    user: Mapped[User] = relationship(
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
