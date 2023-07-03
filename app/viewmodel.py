"""Entity view models.
ViewModel entities that represent a requested entity from the perspective of an actor entity.
For instance, when we instantiate a UserViewModel, this is essentially the actor wishing to perform some action or view the subject of the UserViewModel."""
from __future__ import annotations

import re
import time
import logging
import sys, inspect

from functools import wraps
from datetime import datetime
from marshmallow import Schema, fields, EXCLUDE
from sqlalchemy import asc, desc
from flask_sqlalchemy import Pagination
from werkzeug.local import LocalProxy

from . import db, config, models, error, tracks, races

LOG = logging.getLogger("hawkspeed.viewmodel")
LOG.setLevel( logging.DEBUG )

__view_models__ = {}


class SerialisablePagination(Pagination):
    """A custom pagination wrapper that allows the serialisation of an SQLAlchemy flask pagination object to be directly sent
    to a paged response type object."""
    @property
    def num_in_page(self):
        """Return the number of items in the current page."""
        if not self._pagination.items:
            return 0
        return len(self._pagination.items)

    @property
    def has_next(self):
        """Return internal has_next."""
        return self._pagination.has_next

    @property
    def next_num(self):
        """Return internal next_num."""
        return self._pagination.next_num

    @property
    def page(self):
        """Return internal page."""
        return self._pagination.page

    @property
    def items(self):
        """Return a list of each internal item. If there are no items, an empty list is returned."""
        if not self._pagination.items:
            return []
        return [self._transform_item(item) for item in self._pagination.items]

    def __init__(self, _pagination, **kwargs):
        self._pagination = _pagination
        self._transform_item = kwargs.get("transform_item", lambda item: item)
        self._SerialiseViaSchemaCls = kwargs.get("SerialiseViaSchemaCls", None)

    def serialise(self, **kwargs):
        """Return a serialised list of all items in the page, via the instance of serialise_via_schema if given."""
        if self._SerialiseViaSchemaCls:
            # For each item, instantiate the serialise via schema class and then serialise each item through that.
            return [self._SerialiseViaSchemaCls(**kwargs).dump(item) for item in self.items]
        else:
            return [item.serialise(**kwargs) for item in self.items]

    def as_paged_response(self, base_dict = dict()):
        """Returns this class as a page dto containing the serialised items, the current page number and the next page number.
        Optionally, a dictionary instance can be provided to instead augment with the page attributes, otherwise a blank dict will be started with."""
        return {
            **dict(
                items = self.serialise(),
                this_page = self.page,
                next_page = self.next_num),
            **base_dict
        }

    @classmethod
    def make(cls, pagination, **kwargs):
        """Make and return a serialisation pagination wrapper object.

        Arguments
        ---------
        :pagination: A Pagination type defined by Flask-SQLAlchemy.

        Keyword arguments
        -----------------
        :transform_item: A lambda function that, if given, will be applied to each item within the Pagination object prior to being serialised.
        :SerialiseViaSchemaCls: A Schema instance that, if given, each instance of pagination items should be serialised through."""
        transform_item = kwargs.get("transform_item", lambda item: item)
        SerialiseViaSchemaCls = kwargs.get("SerialiseViaSchemaCls", None)

        """TODO: validate each var here."""
        return SerialisablePagination(pagination,
            transform_item = transform_item, SerialiseViaSchemaCls = SerialiseViaSchemaCls)


class ViewModelPagination(SerialisablePagination):
    """A ViewModel based pagination wrapper for the SQLAlchemy flask's Pagination object. This functions identically to the serialisable pagination
    wrapper, but does so with the concept of the view model in mind."""
    @property
    def items(self):
        """Return a list of each internal item mapped to a typed view model with the given actor. If there are no items, an empty list is returned."""
        if not self._pagination.items:
            return []
        return [self._ViewModelCls(self._actor, self._transform_item(item), *self._extra_vm_args) for item in self._pagination.items]

    def __init__(self, _pagination, _actor, _ViewModelCls, **kwargs):
        super().__init__(_pagination, **kwargs)
        self._actor = _actor
        self._ViewModelCls = _ViewModelCls
        self._extra_vm_args = kwargs.get("extra_vm_args", [])

    def serialise(self, **kwargs):
        """Return a serialised list of all items in the page, as view models."""
        if not issubclass(self._ViewModelCls, SerialisableMixin):
            raise TypeError(f"ViewModel class {self._ViewModelCls} is not serialisable, and therefore can't be serialised by ViewModelPagination.")
        return [item.serialise(**kwargs) for item in self.items]

    @classmethod
    def make(cls, pagination, actor, ViewModelCls, **kwargs):
        """Make and return a view model pagination wrapper object.

        Arguments
        ---------
        :pagination: A Pagination type defined by Flask-SQLAlchemy.
        :actor: An instance of the actor to use in the view model relationship for each item in the page.
        :ViewModelCls: The ViewModel type to instantiate each item as.

        Keyword arguments
        -----------------
        :extra_vm_args: A list of items to be spread across the constructor for the given view model class, at the end.
        :transform_item: A lambda function that, if given, will be applied to each item within the Pagination object prior to being serialised."""
        transform_item = kwargs.get("transform_item", lambda item: item)
        extra_vm_args = kwargs.get("extra_vm_args", [])
        """TODO: validate each var here."""
        if not pagination or not actor or not ViewModelCls:
            raise Exception("ViewModelPagination() failed; actor, pagination and ViewModelCls must be given.")
        return ViewModelPagination(pagination, actor, ViewModelCls,
            transform_item = transform_item, extra_vm_args = extra_vm_args)


class ViewModelList():
    """A custom wrapper that allows the serialisation of a list of view models."""
    @property
    def num_items(self):
        """Return the number of items in this list."""
        return len(self._items)

    @property
    def items(self):
        """Return a list of each internal item mapped to a typed view model with the given actor. If there are no items, an empty list is returned."""
        if not self._items:
            return []
        return [self._ViewModelCls(self._actor, self._transform_item(item), *self._extra_vm_args) for item in self._items]

    def __init__(self, _items, _actor, _ViewModelCls, **kwargs):
        self._items = _items
        self._actor = _actor
        self._ViewModelCls = _ViewModelCls

        self._extra_vm_args = kwargs.get("extra_vm_args", [])
        self._transform_item = kwargs.get("transform_item", lambda item: item)

    def serialise(self, **kwargs):
        """Return a serialised list of all items in the list, as view models."""
        if not issubclass(self._ViewModelCls, SerialisableMixin):
            raise TypeError(f"ViewModel class {self._ViewModelCls} is not serialisable, and therefore can't be serialised by {type(self)}.")
        return [item.serialise(**kwargs) for item in self.items]

    def as_dict(self, base_dict = dict(), **kwargs):
        """Returns this class as a dto containing the serialised items. Optionally, a dictionary instance can be provided to instead augment with the other attributes,
        otherwise a blank dict will be started with."""
        return {
            **dict(
                items = self.serialise(**kwargs)),
            **base_dict
        }

    @classmethod
    def make(cls, items, actor, ViewModelCls, **kwargs):
        """Make and return a view model list wrapper object.

        Arguments
        ---------
        :items: A list of items type defined by Flask-SQLAlchemy, each of which will be the patient in a view model.
        :actor: An instance of the actor to use in the view model relationship for each item in the list.
        :ViewModelCls: The ViewModel type to instantiate each item as.

        Keyword arguments
        -----------------
        :extra_vm_args: A list of items to be spread across the constructor for the given view model class, at the end.
        :transform_item: A lambda function that, if given, will be applied to each item within the list prior to being serialised."""
        transform_item = kwargs.get("transform_item", lambda item: item)
        extra_vm_args = kwargs.get("extra_vm_args", [])
        """TODO: validate each var here."""
        if items == None or not actor or not ViewModelCls:
            raise Exception("ViewModelList() failed; actor, items and ViewModelCls must be given.")
        return ViewModelList(items, actor, ViewModelCls,
            transform_item = transform_item, extra_vm_args = extra_vm_args)
    
    
class SerialisableMixin():
    """Makes an implementing type able to be serialised.
    This usually refers to serialization through a schema that presents the maximum amount of data available."""
    def serialise(self, **kwargs):
        raise NotImplementedError(f"SerialisableMixin::serialise() not implemented on {self}")

    def get_nested_serialisation_kwargs(self):
        """When a serialisable subclass overrides this, a dictionary should be returned.
        This dictionary will be passed to each instance of the serialisable's schema when its being serialised. This is how we will stop infinite loops."""
        return dict()


class SerialiseViewModelField(fields.Field):
    """Field that only serialises objects that implement SerialisableMixin, the resulting value is the return of calling serialise()
    on the object being serialised."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dump_only = True

    def _serialize(self, value, attr, obj, **kwargs):
        if value is None and not self.allow_none:
            """TODO: implement properly."""
            raise NotImplementedError("Failed to serialise view model, allow_none is False yet the given view model is None.")
        if not isinstance(value, SerialisableMixin):
            raise TypeError(f"Failed to serialise a view model, it doesn't implement SerialisableMixin; {value}")
        # Get nested serialisation keyword args.
        nested_serialisation_kwargs = value.get_nested_serialisation_kwargs() or dict()
        # Otherwise, if not many, then return value serialised.
        return value.serialise(**nested_serialisation_kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        raise NotImplementedError("SerialiseViewModelField can not deserialise!")


class SerialiseViewModelListField(fields.Field):
    """Field that only serialises ViewModelList objects."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dump_only = True
        self.base_dict = kwargs.get("base_dict", dict())

    def _serialize(self, value, attr, obj, **kwargs):
        if value is None and not self.allow_none:
            """TODO: implement properly."""
            raise NotImplementedError("Failed to serialise view model list, allow_none is False yet the given view model list is None.")
        if not isinstance(value, ViewModelList):
            raise TypeError(f"Failed to serialise a view model list, the given value is not an instance of ViewModelList; {value}")
        # Get the value as dict, supplying the base dictionary given by ctor.
        value_as_dict = value.as_dict(self.base_dict)
        # If base dict is an empty dictionary, there are no additional arguments, so there's no need for the encapsulating items object, return the contents
        # of the items key. Otherwise, return this value.
        if not self.base_dict:
            return value_as_dict["items"]
        return value_as_dict

    def _deserialize(self, value, attr, data, **kwargs):
        raise NotImplementedError("SerialiseViewModelListField can not deserialise!")
    

"""ViewModels begin."""
class BaseViewModel(SerialisableMixin):
    """A base class representing an entity view model."""
    class BaseViewSchema(Schema):
        pass

    @property
    def can_view(self):
        """This needs to be implemented in each subtype to provide the single source of truth as to whether the actor is
        allowed, at minimum, to view the view model instance. This does not refer to or protect any specific data points
        within the view model.

        By default, we can view."""
        return True

    def __init__(self, _actor, _patient, **kwargs):
        """Instantiate the base functionality for a view model. At least, you must supply the actor entity and patient entity. Both actor and entity can be view models
        themselves and, in this case, the actor/patient entities respectively will be used."""
        if isinstance(_actor, BaseViewModel):
            self.actor = _actor.actor
        else:
            self.actor = _actor
        if isinstance(_patient, BaseViewModel):
            self.patient = _patient.patient
        else:
            self.patient = _patient

    def _refresh_view_model(self):
        """"""
        pass

    @classmethod
    def get_model_class(cls):
        """Return the Model class this view model represents.
        For example, calling this on UserViewModel should return User."""
        raise NotImplementedError(f"get_model_class() not implemented on {cls}")


class VehicleViewModel(BaseViewModel):
    """A view model for representing a specific User's vehicle."""
    class VehicleViewSchema(BaseViewModel.BaseViewSchema):
        """A schema for dumping the state of a User's vehicle."""
        class Meta:
            unknown = EXCLUDE
        ### First some info about the Vehicle. ###
        # The Vehicle's UID. Can't be None.
        uid                 = fields.Str(required = True, allow_none = False)
        # The Vehicle's text. Can't be None.
        text                = fields.Str(required = True, allow_none = False)
        # Does this vehicle belong to the actor? Can't be None.
        belongs_to_you      = fields.Bool(required = True, allow_none = False)

    @property
    def uid(self):
        """Returns the Vehicle's UID."""
        return self.patient.uid
    
    @property
    def title(self):
        """Returns the Vehicle's title."""
        return self.patient.title
    
    @property
    def text(self):
        """Returns the Vehicle's text."""
        return self.patient.text
    
    @property
    def belongs_to_you(self):
        """Returns True if the vehicle belongs to the actor."""
        return self.actor == self.patient.user
    
    def serialise(self, **kwargs):
        """Serialise and return this vehicle view model."""
        schema = VehicleViewModel.VehicleViewSchema(**kwargs)
        return schema.dump(self)


class LeaderboardEntryViewModel(BaseViewModel):
    """A view model for representing a specific user's race outcome as a leaderboard entry. We'll use a view model for this to allow users to like and
    comment on entries."""
    class LeaderboardEntryViewSchema(Schema):
        """A schema representing a single TrackUserRace outcome for this track."""
        class Meta:
            unknown = EXCLUDE
        # The track user race's UID that this leaderboard belongs to. Can't be None.
        race_uid            = fields.Str(required = True, allow_none = False)
        # The place held by this race result. Can't be None.
        finishing_place     = fields.Int(required = True, allow_none = False)
        # A timestamp, in milliseconds, when the race began. Can't be None.
        started             = fields.Int(required = True, allow_none = False)
        # A timestamp, in milliseconds, when the race ended. Can't be None.
        finished            = fields.Int(required = True, allow_none = False)
        # The total recorded time, in milliseconds, for this race. Can't be None.
        stopwatch           = fields.Int(required = True, allow_none = False)
        # The Player that completed this race. Can't be None.
        player              = SerialiseViewModelField(required = True, allow_none = False)
        # The vehicle this Player used to complete the race. Can't be None.
        vehicle             = SerialiseViewModelField(required = True, allow_none = False)
        # The UID for the Track that was raced on. Can't be None.
        track_uid           = fields.Str(required = True, allow_none = False)

    @property
    def race_uid(self):
        """The race's UID."""
        return self.patient.uid

    @property
    def finishing_place(self):
        """Return the finishing place for this leaderboard entry."""
        return self.patient.finishing_place
    
    @property
    def started(self):
        """When this race started, in milliseconds."""
        return self.patient.started

    @property
    def finished(self):
        """When this race finished, in milliseconds."""
        return self.patient.finished

    @property
    def stopwatch(self):
        """The duration of this race, in milliseconds."""
        # If race is not ongoing and not finished, return -1 from this function.
        if not self.patient.is_ongoing and not self.patient.is_finished:
            return -1
        return self.patient.stopwatch

    @property
    def vehicle(self) -> VehicleViewModel:
        """Returns a vehicle view model for the Vehicle used in the race."""
        return VehicleViewModel(self.actor, self.patient.vehicle)
    
    @property
    def player(self) -> UserViewModel:
        """A view model for the Player who raced."""
        return UserViewModel(self.actor, self.patient.user)

    @property
    def track_uid(self):
        """The track's UID."""
        return self.patient.track_uid

    def serialise(self, **kwargs):
        """Serialise and return a LeaderboardEntryViewSchema representing the view relationship between the actor entity and the patient TrackUserRace.

        Returns
        -------
        A dumped instance of LeaderboardEntryViewSchema."""
        return LeaderboardEntryViewModel.LeaderboardEntryViewSchema(**kwargs).dump(self)


class TrackPathViewModel(BaseViewModel):
    """A view model that provides functionality for a Track's path."""
    class TrackPathViewSchema(BaseViewModel.BaseViewSchema):
        """A schema that can be used to serialise a track's path."""
        class Meta:
            unknown = EXCLUDE
        # The owning Track's UID. Can't be None.
        track_uid           = fields.Str(allow_none = False)
        # The track path's CRS. Can't be None.
        crs                 = fields.Int(allow_none = False)
        # All points belonging to this track's path.
        points              = fields.List(fields.Nested(tracks.TrackPointSchema, many = False), allow_none = False)

    @property
    def track_uid(self):
        return self.patient.uid
    
    @property
    def crs(self):
        """Return the CRS this track path is currently in."""
        """TODO: improve this, it does not actually return the geodetic CRS, just the normal one for storage."""
        return self.patient.crs
    
    @property
    def points(self):
        """Returns a list of dictionaries, where each entry contains track uid, longitude and a latitude."""
        """TODO: improve this."""
        try:
            # Get the multi line string geometry (geodetic) from the patient entity.
            geodetic_multi_linestring = self.patient.geodetic_multi_linestring
            """TODO: for now, there is only a single linestring in the multilinestring, since we only support single segment tracks."""
            geodetic_linestring = geodetic_multi_linestring.geoms[0]
            # Create a list of dictionaries where each entry is a track point.
            return [dict(
                track_uid = self.track_uid, longitude = pt[0], latitude = pt[1]
            ) for pt in geodetic_linestring.coords]
        except Exception as e:
            LOG.error(e, exc_info = True)
            raise e
    
    def serialise(self, **kwargs):
        """Serialise and return this track path view model."""
        schema = self.TrackPathViewSchema(**kwargs)
        return schema.dump(self)
    

class TrackCommentViewModel(BaseViewModel):
    """A view model that provides control over a specific comment posted toward a track."""
    class TrackCommentViewSchema(BaseViewModel.BaseViewSchema):
        """A schema for dumping the state of a track comment."""
        class Meta:
            unknown = EXCLUDE
        ### First, information about the Comment ###
        # This comment's UID. Can't be None.
        uid                 = fields.Str(required = True, allow_none = False)
        # When this comment was created. Can't be None.
        created             = fields.Int(required = True, allow_none = False)
        # This comment's text. Can't be None.
        text                = fields.Str(required = True, allow_none = False)
        # The Track's UID. Can't be None.
        track_uid           = fields.Str(required = True, allow_none = False)

        ### Now some objects ###
        # The User that created this comment. Can't be None.
        user                = SerialiseViewModelField(allow_none = False)
    
    @property
    def uid(self):
        """Return the comment's UID."""
        return self.patient.uid
    
    @property
    def created(self):
        """Return the timestamp, in seconds, when this comment was created."""
        return self.patient.created
    
    @property
    def text(self):
        """Return this comment's text."""
        return self.patient.text 
    
    @property
    def user(self) -> UserViewModel:
        """Return a user view model for the User that created this comment."""
        return UserViewModel(self.actor, self.patient.user)
    
    @property
    def track_uid(self):
        """Return the Track UID for the track this comment is posted toward."""
        return self.patient.track_uid
    
    @property
    def can_edit(self):
        """Returns True only if the actor is the User that created the Comment."""
        return self.actor == self.patient.user
    
    @property
    def can_delete(self):
        """Returns True only if the actor is the User that created the Comment."""
        return self.actor == self.patient.user

    def serialise(self, **kwargs):
        """Serialise and return this track view model."""
        schema = self.TrackCommentViewSchema(**kwargs)
        return schema.dump(self)
    
    def edit(self, request_comment, **kwargs) -> TrackCommentViewModel:
        """"""
        try:
            if not self.can_edit:
                """TODO: handle this action not allowed here."""
                raise NotImplementedError("Failed to edit comment due to permissions - not handled.")
            raise NotImplementedError()
        except Exception as e:
            raise e
        
    def delete(self, **kwargs):
        """"""
        try:
            if not self.can_delete:
                """TODO: handle this action not allowed here."""
                raise NotImplementedError("Failed to delete comment due to permissions - not handled.")
            raise NotImplementedError()
        except Exception as e:
            raise e
    

class TrackViewModel(BaseViewModel):
    """A view model that provides detail functionality specifically for Track entities.""" 
    class TrackViewSchema(BaseViewModel.BaseViewSchema):
        """A schema for representing the information sent back as Track detail."""
        ### First, information about the Track ###
        # The track's UID. Can't be None.
        uid                 = fields.Str(equired = True, allow_none = False)
        # The Track's name. Can't be None.
        name                = fields.Str(required = True, allow_none = False)
        # The Track's description. Can't be None.
        description         = fields.Str(required = True, allow_none = False)
        # The Track's owner. Can't be None.
        owner               = SerialiseViewModelField(required = True, allow_none = False)
        # The top three entries on this track's leaderboard. Can't be None.
        top_leaderboard     = SerialiseViewModelListField(required = True, allow_none = False)

        ### Second, state data. ###
        # The track's start point. Can't be None.
        start_point         = fields.Nested(tracks.TrackPointSchema, many = False, required = True, allow_none = False)
        # Is this track verified? Can't be None.
        is_verified         = fields.Bool(required = True, allow_none = False)
        # What type if this track? Can't be None.
        track_type          = fields.Int(required = True, allow_none = False)
        # This track's ratings. Can't be None.
        ratings             = fields.Nested(tracks.RatingsSchema, many = False, allow_none = False)
        # The actor's disposition toward the Track, can be None; which means the actor has not voted.
        your_rating         = fields.Bool(required = True, allow_none = True)
        # The number of comments on this Track. Can't be None.
        num_comments        = fields.Int(required = True, allow_none = False)

        ### Abilities. ###
        # Is the actor approved to race this track? Can't be None.
        can_race            = fields.Bool(required = True, allow_none = False)
        # Can the actor edit this track? Can't be None.
        can_edit            = fields.Bool(required = True, allow_none = False)
        # Can the actor delete this track? Can't be None.
        can_delete          = fields.Bool(required = True, allow_none = False)
        # Can the actor comment on this track? Can't be None.
        can_comment         = fields.Bool(required = True, allow_none = False)

    @property
    def uid(self):
        return self.patient.uid
    
    @property
    def name(self):
        """Return the track's name."""
        return self.patient.name

    @property
    def description(self):
        """Return the track's description."""
        return self.patient.description

    @property
    def owner(self):
        """Return a view model for this track's owner. If there is no owner, this will return None."""
        if not self.patient.has_owner:
            return None
        return UserViewModel(self.actor, self.patient.user)
    
    @property
    def top_leaderboard(self) -> ViewModelList:
        """Return a view model list of the top three entries in this track's leaderboard."""
        top_leaderboard_entries = tracks.leaderboard_query_for(self.patient)\
            .limit(3)\
            .all()
        return ViewModelList.make(top_leaderboard_entries, self.actor, LeaderboardEntryViewModel)

    @property
    def path(self) -> TrackPathViewModel:
        """Return a track path view model for this track's path."""
        return TrackPathViewModel(self.actor, self.patient.path)

    @property
    def start_point(self):
        """Return a dictionary, containing the longitude and latitude (in 4326) of the first point."""
        """TODO: improve this, return an object instead of a dictionary."""
        try:
            geodetic_point = self.patient.geodetic_point.coords[0]
            return dict(track_uid = self.uid, longitude = geodetic_point[0], latitude = geodetic_point[1])
        except Exception as e:
            print("Start point failure")
            LOG.error(e, exc_info = True)
            raise e

    @property
    def is_verified(self):
        """Return True if this track is verified."""
        return self.patient.is_verified

    @property
    def track_type(self):
        """Returns the type of this track."""
        return self.patient.track_type
    
    @property
    def ratings(self):
        """Returns a Ratings object for this Track."""
        return tracks.get_ratings_for(self.patient)
    
    @property
    def your_rating(self):
        """Returns True if the actor has voted for this Track, or False otherwise. None is returned if no vote has been placed."""
        return tracks.get_user_rating(self.patient, self.actor)
    
    @property
    def num_comments(self):
        """Returns the number of comments on this Track."""
        return self.patient.num_comments

    @property
    def can_race(self):
        """TODO"""
        return True

    @property
    def can_edit(self):
        """TODO"""
        return False

    @property
    def can_delete(self):
        """TODO"""
        return False
    
    @property
    def can_comment(self):
        """Returns True if the actor can comment on this Track. The only requirement is that the actor has completed this track at least once."""
        return tracks.has_user_finished(self.patient, self.actor)

    def serialise(self, **kwargs):
        """Serialise and return a TrackViewSchema representing the view relationship between the actor entity and the patient Track.

        Returns
        -------
        A dumped instance of TrackViewSchema."""
        return TrackViewModel.TrackViewSchema(**kwargs).dump(self)

    def rate(self, request_rating, **kwargs):
        """Set the rating of this track from the perspective of the actor to the given request.
        
        Arguments
        ---------
        :request_rating: An instance of RequestRating."""
        try:
            tracks.set_user_rating(self.patient, self.actor, request_rating)
        except Exception as e:
            raise e
        
    def clear_rating(self, **kwargs):
        """Clear any rating from the current actor toward this track."""
        try:
            tracks.clear_user_rating(self.patient, self.actor)
        except Exception as e:
            raise e
    
    def comment(self, request_comment, **kwargs) -> TrackCommentViewModel:
        """Perform a comment toward this track with the given comment content. If successful, this function will return a new track comment view model
        referring to the track comment.
        
        Arguments
        ---------
        :request_comment: An instance of RequestComment.
        
        Returns
        -------
        A TrackComment view model."""
        try:
            if not self.can_comment:
                """TODO: handle this permissions issue properly."""
                raise NotImplementedError("User attempted to comment on a track but is not allowed to. Also this isn't handled.")
            raise NotImplementedError()
        except Exception as e:
            raise e
        
    def find_comment(self, comment_uid, **kwargs) -> TrackCommentViewModel:
        """Search for and return a track comment view model for the desired comment, that should be posted toward this track. If no comment can be found,
        this function will fail with a ValueError. This function will not ensure actor is able to perform any actions.
        
        Arguments
        ---------
        :comment_uid: A track comment's UID.
        
        Returns
        -------
        An instance of TrackCommentViewModel."""
        try:
            # Get the comment.
            track_comment = tracks.get_track_comment(self.patient, comment_uid)
            # If none is returned, raise a value error.
            if not track_comment:
                raise ValueError
            # Otherwise, return a track comment view model.
            return TrackCommentViewModel(self.actor, track_comment)
        except Exception as e:
            raise e

    def page_leaderboard(self, page = 1, **kwargs) -> ViewModelPagination:
        """Page the leaderboard for this track. This will query a list of of TrackUserRace instances from this track, ordered to reflect fastest to slowest.
        This function will return a ViewModelPagination instance for the requested page.

        Arguments
        ---------
        :page: The page to get from the leaderboard.

        Returns
        -------
        A ViewModelPagination object."""
        try:
            return ViewModelPagination.make(
                tracks.leaderboard_query_for(self.patient).paginate(
                    page = page, per_page = config.PAGE_SIZE_LEADERBOARD, max_per_page = config.PAGE_SIZE_LEADERBOARD, error_out = False),
                self.actor,
                LeaderboardEntryViewModel)
        except Exception as e:
            raise e
        
    def page_comments(self, page = 1, **kwargs) -> ViewModelPagination:
        """Page the comments for this track. This will query a list of of TrackComment instances from this track.
        This function will return a ViewModelPagination instance for the requested page.

        Arguments
        ---------
        :page: The page to get from the comments section.

        Returns
        -------
        A ViewModelPagination object."""
        try:
            return ViewModelPagination.make(
                tracks.comments_query_for(self.patient).paginate(
                    page = page, per_page = config.PAGE_SIZE_COMMENTS, max_per_page = config.PAGE_SIZE_COMMENTS, error_out = False),
                self.actor,
                TrackCommentViewModel)
        except Exception as e:
            raise e


class UserViewModel(BaseViewModel):
    """A view model that provides profile functionality specifically for other User entities."""
    class UserViewSchema(BaseViewModel.BaseViewSchema):
        """A schema for representing the information sent back as User detail when other Users query it."""
        ### First, information about the User ###
        # The user's UID. Can't be None.
        uid                 = fields.Str(required = True, allow_none = False)
        # The user's username. Can't be None, since the User who is not setup should never be a result in any query done by other Users.
        username            = fields.Str(required = True, allow_none = False)
        # The user's privilege integer. Can never be None.
        privilege           = fields.Int(required = True, allow_none = False)

        ### Second, state data. ###
        # Whether the user's a bot or not, can never be None.
        is_bot              = fields.Bool(required = True, allow_none = False)
        # Whether this user IS the actor, can never be None.
        is_you              = fields.Bool(required = True, allow_none = False)
        # Whether the user's currently playing.
        is_playing          = fields.Bool(required = True, allow_none = False)

    @property
    def uid(self):
        return self.patient.uid
    
    @property
    def username(self):
        return self.patient.username

    @property
    def privilege(self):
        return self.patient.privilege

    @property
    def is_bot(self):
        return self.patient.is_bot

    @property
    def is_you(self):
        # This is us if actor matches patient.
        return self.actor == self.patient

    @property
    def is_playing(self):
        """Returns True if the User is currently playing. That is, is connected to the game."""
        return self.patient.is_playing
    
    def serialise(self, **kwargs):
        """Serialise and return a UserViewSchema representing the view relationship between the actor entity and the patient User.

        Returns
        -------
        A dumped instance of UserViewSchema."""
        return UserViewModel.UserViewSchema(**kwargs).dump(self)

    def get_nested_serialisation_kwargs(self):
        return dict( exclude = () )


class AccountViewModel(BaseViewModel):
    """A view model that provides profile functionality specifically for a User entity being viewed for management purposes by its owner."""
    class AccountViewSchema(BaseViewModel.BaseViewSchema):
        """A schema for representing the information sent back as detail to be managed."""
        ### First, information about the User ###
        # The user's UID. Can't be None.
        uid                 = fields.Str(required = True, allow_none = False)
        # The user's email address. Can never be None.
        email_address       = fields.Str(required = True, allow_none = False)
        # The user's username. Can be None, if profile is not setup.
        username            = fields.Str(required = True, allow_none = True)
        # The user's privilege integer. Can never be None.
        privilege           = fields.Int(required = True, allow_none = False)

        ### Second, state data. ###
        # Whether this User's account is verified. Can't be None.
        is_account_verified = fields.Bool(required = True, allow_none = False)
        # Whether this User's password is verified. Can't be None.
        is_password_verified= fields.Bool(required = True, allow_none = False)
        # Whether this User is set up or not. Can't be None.
        is_profile_setup    = fields.Bool(required = True, allow_none = False)

        ### Abilities. ###
        # Can this User create new tracks? Can't be None.
        can_create_tracks   = fields.Bool(required = True, allow_none = False)

    @property
    def uid(self):
        return self.patient.uid
    
    @property
    def email_address(self):
        return self.patient.email_address

    @property
    def username(self):
        return self.patient.username

    @property
    def privilege(self):
        return self.patient.privilege

    @property
    def is_account_verified(self):
        return self.patient.is_account_verified

    @property
    def is_password_verified(self):
        return self.patient.is_password_verified

    @property
    def is_profile_setup(self):
        return self.patient.is_profile_setup

    @property
    def can_create_tracks(self):
        """TODO: can create tracks?"""
        return True
    
    @property
    def vehicles(self) -> ViewModelList:
        """Return a new view model list for all Vehicles belonging to this User."""
        return ViewModelList.make(self.patient.all_vehicles, self.actor, VehicleViewModel)

    def __init__(self, _user, **kwargs):
        """The account view model only focuses on User to User view, for the same User."""
        super().__init__(_user, _user, **kwargs)

    def serialise(self, **kwargs):
        """Serialise and return a AccountViewSchema.

        Returns
        -------
        A dumped instance of AccountViewSchema."""
        return AccountViewModel.AccountViewSchema(**kwargs).dump(self)

    def get_nested_serialisation_kwargs(self):
        return dict( exclude = () )

    def create_track(self, new_track_json, **kwargs) -> TrackViewModel:
        """Create a track from the given loaded track instance. This should be typeof LoadedTrack found in the tracks module. This function will raise an error
        if the User is not able to actually create tracks.

        Arguments
        ---------
        :new_track_json: A JSON object containing all attributes in LoadTrackSchema.

        Returns
        -------
        A TrackViewModel."""
        try:
            # Check if the User is able to create tracks. Raise an exception if not.
            if not self.can_create_tracks:
                """TODO: implement this properly."""
                raise NotImplementedError("Failed to create_track, user is not allowed to; but this is NOT handled.")
            """
            TODO: for now, is_verified is True by default since we have no validation procedures.
            """
            # Otherwise, use the tracks module to load this JSON object. Expect back a full Track model.
            new_track = tracks.create_track_from_json(self.actor, new_track_json,
                is_verified = True)
            # We get this track back. Now, create a new TrackViewModel for it, and return that.
            track_view_model = TrackViewModel(self.actor, new_track)
            return track_view_model
        except Exception as e:
            raise e
