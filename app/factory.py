"""A module for creating randomised data or generating model instances from predefined data."""
import logging
import os
import random
import gpxpy
import xml.etree.ElementTree as ET

from typing import List
from datetime import datetime, date
from app import db, config, models, users, vehicles, error

LOG = logging.getLogger("hawkspeed.factory")
LOG.setLevel( logging.DEBUG )

test_first_names = None
test_last_names = None

# Import test names as long as environment is not production.
if config.APP_ENV != "Production":
    """Import all test names."""
    LOG.debug(f"Importing test names...")
    # Read both sets of names; first and last.
    with open(os.path.join(os.getcwd(), config.IMPORTS_PATH, "test_first_names.txt"), "r", encoding = "utf-8") as f:
        first_names = f.read()
    with open(os.path.join(os.getcwd(), config.IMPORTS_PATH, "test_last_names.txt"), "r", encoding = "utf-8") as f:
        last_names = f.read()
    # Split by newline and set into this object.
    test_first_names = list(filter(lambda x: x != "", first_names.split("\n")))
    test_last_names = list(filter(lambda x: x != "", last_names.split("\n")))


def get_random_identity():
    """Determine a random identity. This will return a tuple containing (in this order); first name, last name, date of birth (as date), email address, phone number. All randomised."""
    # Raise an exception if environment production.
    if config.APP_ENV == "Production":
        raise Exception("Failed to get random identity - this function is not enabled in Production!")
    # Determine random first and last name.
    first_name = test_first_names[random.randint(0, len(test_first_names)-1)].title()
    last_name = test_last_names[random.randint(0, len(test_last_names)-1)].title()
    # Now construct a date of birth.
    date_of_birth = date(random.randint(1950, 2023), random.randint(1, 12), random.randint(1, 27))
    # Now construct an email address.
    email_address = f"{first_name.lower()}.{last_name.lower()}@noemail.com"
    # Finally, a phone number.
    phone_number = f"614{random.randint(11, 99)}{random.randint(111, 999)}{random.randint(111, 999)}"
    # Return this tuple.
    return (first_name, last_name, date_of_birth, email_address, phone_number)


def get_random_user(**kwargs) -> models.User:
    """Use a random identity to create and persist a User model.
    Keyword arguments
    -----------------
    :verified: Whether this User is verified or not. Default is True.
    :setup: Whether we shoudl setup the User's profile. Default is True.
    :vehicle: The Vehicle to add to the new User. By default 1994 Toyota Supra will be used."""
    verified = kwargs.get("verified", True)
    setup = kwargs.get("setup", True)
    vehicle = kwargs.get("vehicle", "1994 Toyota Supra")

    # Get that identity.
    (fn, ln, dob, em, ph) = get_random_identity()
    # Now, make the User.
    LOG.debug(f"Making new random user with name {fn} {ln}...")
    new_user = models.User(
        email_address = em,
        username = f"{fn} {ln}",
        verified = verified,
        profile_setup = setup)
    # Create the first vehicle for the User.
    vehicles.create_vehicle(vehicles.RequestCreateVehicle(text = vehicle),
        user = new_user)
    # Set a bad password.
    new_user.set_password("password")
    # Add to database then return.
    db.session.add(new_user)
    LOG.debug(f"Created random User: {new_user}")
    return new_user


def create_user(email_address, password, **kwargs) -> models.User:
    """Create a new user, that is optionally setup. Email address must be unique.

    Keyword arguments
    -----------------
    :privilege: The user's privilege. Default is 0 (user.)
    :enabled: True or False whether user should be enabled. Default is True.
    :verified: True or False whether user has verified their information/account. Default is True.
    :username: Provide to set the username & set profile as setup.
    :vehicle: Provide to create the first Vehicle for this User. This will be in textual form like '1994 Toyota Supra'

    Raises
    ------
    OperationalFail
    :account-already-exists: An account has already been created with this email or phone number.

    Returns
    -------
    The new User."""
    privilege = kwargs.get("privilege", 0)
    enabled = kwargs.get("enabled", True)
    verified = kwargs.get("verified", True)
    username = kwargs.get("username", None)
    vehicle = kwargs.get("vehicle", None)

    LOG.debug(f"Adding new User: {email_address}")
    if models.User.search(email_address = email_address):
        LOG.warning(f"User with email address {email_address} already exists, skipping creating user...")
        raise error.OperationalFail("account-already-exists")
    new_user = models.User(
        email_address = email_address)
    new_user.set_password(password)
    new_user.set_privilege(privilege)
    new_user.set_enabled(enabled)
    new_user.set_verified(verified)
    if username:
        LOG.debug(f"Set username for new user {email_address}! They are therefore setup.")
        new_user.set_username(username)
        new_user.set_profile_setup(True)
    else:
        LOG.debug(f"Did not set username for new user {email_address}, they are not setup.")
    if vehicle:
        vehicles.create_vehicle(vehicles.RequestCreateVehicle(text = vehicle),
            user = new_user)
    LOG.debug(f"New account created; {new_user}")
    db.session.add(new_user)
    return new_user


def make_gpx_from(creator, track_name, track_description, track_segments, **kwargs) -> gpxpy.gpx.GPX:
    """"""
    try:
        is_snapped = kwargs.get("is_snapped", False)
        is_verified = kwargs.get("is_verified", False)
        track_type = kwargs.get("track_type", -1)
        
        # Create a new GPX, with hawkspeed as the creator.
        hawkspeed_gpx = gpxpy.gpx.GPX()
        hawkspeed_gpx.creator = creator
        hawkspeed_gpx.version = "1.1"
        # Now, create a new track, and add it to our GPX.
        hsgpx_track = gpxpy.gpx.GPXTrack()
        hawkspeed_gpx.tracks.append(hsgpx_track)
        # Set information about the track.
        hsgpx_track.name = track_name
        hsgpx_track.description = track_description
        # Now, create a new element for each extension.
        type_ext_elem = ET.Element("type")
        type_ext_elem.text = str(track_type)
        snapped_ext_elem = ET.Element("snapped")
        snapped_ext_elem.text = str(int(is_snapped))
        verified_ext_elem = ET.Element("verified")
        verified_ext_elem.text = str(int(is_verified))
        # Append all to the HS Track.
        hsgpx_track.extensions.append(type_ext_elem)
        hsgpx_track.extensions.append(snapped_ext_elem)
        hsgpx_track.extensions.append(verified_ext_elem)
        # Now, only a single segment is allowed (for now), so fail if there's more than 1.
        if len(track_segments) > 1:
            """TODO: support multiple segs."""
            raise NotImplementedError("failed to convert from GPX studio GPX format; we only support ONE track segment for now.")
        # Get that single seg.
        track_segment_ = track_segments[0]
        # Create a segment for our new track, too. Append it to our HS Track.
        hsgpx_segment = gpxpy.gpx.GPXTrackSegment()
        hsgpx_track.segments.append(hsgpx_segment)
        # Now, iterate all track points, creating a new point from each and add them all to the segment.
        for track_point_ in track_segment_.points:
            hsgpx_trackpoint = gpxpy.gpx.GPXTrackPoint(track_point_.latitude, track_point_.longitude)
            hsgpx_segment.points.append(hsgpx_trackpoint)
            """TODO: we can add further detail about the point here."""
        return hawkspeed_gpx
    except Exception as e:
        raise e
    

def convert_to_hawkspeed_gpx(gpx, **kwargs) -> List[gpxpy.gpx.GPX]:
    """Convert from a GPX authored by a third party entity, to a GPX that is understood exclusively by HawkSpeed."""
    try:
        hsgpx = []
        if gpx.creator == "https://gpx.studio":
            # Iterate all tracks in given GPX.
            for track_ in gpx.tracks:
                # Never snapped if it comes from GPX studio.
                is_snapped = False
                # Always verified if it comes from GPX studio, as we created it.
                is_verified = True
                # Track type from GPX studio is always -1 (determine.)
                track_type = -1
                
                if not track_.name:
                    raise ValueError("Failed to convert a GPX to HawkSpeed format! A name is not provided, but one is required!")
                if not track_.description:
                    raise ValueError("Failed to convert a GPX to HawkSpeed format! A description is not provided, but one is required!")
                # Create a new hawkspeed GPX from all the given information.
                hawkspeed_gpx = make_gpx_from("hawkspeed", track_.name, track_.description, track_.segments,
                    is_snapped = is_snapped, is_verified = is_verified, track_type = track_type)
                # Append the hawkspeed GPX to our hsgpx list.
                hsgpx.append(hawkspeed_gpx)
        else:
            raise NotImplementedError(f"Failed to convert GPX with creator {gpx.creator} to HawkSpeed compat. Unrecognised creator.")
        return hsgpx
    except Exception as e:
        raise e
