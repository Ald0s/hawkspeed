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
        if user_uid == None:
            return None
        user_q = db.session.query(models.User)
        if user_uid:
            user_q = user_q\
                .filter(models.User.uid == user_uid)
        return user_q.first()
    except Exception as e:
        raise e


def update_profile_media(media, **kwargs):
    """"""
    try:
        raise NotImplementedError()
    except Exception as e:
        raise e


def update_cover_media(media, **kwargs):
    """"""
    try:
        raise NotImplementedError()
    except Exception as e:
        raise e