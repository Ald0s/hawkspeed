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

from app import create_app, db, config, models, decorators, error, factory, tracks, vehicles, races
from app.tasks import roadsapi

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


@application.cli.command("import-vehicle-data", help = "Imports all vehicle data.")
@decorators.get_server_configuration()
def import_vehicle_data(server_configuration, **kwargs):
    # Simply load vehicle data from the vehicles JSON.
    vehicles.load_vehicle_data_from("vehicles.json",
        server_configuration = server_configuration)
    # Commit.
    db.session.commit()


@application.cli.command("repair-stuck-races", help = "This should be called as part of every startup sequence. This will properly cancel all races from previous run.")
@decorators.get_server_configuration()
def repair_stuck_races(server_configuration, **kwargs):
    """It's critical to call this function on every single launch. This will set all races currently ongoing to the 'cancelled' state."""
    races.cancel_ongoing_races()
    # Commit.
    db.session.commit()


@application.cli.command("import-gpx-routes", help = "Imports all routes stored in the GPX routes directory.")
@decorators.get_server_configuration()
def import_gpx_routes(server_configuration, **kwargs):
    # Get the absolute path to the GPX routes directory.
    absolute_gpx_routes_dir = os.path.join(os.getcwd(), config.GPX_ROUTES_DIR)
    # Now, list all of the files in this directory and loop them.
    for filename in os.listdir(absolute_gpx_routes_dir):
        # Otherwise, get the file's extension, stripped of the dot.
        file_ext = os.path.splitext(filename)[1].strip(".").lower()
        # If this is not a 'gpx', continue.
        if file_ext != "gpx":
            continue
        try:
            # Otherwise, attempt an import of the track via the tracks module. Shield call from track already exists.
            # We'll get back a created track object, which contains the new track. We'll then set the owner of this track to the hawkspeed User.
            created_track = tracks.create_track_from_gpx(filename)
            created_track.track.set_owner(server_configuration.user)
            db.session.flush()
        except (tracks.TrackInspectionFailed, tracks.TrackAlreadyExists) as tae:
            continue
    # Now that we've created tracks, commit.
    db.session.commit()


@application.cli.command("show-users", help = "")
def show_users():
    users = db.session.query(models.User)\
        .all()
    for user in users:
        print(f"{user}: {user.username}")


@application.cli.command("show-tracks", help = "")
def show_tracks():
    tracks = db.session.query(models.Track)\
        .all()
    for track in tracks:
        print(f"{track}: {track.uid}")


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
@click.argument("password")
@click.option("-u", "--username", default = None, type = str)
@click.option("-d", "--drive", default = None, type = str)
@click.option("-p", "--privilege", default = models.User.PRIVILEGE_USER, is_flag = False)
@click.option("-e", "--enabled", default = True, is_flag = True)
@click.option("-v", "--verified", default = True)
def create_user(email_address, password, username, drive, privilege, enabled, verified):
    if models.User.search(email_address = email_address):
        raise Exception(f"Failed to create a new User ({email_address}), this user already exists.")
    LOG.debug(f"Creating a new HawkSpeed user; {email_address}")
    # Create this new user.
    new_user = factory.create_user(email_address, password,
        privilege = privilege, enabled = enabled, verified = verified, username = username, vehicle = drive)
    LOG.debug(f"Created new user '{new_user}'!")
    db.session.commit()


@application.cli.command("add-fake-attempt", help = "Create a fake race attempt on the given track, by the given User; for the given time, in milliseconds.")
@click.argument("track_uid")
@click.argument("username")
@click.argument("started")
@click.argument("finished")
def add_fake_race_attempt(track_uid, username, started, finished):
    # Find User.
    user = models.User.search(username = username)
    if not user:
        raise Exception(f"Failed to add fake race attempt, no user with username {username} could be found.")
    # Ensure the user has at least one vehicle.
    if not user.num_vehicles:
        raise Exception(f"Failed to add fake race attempt for {user}, that user has no vehicles.")
    # Find Track.
    track = tracks.find_existing_track(track_uid = track_uid)
    if not track:
        raise Exception(f"Failed to add fake race attempt for {user}, the requested track with UID {track_uid} does not exist.")
    if finished < started:
        raise Exception(f"Failed to add fake race attempt for {user}, the given time starts after its supposed to finish.")
    track_user_race = models.TrackUserRace(
            started = started)
    track_user_race.set_fake(True)
    track_user_race.set_finished(finished)
    track_user_race.set_vehicle(user.vehicles.first())
    track_user_race.set_track_and_user(track, user)
    db.session.add(track_user_race)
    db.session.commit()
    LOG.debug(f"Added fake race attempt for {user}; {track_user_race}")


@application.cli.command("assimgpx", help = "Convert an erroneous GPX file to HawkSpeed GPX. If no output given, input file overwritten.")
@click.argument("input_file", type = click.Path(exists = True))
@click.argument("output_file", default = None, required = False)
def assimgpx(input_file, output_file):
    # Start by reading the contents of the input file, as a GPX instance.
    with open(input_file, "r") as f:
        gpx_file_contents = f.read()
        gpx = gpxpy.parse(gpx_file_contents)
    # Now, employ factory to convert to HawkSpeed GPX, and receive that back.
    hsgpx = factory.convert_to_hawkspeed_gpx(gpx)
    if len(hsgpx) > 1:
        """TODO: please implement this, we want to write a new file each with a different name; the name of the track, to output dir."""
        raise NotImplementedError("assimgpx writing multiple tracks not yet implemented.")
    else:
        hsgpx = hsgpx[0]
        # Now, if output file is None, we are going to overwrite input file.
        if not output_file:
            output_file = input_file
        with open(output_file, "w") as w:
            # Convert to XML, pretty print. Then write.
            w.write(hsgpx.to_xml(prettyprint = True))


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


@application.cli.command("exporttrack", help = "Export a track, by its UID, to the specified file.")
@click.argument("track_uid", type = str)
@click.argument("output_file")
@click.argument("format", default = "gpx", required = False)
def export_track(track_uid, output_file, format):
    # Find Track.
    track = tracks.find_existing_track(track_uid = track_uid)
    if not track:
        raise Exception(f"Failed to export track with UID {track_uid}, that track can't be found.")
    # Now, call out to tracks module to actually export the track. Always pass overwrite being who cares.
    """TODO: output file currently ignored, we'll just output to working dir. Change this, though..."""
    tracks.export_track(track, format,
        overwrite = True)


@application.cli.command("snaptoroads", help = "Snap a Track with the given UID to roads via Google API.")
@click.argument("track_uid", type = str)
def snap_to_roads(track_uid):
    # Find Track.
    track = tracks.find_existing_track(track_uid = track_uid)
    if not track:
        raise Exception(f"Failed to snap track with UID {track_uid} to roads, that track can't be found.")
    # Call out to roads api to snap this track.
    result = roadsapi.snap_to_road(track,
        force_live_api = True)
    if result.is_successful:
        LOG.debug(f"Successfully snapped {track} to roads!")
    else:
        LOG.error(f"Failed to snap track {track} to road.")