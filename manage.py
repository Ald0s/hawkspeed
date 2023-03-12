import os
import json
import uuid
import click
import logging
import gpxpy
import time
import flask_socketio

from flask import current_app
from sqlalchemy_utils import create_database, database_exists

from app import create_app, db, config, models, error, factory

LOG = logging.getLogger("hawkspeed.manage")
LOG.setLevel( logging.DEBUG )

application = create_app()


@application.cli.command("init-db", help = "Creates the database and all tables, only if they do not exist.")
def init_db():
    if config.APP_ENV != "Production":
        # Just ensure our db is created.
        db.create_all()
    else:
        # Otherwise, check if our current database exists.
        if not database_exists(config.SQLALCHEMY_DATABASE_URI):
            create_database(config.SQLALCHEMY_DATABASE_URI)
        db.create_all()
    try:
        models.ServerConfiguration.get()
    except error.NoServerConfigurationError as nme:
        LOG.debug(f"Creating a new ServerConfiguration instance - it does not exist yet.")
        models.ServerConfiguration.new()
    db.session.flush()
    db.session.commit()


@application.cli.command("set-verified", help = "")
@click.argument("email_address")
@click.argument("verified")
def set_verified(email_address, verified):
    user = models.User.search(email_address = email_address)
    if not user:
        raise Exception(f"Failed to set verified for User ({email_address}), this user does not exist.")
    LOG.debug(f"Setting verified for {user} to {verified}")
    # Set the verified now.
    if verified == "False":
        verified = False
    else:
        verified = True
    user.set_verified(verified)
    db.session.commit()


@application.cli.command("set-enabled", help = "")
@click.argument("email_address")
@click.argument("enabled")
def set_enabled(email_address, enabled):
    user = models.User.search(email_address = email_address)
    if not user:
        raise Exception(f"Failed to set enabled for User ({email_address}), this user does not exist.")
    LOG.debug(f"Setting enabled for {user} to {enabled}")
    # Set the enabled now.
    if enabled == "False":
        enabled = False
    else:
        enabled = True
    user.set_enabled(enabled)
    db.session.commit()


@application.cli.command("create-user", help = "Create a new user, able to be used to use HawkSpeed fully.")
@click.argument("email_address")
@click.argument("username")
@click.argument("password")
@click.option("-p", "--privilege", default = models.User.PRIVILEGE_USER, is_flag = False)
@click.option("-e", "--enabled", default = True, is_flag = True)
@click.option("-v", "--verified", default = True)
def create_user(email_address, username, password, privilege, enabled, verified):
    if models.User.search(email_address = email_address):
        raise Exception(f"Failed to create a new User ({email_address}), this user already exists.")
    LOG.debug(f"Creating a new HawkSpeed user; {email_address}")
    # Create this new user.
    new_user = factory.create_user(email_address, password,
        privilege = privilege, enabled = enabled, verified = verified, username = username)
    LOG.debug(f"Created new user '{new_user}'!")
    db.session.commit()


@application.cli.command("create-user-x", help = "Create a new user, not setup.")
@click.argument("email_address")
@click.argument("password")
@click.option("-p", "--privilege", default = models.User.PRIVILEGE_USER, is_flag = False)
@click.option("-e", "--enabled", default = True, is_flag = True)
@click.option("-v", "--verified", default = True)
def create_user(email_address, password, privilege, enabled, verified):
    if models.User.search(email_address = email_address):
        raise Exception(f"Failed to create a new User ({email_address}), this user already exists.")
    LOG.debug(f"Creating a new HawkSpeed user; {email_address}")
    # Create this new user.
    new_user = factory.create_user(email_address, password,
        privilege = privilege, enabled = enabled, verified = verified)
    LOG.debug(f"Created new user '{new_user}'!")
    db.session.commit()


@application.cli.command("gpx2json", help = "Convert a GPX file to JSON.")
@click.argument("input_file")
@click.argument("output_file")
def gpx2json(input_file, output_file):
    # Start by reading the contents of the input file, as a GPX instance.
    with open(input_file, "r") as f:
        gpx_file_contents = f.read()
        gpx = gpxpy.parse(gpx_file_contents)
    # Now, get the first track.
    track = gpx.tracks[0]
    # Now, get the first segment (since we currently do not support multiple segments.)
    segment = track.segments[0]
    # Now, convert this segment to a dictionary.
    track_d = {
        "name": track.name,
        "description": track.description,
        "segments": [dict(points = [{
                "latitude": track_point.latitude,
                "longitude": track_point.longitude
            } for track_point in segment.points])]
    }
    # Now, dump this as a JSON string to the output file.
    with open(output_file, "w") as w:
        w.write(json.dumps(track_d, indent = 4))
