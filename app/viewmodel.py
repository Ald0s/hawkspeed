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

from . import db, config, models, error, tracks

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
        return [ self._transform_item(item) for item in self._pagination.items ]

    def __init__(self, _pagination, **kwargs):
        self._pagination = _pagination
        self._transform_item = kwargs.get("transform_item", lambda item: item)
        self._SerialiseViaSchemaCls = kwargs.get("SerialiseViaSchemaCls", None)

    def serialise(self, **kwargs):
        """Return a serialised list of all items in the page, via the instance of serialise_via_schema if given."""
        if self._SerialiseViaSchemaCls:
            # For each item, instantiate the serialise via schema class and then serialise each item through that.
            return [ self._SerialiseViaSchemaCls(**kwargs).dump(item) for item in self.items ]
        else:
            return [ item.serialise(**kwargs) for item in self.items ]

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
        return [ self._ViewModelCls(self._actor, self._transform_item(item), *self._extra_vm_args) for item in self._pagination.items ]

    def __init__(self, _pagination, _actor, _ViewModelCls, **kwargs):
        super().__init__(_pagination, **kwargs)
        self._actor = _actor
        self._ViewModelCls = _ViewModelCls
        self._extra_vm_args = kwargs.get("extra_vm_args", [])

    def serialise(self, **kwargs):
        """Return a serialised list of all items in the page, as view models."""
        if not issubclass(self._ViewModelCls, SerialisableMixin):
            raise TypeError(f"ViewModel class {self._ViewModelCls} is not serialisable, and therefore can't be serialised by ViewModelPagination.")
        return [ item.serialise(**kwargs) for item in self.items ]

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
        self.allow_none = True

    def _serialize(self, value, attr, obj, **kwargs):
        if value is None:
            return None
        if not isinstance(value, SerialisableMixin):
            raise Exception(f"Failed to serialise a view model, it doesn't implement SerialisableMixin; {value}")
        # Get nested serialisation keyword args.
        nested_serialisation_kwargs = value.get_nested_serialisation_kwargs() or dict()
        # Otherwise, if not many, then return value serialised.
        return value.serialise(**nested_serialisation_kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        raise NotImplementedError("serialiseViewModelField can not deserialise!")


"""ViewModels begin."""
class BaseViewModel(SerialisableMixin):
    """A base class representing an entity view model."""
    class BaseViewSchema(Schema):
        uid                 = fields.Str()

    @property
    def uid(self):
        return self.patient.uid

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


class TrackViewModel(BaseViewModel):
    """A view model that provides detail functionality specifically for Track entities."""
    class TrackPathSchema(Schema):
        """A response schema containing all points, each serialised through TrackPointSchema. This schema should have the Track model dumped through it."""
        uid                     = fields.Str(data_key = "track_uid")
        points                  = fields.List(fields.Nested(tracks.TrackPointSchema, many = False))

    class TrackViewSchema(BaseViewModel.BaseViewSchema):
        """A schema for representing the information sent back as Track detail."""
        ### First, information about the Track ###
        # The Track's name. Can't be None.
        name                = fields.Str(required = True, allow_none = False)
        # The Track's description. Can't be None.
        description         = fields.Str(required = True, allow_none = False)
        # The Track's owner. Can't be None.
        owner               = SerialiseViewModelField(required = True, allow_none = False)

        ### Second, state data. ###
        # The track's start point. Can't be None.
        start_point         = fields.Nested(tracks.TrackPointSchema, many = False, required = True, allow_none = False)
        # Is this track verified? Can't be None.
        verified            = fields.Bool(required = True, allow_none = False)
        """TODO: leaderboard"""
        """TODO: ratings"""
        """TODO: comments"""

        ### Abilities. ###
        # Can the actor race this track? Can't be None.
        can_race            = fields.Bool(required = True, allow_none = False)
        # Can the actor edit this track? Can't be None.
        can_edit            = fields.Bool(required = True, allow_none = False)
        # Can the actor delete this track? Can't be None.
        can_delete          = fields.Bool(required = True, allow_none = False)

    @property
    def name(self):
        return self.patient.name

    @property
    def description(self):
        return self.patient.description

    @property
    def owner(self):
        """Return a view model for this track's owner."""
        try:
            return UserViewModel(self.actor, self.patient.user)
        except Exception as e:
            LOG.error(e, exc_info = True)
            raise e

    @property
    def path(self):
        """Return the TrackPath object for this track."""
        return self.patient.path

    @property
    def start_point(self):
        """Return a dictionary, containing the longitude and latitude (in 4326) of the first point."""
        try:
            geodetic_point = self.patient.geodetic_point.coords[0]
            return dict(track_uid = self.uid, longitude = geodetic_point[0], latitude = geodetic_point[1])
        except Exception as e:
            LOG.error(e, exc_info = True)
            raise e

    @property
    def points(self):
        """Returns a list of dictionaries, where each entry contains track uid, longitude and a latitude."""
        try:
            # Get the multilinestring geometry from the patient entity.
            geodetic_multi_linestring = self.path.geodetic_multi_linestring
            """TODO: for now, there is only a single linestring in the multilinestring, since we only support single segment tracks."""
            return [dict(track_uid = self.uid, longitude = pt[0], latitude = pt[1]) for pt in geodetic_multi_linestring.geoms[0].coords]
        except Exception as e:
            LOG.error(e, exc_info = True)
            raise e

    @property
    def verified(self):
        return self.patient.verified

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

    def serialise(self, **kwargs):
        """Serialise and return a TrackViewSchema representing the view relationship between the actor entity and the patient Track.

        Returns
        -------
        A dumped instance of TrackViewSchema."""
        return TrackViewModel.TrackViewSchema(**kwargs).dump(self)

    def serialise_path(self, **kwargs):
        """Serialise this track's path. This will return a dumped instance of TrackPathSchema."""
        return TrackViewModel.TrackPathSchema(**kwargs).dump(self)


class UserViewModel(BaseViewModel):
    """A view model that provides profile functionality specifically for User entities."""
    class UserViewSchema(BaseViewModel.BaseViewSchema):
        """A schema for representing the information sent back as User detail when other Users query it."""
        ### First, information about the User ###
        # The user's username. Can't be None, since the User who is not setup should never be a result in any query done by other Users.
        username            = fields.Str(required = True, allow_none = False)
        # The user's privilege integer. Can never be None.
        privilege           = fields.Int(required = True, allow_none = False)

        ### Second, state data. ###
        # Whether the user's a bot or not, can never be None.
        is_bot              = fields.Bool(required = True, allow_none = False)
        # Whether this user IS the actor, can never be None.
        is_you              = fields.Bool(required = True, allow_none = False)

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
        # The user's email address. Can never be None.
        email_address       = fields.Str(required = True, allow_none = False)
        # The user's username. Can be None, if profile is not setup.
        username            = fields.Str(required = True, allow_none = True)
        # The user's privilege integer. Can never be None.
        privilege           = fields.Int(required = True, allow_none = False)

        ### Second, state data. ###
        # Whether this User's account is verified. Can't be None.
        account_verified    = fields.Bool(required = True, allow_none = False)
        # Whether this User's password is verified. Can't be None.
        password_verified   = fields.Bool(required = True, allow_none = False)
        # Whether this User is set up or not. Can't be None.
        profile_setup       = fields.Bool(required = True, allow_none = False)

        ### Abilities. ###
        # Can this User create new tracks? Can't be None.
        can_create_tracks   = fields.Bool(required = True, allow_none = False)

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
    def account_verified(self):
        return self.patient.is_account_verified

    @property
    def password_verified(self):
        return self.patient.is_password_verified

    @property
    def profile_setup(self):
        return self.patient.is_profile_setup

    @property
    def can_create_tracks(self):
        """TODO: can create tracks?"""
        return True

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
