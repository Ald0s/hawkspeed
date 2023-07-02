"""A module for handling functionality relating to locating Users and their information points."""
import logging
import os
import random
import hashlib

from datetime import datetime, date
from sqlalchemy import func, asc, desc, delete
from sqlalchemy.orm import with_expression
from marshmallow import fields, Schema, post_load, EXCLUDE

from .compat import insert
from . import db, config, models, error

LOG = logging.getLogger("hawkspeed.users")
LOG.setLevel( logging.DEBUG )


class RequestCreateVehicle():
    """A container for a loaded request to create a new Vehicle."""
    def __init__(self, **kwargs):
        self.text = kwargs.get("text")


class RequestCreateVehicleSchema(Schema):
    """A schema for loading a request to create a new Vehicle."""
    class Meta:
        unknown = EXCLUDE
    text                    = fields.Str(required = True, allow_none = False)

    @post_load
    def request_create_vehicle_post_load(self, data, **kwargs) -> RequestCreateVehicle:
        return RequestCreateVehicle(**data)
    

def find_existing_user(**kwargs) -> models.User:
    """Locate a User with the given data in keyword arguments.
    
    Keyword arguments
    -----------------
    :user_uid: The UID for a User.
    
    Returns
    -------
    An instance of User that matches the UID."""
    try:
        user_uid = kwargs.get("user_uid", None)

        user_q = db.session.query(models.User)
        if user_uid:
            user_q = user_q\
                .filter(models.User.uid == user_uid)
        return user_q.first()
    except Exception as e:
        raise e
    

def find_vehicle_for_user(user, vehicle_uid, **kwargs) -> models.UserVehicle:
    """Locate and return a Vehicle belonging to the given User, identified by the given Vehicle UID.
    
    Arguments
    ---------
    :user: The User to which the vehicle should belong.
    :vehicle_uid: The UID under which the Vehicle has been given.
    
    Returns
    -------
    The located vehicle."""
    try:
        # Setup a query for user vehicle, filter by User's ID and by Vehicle UID.
        user_vehicle_q = db.session.query(models.UserVehicle)\
            .filter(models.UserVehicle.uid == vehicle_uid)\
            .filter(models.UserVehicle.user_id == user.id)
        # Now, return the first result.
        return user_vehicle_q.first()
    except Exception as e:
        raise e
    

def create_vehicle(request_create_vehicle, **kwargs) -> models.UserVehicle:
    """Create and return a new User Vehicle. This function will not add the Vehicle to the session, unless of course, a User is provided. In which case
    Vehicle will be added to session as a biproduct of being added to the User.
    
    Arguments
    ---------
    :request_create_vehicle: An instance of RequestCreateVehicle from which the new Vehicle should be created.

    Keyword arguments
    -----------------
    :user: Optionally provide a User to immediately add that vehicle to.

    Returns
    -------
    The UserVehicle instance."""
    try:
        user = kwargs.get("user", None)
        
        # Create a new Vehicle model.
        vehicle = models.UserVehicle()
        # Set its text.
        vehicle.set_text(request_create_vehicle.text)
        # If User has been given, add the vehicle to that User.
        if user:
            user.add_vehicle(vehicle)
        # Return vehicle.
        return vehicle
    except Exception as e:
        raise e