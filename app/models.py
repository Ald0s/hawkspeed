import time
import uuid
import math
import logging
import pytz
import json
import hashlib

from typing import List
from datetime import datetime, date, timedelta, timezone

# All imports for geospatial aspect.
import pyproj
from shapely import geometry, wkb, ops
from geoalchemy2 import Geometry, shape

from flask_login import AnonymousUserMixin, UserMixin
from flask import g
from sqlalchemy import asc, desc, or_, and_, func, select, case, insert, union_all
from sqlalchemy import Table, Column, BigInteger, Boolean, Date, DateTime, Numeric, String, Text, ForeignKey, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.types import TypeDecorator, CHAR
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, aliased, Mapped, mapped_column, with_polymorphic, declared_attr, column_property, query_expression
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.ext.associationproxy import association_proxy, AssociationProxy
from sqlalchemy.event import listens_for
from sqlite3 import IntegrityError as SQLLite3IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash

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
    def geodetic_crs_object(self):
        if not self.crs_object:
            return None
        return self.crs_object.geodetic_crs
    
    @property
    def geodetic_transformer(self):
        return pyproj.Transformer.from_crs(self.crs_object, self.geodetic_crs_object, always_xy = True)

    @declared_attr
    def crs(cls) -> Mapped[int]:
        return mapped_column(nullable = True, default = None)

    def set_crs(self, crs):
        if isinstance(crs, pyproj.crs.CRS):
            crs = crs.to_epsg()
        self.crs = crs


class PointGeometryMixin(EPSGWrapperMixin):
    """Enables all subclasses to own a Point; perhaps as a center for example."""
    @property
    def is_position_valid(self):
        return self.point_geom != None

    @declared_attr
    def point_geom(self):
        """Represents a column for a geometry of type Point that defines the center/position of this object."""
        return db.Column(Geometry("POINT", srid = config.WORLD_CONFIGURATION_CRS))

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
        return db.Column(Geometry("POLYGON", srid = config.WORLD_CONFIGURATION_CRS))

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
        return db.Column(Geometry("MULTIPOLYGON", srid = config.WORLD_CONFIGURATION_CRS))

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
        return db.Column(Geometry("LINESTRING", srid = config.WORLD_CONFIGURATION_CRS))

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
        return db.Column(Geometry("MULTILINESTRING", srid = config.WORLD_CONFIGURATION_CRS))

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


class SnapToRoadTrackPoint(db.Model, PointGeometryMixin):
    """A model for containing a single track point, uniquely identified by an absolute index."""
    __tablename__ = "snap_to_road_track_point"

    id: Mapped[int] = mapped_column(primary_key = True)
    # A foreign key to the snap to road track that owns this point. Cascades such that when snap to road track is deleted, this will be too. Can't be None.
    snap_to_road_track_id: Mapped[int] = mapped_column(ForeignKey("snap_to_road_track.id", ondelete = "CASCADE"), nullable = False)

    # The absolute index for this point. Can't be None.
    absolute_idx: Mapped[int] = mapped_column(nullable = False)

    # The track to which this point belongs.
    snap_to_road_track: Mapped["SnapToRoadTrack"] = relationship(
        back_populates = "track_points",
        uselist = False)
    
    def __repr__(self):
        return f"SnapToRoadTrackPoint<{self.absolute_idx},X={self.geodetic_point.x},Y={self.geodetic_point.y}>"

    def set_absolute_idx(self, absolute_idx):
        """Set this point's absolute index."""
        self.absolute_idx = absolute_idx


class SnapToRoadTrack(db.Model, EPSGWrapperMixin):
    """A model for containing a list of snap to road track points."""
    __tablename__ = "snap_to_road_track"

    id: Mapped[int] = mapped_column(primary_key = True)

    # A list of all points attached to this track. Eagerly loaded, ordered by absolute point index in ascending order.
    track_points: Mapped[List[SnapToRoadTrackPoint]] = relationship(
        back_populates = "snap_to_road_track",
        uselist = True,
        order_by = "asc(SnapToRoadTrackPoint.absolute_idx)",
        cascade = "all, delete")

    def __repr__(self):
        return f"SnapToRoadTrack<npts={self.num_points}>"
    
    @property
    def num_points(self):
        """Return the number of points in this track."""
        return len(self.track_points)
    
    def add_point(self, track_point):
        """Add the given SnapToRoadTrackPoint to this track. This function will raise ValueError if CRS does not match this track's CRS."""
        if track_point.crs != self.crs:
            raise ValueError(f"Failed to add pt {track_point} to a track. CRS mismatch!")
        self.track_points.append(track_point)

    def remove_point(self, track_point):
        """Remove the given SnapToRoadTrackPoint from this track."""
        self.track_points.remove(track_point)
    

class SnapToRoadOrder(db.Model):
    """A model for containing a snap to road order, as part of the verification process for a new track. It will hold two separate references to the SnapToRoadTrack table,
    one for containing the track as yet to be snapped to road, and the other having been snapped to road. The process of snapping should therefore remove points that have
    been snapped from the unsnapped geometry, and place them within the snapped geometry."""
    __tablename__ = "snap_to_road_order"

    id: Mapped[int] = mapped_column(primary_key = True)
    # A foreign key to the snap to track model, for the unsnapped track points. Can't be None.
    unsnapped_track_id: Mapped[int] = mapped_column(ForeignKey("snap_to_road_track.id"), nullable = False)
    # A foreign key to the snap to track model, for the snapped track points. Can't be None.
    snapped_track_id: Mapped[int] = mapped_column(ForeignKey("snap_to_road_track.id"), nullable = False)
    # A foreign key to the track. Can't be None.
    track_id: Mapped[int] = mapped_column(ForeignKey("track.id"), nullable = False)

    # The number of points in total to expect, set during creation phase.
    static_num_points: Mapped[int] = mapped_column(nullable = False)
    # The track. Can't be None.
    track: Mapped["Track"] = relationship(
        uselist = False)
    # The unsnapped snap to track instance. Can't be None.
    unsnapped_track: Mapped[SnapToRoadTrack] = relationship(
        uselist = False,
        foreign_keys = [unsnapped_track_id])
    # The snapped snap to track instance. Can't be None.
    snapped_track: Mapped[SnapToRoadTrack] = relationship(
        uselist = False,
        foreign_keys = [snapped_track_id])
    
    def __repr__(self):
        return f"SnapToRoadOrder<{self.track},% snapped={self.percent_snapped}>"
    
    @property
    def is_complete(self):
        """Returns True if this entire order is complete."""
        return self.percent_snapped == 100
    
    @property
    def num_unsnapped_batches(self):
        """Return the number of batches required to snap the remaining points."""
        return math.ceil(self.unsnapped_track.num_points / config.NUM_POINTS_PER_SNAP_BATCH)
    
    @property
    def percent_snapped(self):
        """Returns the total percent snapped."""
        if self.snapped_track.num_points == 0:
            return 0
        return int((self.snapped_track.num_points / (self.unsnapped_track.num_points + self.snapped_track.num_points)) * 100)
    
    def set_static_num_points(self, static_num_points):
        """Set the static number of points."""
        self.static_num_points = static_num_points

    def set_track(self, track):
        """Set the subject track instance."""
        self.track = track
    
    def set_unsnapped_track(self, unsnapped):
        """Set the unsnapped track in question."""
        self.unsnapped_track = unsnapped
    
    def set_snapped_track(self, snapped):
        """Set the snapped track in question."""
        self.snapped_track = snapped


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
    # If this race attempt should not be included in actual results when in Production/LiveDevelopment mode; specifically, use this for showcasing. Can't be None, default is False.
    fake: Mapped[bool] = mapped_column(nullable = False, default = False)

    ### Some race values, these are updated while the race is ongoing. ###
    # Average speed, in meters per second. Can be None.
    average_speed: Mapped[int] = mapped_column(nullable = True, default = None)
    # The total percent of this race attempt that was missed; that is, where the track has been dodged. This applies to both sprint and circuit races. Can't be None and is 0 by default.
    percent_missed: Mapped[int] = mapped_column(nullable = False, default = 0)
    # Percent of the track complete, only applies to Sprint type tracks. Can be None.
    percent_complete: Mapped[int] = mapped_column(nullable = True, default = None)
    # Number of laps complete, only applies to Circuit type tracks. Can be None.
    num_laps_complete: Mapped[int] = mapped_column(nullable = True, default = None)
 
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
    def stopwatch(self):
        """Instance level property for getting the total amount of time, in milliseconds, elapsed by this race so far. The result is calculated by
        subtracting the 'started' timestamp attribute from either current timestamp (in milliseconds) or the finished timestamp (if given). Keep in
        mind that if finished is False, current timestamp will be used no matter what; so stopwatch could eventually report an ever growing number
        in both instance and expression levels."""
        if self.is_finished:
            # Race is finished. Calculate difference between finished timestamp and the started timestamp.
            return self.finished - self.started
        # Race is not finished. Calculate difference between current time and the started timestamp.
        return (g.get("timestamp_now", time.time()) * 1000) - self.started

    @stopwatch.expression
    def stopwatch(cls):
        """Expression level property for getting this race's set (or ongoing) time."""
        return func.coalesce(cls.finished, g.get("timestamp_now", time.time()) * 1000) - cls.started
    
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
        return self.vehicle
    
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
        """Returns the disqualified extra info as a dictionary if set, otherwise None is returned."""
        if not self.dq_extra_info_:
            return None
        return json.loads(self.dq_extra_info_)

    @dq_extra_info.setter
    def dq_extra_info(self, value):
        """Sets the given disqualified extra info dictionary to the one given, or None."""
        if value:
            self.dq_extra_info_ = json.dumps(value)
        else:
            self.dq_extra_info_ = None

    def set_vehicle(self, vehicle):
        """Set this race's vehicle."""
        self.vehicle = vehicle
        
    def set_track_and_user(self, track, user):
        """Setting the track and user."""
        self.track = track
        self.user = user

    def set_finished(self, time_finished_ms):
        """Set this race to finished. This supplied timestamp must be in milliseconds."""
        # Set the timestamp we finished at.
        self.finished = time_finished_ms

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
        """Set this race's average speed, in meters per second."""
        self.average_speed = average_speed

    def set_num_laps_complete(self, num_laps_complete):
        """Set the number of laps this track is verified to have completed."""
        self.num_laps_complete = num_laps_complete
    
    def set_percent_complete(self, percent_complete):
        """Set the percent of the track complete."""
        self.percent_complete = percent_complete

    def set_percent_missed(self, percent_missed):
        """Set the percent of the race/race track that has been missed. This is judged differently overall for the various types of track types."""
        self.percent_missed = percent_missed

    def add_location(self, location):
        """Add the location as progress."""
        # Add the new location.
        self.progress.append(location)
        # Each time we add a new location, we must re-comprehend the progress geometry.
        self._refresh_progress_geometry()
    
    def set_fake(self, fake):
        """Set whether this attempt is fake or not."""
        self.fake = fake

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
    def length(self):
        """Returns the total length of this track, in meters."""
        return int(self.multi_linestring.length)

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

    # Composite primary key, involving the track and user models, as well as a UID for allowing multiple comments on same track for same user. Both track and user
    # references will cascade on delete, hopefully deleting the comment if either the User or track is deleted.
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
    # The track's start point bearing, in degrees. This is the direction in which the Player must face in order to race. Can't be None.
    start_bearing: Mapped[float] = mapped_column(nullable = False)
    # Whether this track's path has been processed and snap-to-roads has been executed on it (or if this process was skipped.) Default is not REQUIRE_SNAP_TO_ROADS. Can't be None.
    snapped_to_roads: Mapped[bool] = mapped_column(nullable = False, default = not config.REQUIRE_SNAP_TO_ROADS)
    # Whether this track has been verified; meaning administrators have approved it. Default is False, can't be None.
    verified: Mapped[bool] = mapped_column(nullable = False, default = False)
    # The number of laps required by this track. This only applies when this track is a circuit. Can be None.
    num_laps_required: Mapped[int] = mapped_column(nullable = True, default = None)

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
        """Returns True if this track can be raced; that is, it is verified, snapped to roads and has an owner. This boolean should be used
        to determine whether this track appears on the world map."""
        return self.is_verified and self.is_snapped_to_roads and self.has_owner
    
    @can_be_raced.expression
    def can_be_raced(cls):
        """Expression level equivalent of can be raced."""
        return and_(cls.is_verified, and_(cls.is_snapped_to_roads, cls.has_owner))
    
    @hybrid_property
    def is_snapped_to_roads(self):
        return self.snapped_to_roads
    
    @hybrid_property
    def is_verified(self):
        return self.verified
    
    @property
    def length(self):
        """Return the length of the track."""
        return self.path.length
    
    @property
    def has_owner(self):
        """Returns True if this Track has an owner."""
        return self.user != None
    
    @property
    def is_sprint(self):
        """Returns True if this Track is a Sprint type race."""
        return self.track_type == self.TYPE_SPRINT
    
    @property
    def is_circuit(self):
        """Returns True if this Track is a Circuit type race."""
        return self.track_type == self.TYPE_CIRCUIT

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
        """Return the track's path, as an instance."""
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

    def set_start_bearing(self, start_bearing):
        """Set this track's start bearing, should be degrees."""
        self.start_bearing = start_bearing

    def set_num_laps_required(self, num_laps_required):
        """Set the number of laps required for this Track."""
        self.num_laps_required = num_laps_required

    def set_verified(self, verified):
        """Set whether this track is verified."""
        self.verified = verified

    def set_path(self, track_path):
        """Set this track's path. This should be an instance of TrackPath."""
        self.path_ = track_path
    
    def set_owner(self, user):
        """Set the owner of this track to the given User."""
        self.user = user

    def set_snapped_to_roads(self, snapped):
        """Sets whether this track's path has been snapped to roads."""
        self.snapped_to_roads = snapped

    def set_circuit(self, num_laps_required):
        """Set this track as a circuit."""
        self.set_num_laps_required(num_laps_required)
        self.set_track_type(self.TYPE_CIRCUIT)
    
    def set_sprint(self):
        """Set this track as a sprint."""
        self.set_num_laps_required(None)
        self.set_track_type(self.TYPE_SPRINT)
        

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
            ["track_user_race.track_id", "track_user_race.user_id", "track_user_race.uid"],),)


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
    # The User's bearing at this time. Can't be None.
    bearing: Mapped[float] = mapped_column(Numeric(8,5), nullable = False)
    # The User's speed at this time; in meters per second. Can't be None.
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


class VehicleType(db.Model):
    """A general overview of a specific type of vehicle such as a Car or Bike."""
    __tablename__ = "vehicle_type"

    type_id: Mapped[str] = mapped_column(String(32), primary_key = True)

    # The name of this vehicle type. Can't be None; this is also unique.
    name: Mapped[str] = mapped_column(String(32), unique = True, nullable = False)
    # The description of this vehicle type. Can't be None.
    description: Mapped[str] = mapped_column(Text(), nullable = False)
    
    # A dynamic relationship to all models associated with this type- no filtration on make.
    models_: Mapped[List["VehicleModel"]] = relationship(
        back_populates = "type",
        uselist = True,
        lazy = "dynamic")

    def __repr__(self):
        return f"VehicleType<{self.name}>"


class VehicleYear(db.Model):
    """A single year of vehicles."""
    __tablename__ = "vehicle_year"

    year_: Mapped[int] = mapped_column(primary_key = True)

    def __repr__(self):
        return f"VehicleYear<{self.year_}>"
    
    @property
    def year(self):
        return self.year_


class VehicleMake(db.Model):
    """A single vehicle make, that can refer to many vehicle models."""
    __tablename__ = "vehicle_make"

    uid: Mapped[str] = mapped_column(String(128), primary_key = True)

    # The name of this vehicle make. Can't be None; this is also unique.
    name: Mapped[str] = mapped_column(String(48), nullable = False)
    
    # A dynamic relationship to all models associated with this make- no filtration on type.
    models_: Mapped[List["VehicleModel"]] = relationship(
        back_populates = "make",
        uselist = True,
        lazy = "dynamic")

    def __repr__(self):
        return f"VehicleMake<{self.name}>"


class VehicleModel(db.Model):
    """A single vehicle model, which is owned by a single vehicle make, and is differentiated by its name and type. This is where we can abstract a single make
    also producing cars if they're primarily a bike producer etc."""
    __tablename__ = "vehicle_model"

    uid: Mapped[str] = mapped_column(String(128), primary_key = True)
    # The vehicle model's make UID. This can't be None.
    make_uid: Mapped[str] = mapped_column(String(128), ForeignKey("vehicle_make.uid"), nullable = False)
    # The vehicle type's ID. This can't be None.
    type_id: Mapped[str] = mapped_column(String(32), ForeignKey("vehicle_type.type_id"), nullable = False)

    # The name of this vehicle make. Can't be None; this is also unique.
    name: Mapped[str] = mapped_column(String(48), nullable = False)
    
    # Relationship to the vehicle type for this model. This is eager.
    type: Mapped[VehicleType] = relationship(
        back_populates = "models_",
        uselist = False)
    # Relationship to the vehicle make for this model. This is eager.
    make: Mapped[VehicleMake] = relationship(
        back_populates = "models_",
        uselist = False)
    # A dynamic relationship to all available year models for this vehicle model.
    year_models_: Mapped[List["VehicleYearModel"]] = relationship(
        back_populates = "model",
        uselist = True,
        lazy = "dynamic")

    def __repr__(self):
        return f"VehicleModel<{self.name},mk={self.make.name}>"


class VehicleYearModel(db.Model):
    """A single vehicle year model, owned by a single vehicle model; this entity centralises all individually optioned stock vehicles for a specific model and year."""
    __tablename__ = "vehicle_year_model"

    # The primary key for the vehicle year model is a composite made up of make UID, model UID and the year.
    # The make's UID, referring to VehicleMake. This can't be None.
    make_uid: Mapped[str] = mapped_column(String(128), ForeignKey("vehicle_make.uid"), primary_key = True)
    # The model's UID, referring to VehicleModel. This can't be None.
    model_uid: Mapped[str] = mapped_column(String(128), ForeignKey("vehicle_model.uid"), primary_key = True)
    # The year, referring to VehicleYear. This can't be None.
    year_: Mapped[int] = mapped_column(ForeignKey("vehicle_year.year_"), primary_key = True)

    # An eager relationship to this year model's make. This does not back populate.
    make: Mapped[VehicleMake] = relationship(
        uselist = False)
    # An eager relationship to this year model's model. This does not back populate.
    model: Mapped[VehicleModel] = relationship(
        uselist = False)
    # A dynamic relationship to all stock vehicles attached to this vehicle year model. These are essentially separate optioned loadouts from that year.
    stock_vehicles_: Mapped[List["VehicleStock"]] = relationship(
        back_populates = "year_model",
        uselist = True,
        lazy = "dynamic")

    def __repr__(self):
        return f"VehicleYearModel<{self.year_},mdl={self.make.name} {self.model.name}>"
    
    @property
    def year(self):
        return self.year_


class VehicleStock(db.Model):
    """A single stock vehicle. These UIDs can be thought of as associating with a single specific vehicle."""
    __tablename__ = "vehicle_stock"

    # The vehicle stock's UID.
    vehicle_uid: Mapped[str] = mapped_column(String(128), primary_key = True)
    # A composite foreign key to the vehicle year model entity. None of these can be None.
    year_model_make_uid: Mapped[str] = mapped_column(String(128), nullable = False)
    year_model_model_uid: Mapped[str] = mapped_column(String(128), nullable = False)
    year_model_year_: Mapped[int] = mapped_column(nullable = False)

    # The vehicle stock's version. Can be None.
    version: Mapped[str] = mapped_column(nullable = True, default = None)
    # The vehicle stock's badge. Can be None.
    badge: Mapped[str] = mapped_column(nullable = True, default = None)

    # General motor type; piston, electric, rotary. This can't be None, and should be one of 'piston', 'rotary', 'hybrid' or 'electric'.
    motor_type: Mapped[str] = mapped_column(nullable = False)

    ### Specifics for Piston/Rotary types. ###
    # The displacement, in CC. Can be None.
    displacement: Mapped[int] = mapped_column(nullable = True)
    # The induction type. Can be None.
    induction_type: Mapped[str] = mapped_column(nullable = True)
    # The fuel type. Can be None.
    fuel_type: Mapped[str] = mapped_column(nullable = True)

    ### Specifics for Electric types. ###
    # The amount of power this motor produces. Can be None.
    power: Mapped[int] = mapped_column(nullable = True)
    # The type of electric motor(s). Can be None.
    electric_motor_type: Mapped[str] = mapped_column(nullable = True)

    ### Transmission information. ###
    # The transmission type. Can't be None.
    transmission_type: Mapped[str] = mapped_column(nullable = False)
    # The number of gears in this transmission. Can't be None.
    num_gears: Mapped[int] = mapped_column(nullable = False)

    # Association proxy through year model to the vehicle Make entity.
    make: AssociationProxy[VehicleMake] = association_proxy("year_model", "make")
    # Association proxy through year model to the vehicle Model entity.
    model: AssociationProxy[VehicleModel] = association_proxy("year_model", "model")
    # An eager relationship to the year model entity to which this stock vehicle belongs.
    year_model: Mapped[VehicleYearModel] = relationship(
        back_populates = "stock_vehicles_",
        uselist = False)
    
    __table_args__ = (
        ForeignKeyConstraint(
            ["year_model_make_uid", "year_model_model_uid", "year_model_year_"], 
            ["vehicle_year_model.make_uid", "vehicle_year_model.model_uid", "vehicle_year_model.year_"],),)
    
    def __repr__(self):
        return f"VehicleStock<{self.title}>"

    @property
    def title(self):
        """Return a central 'title' for this stock vehicle. This should be a displayable text that uniquely identifies this vehicle and perhaps some of the
        more important options that separates it from the rest."""
        return f"{self.year_model_year_} {self.make.name} {self.model.name}"
    
    def set_year_model(self, make_uid, model_uid, year):
        """Set the vehicle year model to which this vehicle stock belongs."""
        self.year_model_make_uid = make_uid
        self.year_model_model_uid = model_uid
        self.year_model_year_ = year

    def set_version(self, version):
        """Set this vehicle stock's version."""
        self.version = version

    def set_badge(self, badge):
        """Set this vehicle stock's badge."""
        self.badge = badge

    def set_motor_type(self, motor_type):
        """Set this vehicle's motor type; piston, rotary, electric etc."""
        self.motor_type = motor_type
    
    def set_displacement(self, displacement_cc):
        """Set this vehicle's displacement, in CC."""
        self.displacement = displacement_cc
    
    def set_induction_type(self, induction_type):
        """Set this vehicle's induction type."""
        self.induction_type = induction_type

    def set_fuel_type(self, fuel_type):
        """Set this vehicle's fuel type."""
        self.fuel_type = fuel_type

    def set_power(self, power):
        """Set this vehicle's power amount (for electric motors.)"""
        self.power = power

    def set_electric_motor_type(self, electric_motor_type):
        """Set this vehicle's electric motor(s) type(s)."""
        self.electric_motor_type = electric_motor_type

    def set_transmission_type(self, transmission_type):
        """Set this vehicle's transmission type."""
        self.transmission_type = transmission_type

    def set_num_gears(self, num_gears):
        """Set the number of gears on this vehicle's transmission."""
        self.num_gears = num_gears


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
    

class UserPlayer(db.Model, PointGeometryMixin):
    """A model that represents a single socket session for a single User. Logically, all attributes that are dependant on the User being joined to the game world
    will now be associated with this table. At most, there can be one UserPlayer instance at any time for one User. If a User connects on a new session, any existing
    instances of this model will be deleted and replaced.
    
    This model is also a point geometry, which can contain the User's current location."""
    __tablename__ = "user_player"
    
    # Composite primary key involving the User's ID, the device's firebase installation ID and the SID. We will cascade a delete if User is deleted.
    user_id: Mapped[int] = mapped_column(ForeignKey("user_.id", ondelete = "CASCADE"), primary_key = True)
    device_fid: Mapped[str] = mapped_column(String(256), primary_key = True)
    socket_id: Mapped[str] = mapped_column(String(32), primary_key = True)
    # The ID of the vehicle currently in use by this User for the session. Currently, we don't require a vehicle for connecting to the world, so this we will set
    # this to NULL on cascade; also meaning this can be None.
    current_vehicle_id: Mapped[int] = mapped_column(ForeignKey("user_vehicle.id", ondelete = "SET NULL"), nullable = True)

    # An association proxy for the User's UID, which is the Player's UID.
    uid: AssociationProxy["User"] = association_proxy("user", "uid")
    # A timestamp, in seconds, when this session was created. Can't be None.
    created: Mapped[int] = mapped_column(BigInteger(), nullable = False, default = time.time)
    # The last time a location update was received from this User, as a timestamp in seconds. Can't be None.
    last_location_update: Mapped[int] = mapped_column(BigInteger(), nullable = False, default = time.time)

    # The vehicle currently in use by this User for the current session. Can be None.
    current_vehicle: Mapped[UserVehicle] = relationship(
        uselist = False)

    # The User currently joined to the world.
    user: Mapped["User"] = relationship(
        back_populates = "player_",
        uselist = False)
    
    # We will add a unique constraint on the User ID by itself, since we require there be only one Player instance for each User, on top of identifying a specific
    # session by all three attributes.
    __table_args__ = (
        UniqueConstraint(
            "user_id",),)
    
    def __repr__(self):
        if not self.user:
            return "UserPlayer<***NEW***>"
        return f"UserPlayer<{self.user}>"
    
    @property
    def is_playing(self):
        """Returns true if the time since last location update is under 60 seconds."""
        return time.time()-self.last_location_update < 60
    
    def set_key(self, user_id, device_fid, socket_id):
        """Set the composite key for this instance."""
        if isinstance(user_id, User):
            self.user_id = user_id.id
        else:
            self.user_id = user_id
        self.device_fid = device_fid
        self.socket_id = socket_id

    def set_current_vehicle(self, vehicle):
        """Set this Player's current vehicle."""
        self.current_vehicle = vehicle

    def clear_current_vehicle(self):
        """Clear this Player's current vehicle."""
        self.current_vehicle = None

    def set_last_location_update(self, last_timestamp_s = time.time()):
        """Set the timestamp, in seconds, when the last location update was received."""
        self.last_location_update = last_timestamp_s


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

    # The User's current game session, if any. This can be None.
    player_: Mapped[UserPlayer] = relationship(
        back_populates = "user",
        uselist = False,
        cascade = "all, delete")
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
        """Return the world Player."""
        return self.player_

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
    def current_vehicle(self):
        """Returns the Vehicle in use by this User's Player, or None if there is no Player."""
        if not self.has_player:
            return None
        return self.player.current_vehicle
    
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
    def has_player(self):
        """Returns True if this User has a Player instance."""
        return self.player_ != None
    
    @property
    def is_playing(self):
        """Returns True if the User is currently in the world. This will return True when the User currently has a socket ID set, and a location update
        has been received from them in the last minute."""
        return self.has_player and self.player_.is_playing

    def add_location(self, location):
        """Adds this location to the User's history."""
        self.location_history_.append(location)

    def set_player(self, new_player):
        """Set the current Player for this User to the one given. This function will fail if a Player is already set, so be sure to call clear players
        everytime you wish to install a new one."""
        if self.has_player:
            raise ValueError(f"Attempt to set Player for {self} failed because this User already has a Player configured.")
        # Otherwise, set the player.
        self.player_ = new_player

    def clear_player(self):
        """Clear the User's current Player. If None, nothing will happen."""
        if self.has_player:
            # If the existing player is not yet in the deleted list, delete it first.
            if not self.player_ in db.session.deleted:
                db.session.delete(self.player_)
            self.player_ = None
    
    def set_last_location_update(self, last_timestamp_s = time.time()):
        """Set the timestamp, in seconds, when the last location update was received. This will set the last location update on both this User instance,
        and if any, the current Player instance."""
        self.last_location_update = last_timestamp_s
        if self.has_player:
            self.player_.set_last_location_update(last_timestamp_s)

    def set_current_vehicle(self, vehicle):
        """Set this User's current vehicle to the one given. This function will only succeed if the User currently has a Player."""
        if not self.has_player:
            """TODO: handle this case, when User does not have a Player."""
            raise NotImplementedError("Failed to set_current_vehicle() on User - they do not have a Player.")
        self.player_.set_current_vehicle(vehicle)

    def clear_current_vehicle(self):
        """Clear the User's current Vehicle. This function will only succeed if the User currently has a Player."""
        if not self.has_player:
            """TODO: handle this case, when User does not have a Player."""
            raise NotImplementedError("Failed to clear_current_vehicle() on User - they do not have a Player.")
        self.player_.clear_current_vehicle()

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


class AnonymousUser(AnonymousUserMixin):
    """Another User model specifically for managing and tracking unauthenticated Users."""
    pass


class ServerConfiguration(db.Model):
    """A model that represents the current global configuration for the entire server."""
    __tablename__ = "server_configuration"

    id: Mapped[int] = mapped_column(primary_key = True)
    # User ID for the currently configured HawkSpeed User. Can't be None.
    user_id: Mapped[int] = mapped_column(ForeignKey("user_.id"), nullable = False)
    # The version code for the currently loaded vehicle data information. Can be None.
    vehicle_version_code: Mapped[int] = mapped_column(nullable = True)
    # The version for the currently loaded vehicle data information. Can be None.
    vehicle_version: Mapped[str] = mapped_column(String(32), nullable = True)

    # The HawkSpeed user.
    user: Mapped[User] = relationship(
        uselist = False)

    def __repr__(self):
        return f"ServerConfiguration<{self.id}>"
    
    @property
    def has_vehicle_data(self):
        """Returns True if there is a vehicle data version currently configured on this server."""
        return self.vehicle_version != None and self.vehicle_version_code != None
    
    def set_vehicle_data_version(self, version, version_code):
        self.vehicle_version = version
        self.vehicle_version_code = version_code

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
