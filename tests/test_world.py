import os
import time
import json
import base64

from sqlalchemy import func, asc
from datetime import date, datetime, timedelta
from flask import url_for
from tests.conftest import BaseCase

from app import db, config, factory, models, login_manager, world, tracks
from app.socket import handler as sockhandler


class TestWorld(BaseCase):
    def test_dont_trim_player_location_attached_race(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden")
        # Create a track.
        track = tracks.create_track_from_gpx("example1.gpx")
        db.session.flush()
        # Start a new race for this User and this track.
        # Create a new track user race for between this user and track.
        race = models.TrackUserRace(user = aldos, track = track, started = 1678508089000)
        # Set the CRS for this geometry.
        race.set_crs(config.WORLD_CONFIGURATION_CRS)
        db.session.add(race)
        db.session.flush()
        # Create 10 locations, 2 will be connected to the track, 8 will not.
        user_locations = [
            world._prepare_user_location(dict(latitude = -37.843652, longitude = 145.03001, logged_at = 1678508081000, speed = 70.0, rotation = 180.0)),
            world._prepare_user_location(dict(latitude = -37.84354, longitude = 145.029053, logged_at = 1678508082000, speed = 70.0, rotation = 180.0)),
            world._prepare_user_location(dict(latitude = -37.84355, longitude = 145.029053, logged_at = 1678508083000, speed = 70.0, rotation = 180.0)),
            world._prepare_user_location(dict(latitude = -37.84356, longitude = 145.029053, logged_at = 1678508084000, speed = 70.0, rotation = 180.0)),
            world._prepare_user_location(dict(latitude = -37.84357, longitude = 145.029053, logged_at = 1678508085000, speed = 70.0, rotation = 180.0)),
            world._prepare_user_location(dict(latitude = -37.84358, longitude = 145.029053, logged_at = 1678508086000, speed = 70.0, rotation = 180.0)),
            world._prepare_user_location(dict(latitude = -37.84359, longitude = 145.029053, logged_at = 1678508087000, speed = 70.0, rotation = 180.0)),
            world._prepare_user_location(dict(latitude = -37.84360, longitude = 145.029053, logged_at = 1678508088000, speed = 70.0, rotation = 180.0)),
            world._prepare_user_location(dict(latitude = -37.84361, longitude = 145.029053, logged_at = 1678508089000, speed = 70.0, rotation = 180.0)),
            world._prepare_user_location(dict(latitude = -37.84362, longitude = 145.029053, logged_at = 1678508090000, speed = 70.0, rotation = 180.0))
        ]
        # Associate all with the User, so they are all granted a User ID.
        for x in user_locations:
            aldos.add_location(x)
        db.session.flush()
        # Add the last 2 to the track.
        for loc in user_locations[len(user_locations)-2:]:
            race.add_location(loc)
        db.session.flush()
        # Ensure there are 10 user locations currently in the User's history.
        self.assertEqual(aldos.location_history.count(), 10)
        # Ensure there are 2 in the Race's progress.
        self.assertEqual(len(race.progress), 2)
        # Now, trim the Player's location history.
        world._trim_player_location_history(aldos)
        db.session.flush()
        db.session.refresh(race)
        # Now, ensure the Player has 7 updates in total.
        self.assertEqual(aldos.location_history.count(), 7)
        # Ensure there are 2 in the Race's progress.
        self.assertEqual(len(race.progress), 2)

    def test_trim_player_location_history(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden")
        def add_user_locations():
            # Add 6 user locations to the User's history.
            for x in range(6):
                location = world._prepare_user_location(dict(latitude = -37.8490495286849, longitude = 145.00537088213827, logged_at = 1678508080000+x, speed = 70.0, rotation = 180.0))
                # Add it.
                aldos.add_location(location)
            db.session.flush()
        # Add user locations.
        add_user_locations()
        # Ensure we have 6.
        self.assertEqual(aldos.location_history.count(), 6)
        # Call the trim function.
        world._trim_player_location_history(aldos)
        db.session.flush()
        # Ensure aldos now only has 5, and ensure the oldest logged at is ..01
        self.assertEqual(aldos.location_history.count(), 5)
        self.assertEqual(aldos.location_history.order_by(asc(models.UserLocation.logged_at)).first().logged_at, 1678508080001)
        # Now, delete all user locations.
        for x in aldos.location_history.all():
            db.session.delete(x)
        db.session.flush()
        # Add user locations.
        add_user_locations()
        # Get the oldest user location.
        oldest_location = aldos.location_history.order_by(asc(models.UserLocation.logged_at)).first()

    def test_collect_world_objects(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden")
        # Create a new track from example1.
        tracks.create_track_from_gpx("example1.gpx")
        db.session.flush()
        # Create a viewport, containing example1.
        viewport_1_d = dict(
            viewport_minx = 145.00537088213827,
            viewport_miny = -37.8490495286849,
            viewport_maxx = 145.03657889101794,
            viewport_maxy = -37.83703648343093,
        )
        # Create a viewport, not containing example1.
        viewport_2_d = dict(
            viewport_minx = 145.05104656931678,
            viewport_miny = -37.84497529027691,
            viewport_maxx = 145.07307815713955,
            viewport_maxy = -37.8335477251275,
        )
        # Query all tracks in view of the first viewport, ensure the track is present.
        world_view_1 = world.collect_viewed_objects(aldos, viewport_1_d)
        # Ensure there is a single track in this view object.
        self.assertEqual(len(world_view_1.tracks), 1)
        # Query all tracks in view of the second viewport, ensure the track is not present.
        world_view_2 = world.collect_viewed_objects(aldos, viewport_2_d)
        # Ensure there are 0 tracks in this view object.
        self.assertEqual(len(world_view_2.tracks), 0)

    def test_serialise_viewport_response(self):
        """"""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden")
        # Create a new track from example1.
        example1 = tracks.create_track_from_gpx("example1.gpx")
        db.session.flush()
        # Create a new viewport update result, add the track in.
        viewport_update_result = world.ViewportUpdateResult([example1])
        # Now, instantiate a ViewportUpdateResponseSchema, and dump this result through it.
        viewport_update_response_schema = sockhandler.ViewportUpdateResponseSchema()
        viewport_update_d = viewport_update_response_schema.dump(viewport_update_result)
